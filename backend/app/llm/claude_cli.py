"""Claude Code CLI client — primary LLM channel.

Uses subprocess `claude -p` with `--output-format json` (single-shot result).
Authentication via the user's Claude Max subscription (CLI manages it).

This module is for **simple text completions** (PRD → cases, failure → diagnosis).
For execution orchestration that needs MCP tools, use `app/agent/claude_runner.py`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from app.config import settings
from app.llm.base import (
    BaseChatClient,
    LLMAuthError,
    LLMResponseFormatError,
    LLMResult,
    LLMTimeoutError,
    RateLimitError,
)
from app.obs import EVENTS, get_logger

_log = get_logger(__name__)

_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate-limited",
    "rate limited",
    "rate_limit",
    "throttl",
    "429",
    "5h limit reached",
    "usage limit",
)
_AUTH_PATTERNS = (
    "not logged in",
    "authentication",
    "auth required",
    "no api key",
    "401",
    "403",
)


def _looks_like_rate_limit(blob: str) -> bool:
    blob_l = blob.lower()
    return any(p in blob_l for p in _RATE_LIMIT_PATTERNS)


def _looks_like_auth_error(blob: str) -> bool:
    blob_l = blob.lower()
    return any(p in blob_l for p in _AUTH_PATTERNS)


class ClaudeCLIClient(BaseChatClient):
    name = "claude-cli"

    def __init__(
        self,
        *,
        binary: str | None = None,
        default_timeout: int | None = None,
    ):
        self.binary = binary or settings.claude_cli_path or "claude"
        self.default_timeout = default_timeout or settings.claude_timeout_seconds

    async def chat(
        self,
        prompt: str,
        *,
        prompt_version: str,
        system: str | None = None,
        image: bytes | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        timeout_seconds: int | None = None,
    ) -> LLMResult:
        log = _log.bind(provider=self.name, prompt_version=prompt_version)

        if image:
            # `claude -p` does not reliably support image attachments — the
            # `--file image1:<path>` flag requires `CLAUDE_CODE_SESSION_ACCESS_TOKEN`
            # and a runtime that's not the subscription CLI. The diagnoser
            # routes vision calls through Flywheel/MiniMax for this reason.
            raise LLMResponseFormatError(
                "claude CLI does not support image input in -p mode; "
                "route this call to a vision-capable provider",
                provider=self.name,
            )

        cmd: list[str] = [
            self.binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            # NOTE: do NOT pass --bare — it disables OAuth/keychain reads, which
            # breaks subscription auth (Claude Max). For LLM gateway calls we
            # want subscription, not API key.
        ]
        if system:
            cmd += ["--append-system-prompt", system]
        if max_tokens:
            # claude --print does not have a direct max-tokens flag in current versions;
            # we rely on the model defaults + prompt-side limits. Surface as metadata only.
            pass

        timeout = timeout_seconds or self.default_timeout
        log.info("llm.completion.start", model_hint="claude-opus", timeout=timeout)

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LLMAuthError(
                f"claude CLI not found at {self.binary!r}",
                provider=self.name,
            ) from exc
        except OSError as exc:
            # Argument list too long, permission denied, etc.
            raise LLMResponseFormatError(
                f"claude CLI failed to start: {exc}",
                provider=self.name,
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            latency = int((time.monotonic() - t0) * 1000)
            log.error("llm.completion.timeout", latency_ms=latency)
            raise LLMTimeoutError(
                f"claude CLI timed out after {timeout}s",
                provider=self.name,
            ) from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        latency = int((time.monotonic() - t0) * 1000)

        if proc.returncode != 0:
            blob = stderr or stdout
            if _looks_like_auth_error(blob):
                log.error("llm.completion.auth_error", stderr=blob[:300])
                raise LLMAuthError(
                    f"claude CLI auth failure: {blob[:200]}",
                    provider=self.name,
                )
            if _looks_like_rate_limit(blob):
                log.warning("llm.completion.rate_limited", stderr=blob[:300])
                raise RateLimitError(
                    f"claude CLI rate limited: {blob[:200]}",
                    provider=self.name,
                )
            log.error(
                "llm.completion.failed",
                exit_code=proc.returncode,
                stderr=blob[:500],
                latency_ms=latency,
            )
            raise LLMResponseFormatError(
                f"claude CLI exit={proc.returncode}: {blob[:200]}",
                provider=self.name,
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            log.error("llm.completion.unparseable", stdout=stdout[:300])
            raise LLMResponseFormatError(
                f"claude CLI returned non-JSON: {stdout[:200]}",
                provider=self.name,
            ) from exc

        usage = data.get("usage", {}) or {}
        mu = data.get("modelUsage", {}) or {}
        # pick the heaviest model usage as the "model" label
        model = ""
        if mu:
            model = max(
                mu.items(),
                key=lambda kv: kv[1].get("inputTokens", 0) + kv[1].get("outputTokens", 0),
            )[0]

        text = data.get("result", "") or ""
        if json_mode:
            text = _strip_to_json(text)

        result = LLMResult(
            text=text,
            model=model,
            provider=self.name,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            latency_ms=latency,
            cost_usd=data.get("total_cost_usd"),
            metadata={
                "session_id": data.get("session_id"),
                "num_turns": data.get("num_turns"),
                "stop_reason": data.get("stop_reason"),
                "model_usage": mu,
            },
        )
        log.info(
            EVENTS.LLM_COMPLETION.name,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            latency_ms=latency,
            cost_usd=result.cost_usd,
        )
        return result


def _strip_to_json(text: str) -> str:
    """Best-effort: peel ```json fences or surrounding prose to leave a JSON value.

    We don't validate the JSON here — caller may want partial. Just trim wrappers.
    """
    s = text.strip()
    if s.startswith("```"):
        # ```json or just ```
        s = re.sub(r"^```(json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()
