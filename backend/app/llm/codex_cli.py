"""Codex CLI client — secondary subprocess channel.

Mirrors ClaudeCLIClient. Codex CLI is OpenAI's agent CLI that ships with the
ChatGPT desktop / `codex` brew. When the user has a ChatGPT subscription,
`codex exec '<prompt>'` returns the model's response without an API key.

Notes:
  - Codex's stdout format is plain text by default (no `--output-format json`
    flag in current versions). We capture stdout and treat it as the assistant
    text; token usage is unreported.
  - Codex CLI is opt-in: only enabled when the binary is present on PATH.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

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

_RATE_PATTERNS = ("rate limit", "throttl", "429", "too many request", "usage limit")
_AUTH_PATTERNS = ("not logged in", "auth required", "no api key", "401", "403", "/login")


def _looks_like_rate_limit(blob: str) -> bool:
    blob_l = blob.lower()
    return any(p in blob_l for p in _RATE_PATTERNS)


def _looks_like_auth_error(blob: str) -> bool:
    blob_l = blob.lower()
    return any(p in blob_l for p in _AUTH_PATTERNS)


class CodexCLIClient(BaseChatClient):
    name = "codex-cli"

    def __init__(
        self,
        *,
        binary: str = "codex",
        default_timeout: int = 180,
    ):
        self.binary = binary
        self.default_timeout = default_timeout
        self.enabled = shutil.which(binary) is not None

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
        if not self.enabled:
            raise LLMAuthError(
                "codex CLI not on PATH (install via `brew install codex` or similar)",
                provider=self.name,
            )
        if image:
            raise LLMResponseFormatError(
                "codex CLI client does not currently relay images; route image input to a multimodal provider (minimax / gemini)",
                provider=self.name,
            )

        log = _log.bind(provider=self.name, prompt_version=prompt_version)

        # Codex CLI prompt format: `codex exec "<prompt>"` (non-interactive).
        # If a system prompt is supplied, we prepend it as a separate paragraph.
        full = f"{system}\n\n{prompt}" if system else prompt
        cmd: list[str] = [self.binary, "exec", full]

        timeout = timeout_seconds or self.default_timeout
        log.info("llm.completion.start", model_hint="codex-default", timeout=timeout)

        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise LLMTimeoutError(
                f"codex CLI timed out after {timeout}s", provider=self.name
            ) from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        latency = int((time.monotonic() - t0) * 1000)

        if proc.returncode != 0:
            blob = stderr or stdout
            if _looks_like_auth_error(blob):
                raise LLMAuthError(f"codex CLI auth: {blob[:200]}", provider=self.name)
            if _looks_like_rate_limit(blob):
                raise RateLimitError(f"codex CLI rate limited: {blob[:200]}", provider=self.name)
            raise LLMResponseFormatError(
                f"codex CLI exit={proc.returncode}: {blob[:200]}",
                provider=self.name,
            )

        text = stdout.strip()
        result = LLMResult(
            text=text,
            model="codex-default",
            provider=self.name,
            latency_ms=latency,
            metadata={"stderr_tail": stderr[-200:] if stderr else ""},
        )
        log.info(
            EVENTS.LLM_COMPLETION.name,
            model=result.model,
            latency_ms=latency,
            output_chars=len(text),
        )
        return result
