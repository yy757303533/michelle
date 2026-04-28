"""Flywheel client — premium upgrade channel.

ZStack Flywheel network gateway, OpenAI-compatible at
`https://flywheel.zstack.io/v1/chat/completions`. Models available include
`anthropic/claude-opus-4.7`, `openai/gpt-5.4-pro`, `openai/gpt-5.4`, plus a
catalog of others (`openai/gpt-4o`, `deepseek/deepseek-v3.2`, ...).

As of 2026-04-27 the user's Flywheel token is **quota-exhausted** (HTTP 402
across all models). Code is in place so the moment quota resets, callers
get free access to top-tier reasoning models with no other change.

Use cases (when quota recovers):
  - Failure diagnosis on the hardest cases (Opus 4.7 reasoning)
  - Prompt iteration A/B (compare Claude vs GPT-5.4 on the same case)
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.llm.base import (
    BaseChatClient,
    LLMAuthError,
    LLMResponseFormatError,
    LLMResult,
    LLMTimeoutError,
    QuotaExceededError,
    RateLimitError,
)
from app.obs import EVENTS, get_logger

_log = get_logger(__name__)


class FlywheelClient(BaseChatClient):
    name = "flywheel"

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        default_max_tokens: int = 2000,
    ):
        self.token = token if token is not None else settings.flywheel_token
        self.base_url = base_url or settings.flywheel_base_url
        self.model = model or settings.flywheel_model_premium
        self.default_max_tokens = default_max_tokens
        self.enabled = bool(self.token)

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
        if not self.token:
            raise LLMAuthError("Flywheel token is empty (set FLYWHEEL_TOKEN)", provider=self.name)

        log = _log.bind(provider=self.name, prompt_version=prompt_version, model=self.model)

        # Flywheel forwards two different request shapes depending on the
        # model name we send:
        #
        #   namespaced (`anthropic/...`, `openai/...`)
        #     → upstream is OpenAI-compatible; images are
        #       {type: "image_url", image_url: {url: "data:...;base64,..."}}
        #
        #   bare (`claude-opus-4-7`, `gpt-5.5`, ...)
        #     → upstream is the model's *native* API; for Anthropic models
        #       images must be {type: "image", source: {type: "base64",
        #       media_type: "image/png", data: "..."}}.
        #
        # Detection rule: a "/" in the model name means namespaced/OpenAI-compat;
        # bare names route to native upstreams. Currently the bare names
        # Flywheel exposes are all Claude variants, but we don't want a future
        # bare `gpt-5.5` registration to silently use Anthropic-native shapes,
        # so we constrain "Anthropic-native" to model names that look like
        # Claude. Anything else falls back to OpenAI-compat.
        is_namespaced = "/" in self.model
        is_anthropic_native = (not is_namespaced) and _looks_like_claude(self.model)
        # `is_namespaced` controls request shape (system message vs system
        # field, image_url vs image source). For non-Claude bare names we
        # treat the route as OpenAI-compatible.
        is_namespaced = is_namespaced or not is_anthropic_native
        messages: list[dict[str, Any]] = []
        # System prompt is added as a `system: ...` top-level field for
        # Anthropic-native (bare model names) and as a role=system message for
        # OpenAI-compatible (namespaced names). Flywheel proxies generally
        # tolerate either, but the native Anthropic backend is stricter.
        if system and is_namespaced:
            messages.append({"role": "system", "content": system})
        if image:
            import base64

            b64 = base64.b64encode(image).decode("ascii")
            mime = _detect_image_mime(image)
            if is_namespaced:
                content_blocks: list[dict[str, Any]] = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ]
            else:
                # Anthropic-native shape
                content_blocks = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": b64,
                        },
                    },
                ]
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if system and not is_namespaced:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode and is_namespaced:
            # `response_format: {type: "json_object"}` is OpenAI-only.
            # Native Anthropic 400s on it. We rely on the prompt to ask
            # for strict JSON in either case.
            body["response_format"] = {"type": "json_object"}

        timeout = timeout_seconds or 120
        t0 = time.monotonic()

        try:
            # trust_env=False — see MiniMaxClient for rationale
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(
                    self.base_url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"Flywheel timed out after {timeout}s", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMResponseFormatError(
                f"Flywheel network error: {exc}", provider=self.name
            ) from exc

        latency = int((time.monotonic() - t0) * 1000)

        # Best-effort JSON parse: a 402/429 from the proxy can come back as an
        # HTML gateway page. We must still classify those as quota / rate-limit
        # so the gateway falls through to the next provider — never raise
        # LLMResponseFormatError on a status code that's already meaningful.
        try:
            data = resp.json()
        except ValueError:
            data = {}
        body_text = resp.text[:500]

        if resp.status_code == 401:
            raise LLMAuthError(f"Flywheel auth failure: {body_text[:200]}", provider=self.name)
        if resp.status_code == 402 or (isinstance(data, dict) and _is_quota_error(data)):
            # `error` envelope is sometimes a string or list, not a dict —
            # `_extract_error_message` handles all of those without crashing.
            err_msg = _extract_error_message(data, body_text)
            log.warning("llm.completion.quota_exceeded", error=err_msg[:300])
            raise QuotaExceededError(
                f"Flywheel quota exceeded: {err_msg[:200]}", provider=self.name
            )
        if resp.status_code == 429:
            raise RateLimitError(f"Flywheel rate limited: {body_text[:200]}", provider=self.name)
        if resp.status_code >= 400:
            raise LLMResponseFormatError(
                f"Flywheel error: http={resp.status_code} body={body_text[:200]}",
                provider=self.name,
            )

        if not isinstance(data, dict):
            raise LLMResponseFormatError(
                f"Flywheel returned non-object JSON: {body_text[:200]}",
                provider=self.name,
            )

        # Flywheel forwards to whichever upstream backs the requested model.
        # When the model is namespaced like `anthropic/...` it returns
        # OpenAI-compatible {choices:[{message:{content:...}}]}.
        # When the model is bare like `claude-opus-4-7` it returns the native
        # Anthropic shape {content:[{type:"text", text:...}], stop_reason}.
        # We handle both.
        text = ""
        finish_reason: str | None = None
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0

        if "choices" in data and data["choices"]:
            # OpenAI-compatible shape
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            text = msg.get("content") or ""
            # Some upstreams (reasoning models like gpt-5.5) put output into
            # reasoning tokens with empty content — surface that in metadata.
            finish_reason = choice.get("finish_reason")
        elif "content" in data and isinstance(data["content"], list):
            # Anthropic-native shape: {content:[{type:"text", text:"..."}]}
            blocks = data["content"]
            text = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
            finish_reason = data.get("stop_reason")
        else:
            raise LLMResponseFormatError(
                "Flywheel returned unrecognised response shape "
                "(no `choices` and no `content` array)",
                provider=self.name,
                detail=data,
            )

        result = LLMResult(
            text=text,
            model=data.get("model") or self.model,
            provider=self.name,
            input_tokens=int(prompt_tokens or 0),
            output_tokens=int(completion_tokens or 0),
            latency_ms=latency,
            metadata={
                "id": data.get("id"),
                "finish_reason": finish_reason,
            },
        )
        log.info(
            EVENTS.LLM_COMPLETION.name,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency,
        )
        return result


def _is_quota_error(data: dict[str, Any]) -> bool:
    """Match Flywheel's error envelope variants. Flywheel itself emits
    `quote_exceeded` (typo upstream) AND newer providers emit
    `quota_exceeded` / `insufficient_quota`. The bare `"quota"` substring is
    intentionally absent — it matches benign messages like 'quota parameter
    is not supported'; status-code 402 already covers the catch-all."""
    err = data.get("error") or {}
    if not isinstance(err, dict):
        return False
    typ = str(err.get("type") or "").lower()
    code = str(err.get("code") or "").lower()
    msg = str(err.get("message") or "").lower()
    blob = f"{typ} {code} {msg}"
    return any(
        k in blob
        for k in (
            "quote_exceeded",
            "quota_exceeded",
            "insufficient_quota",
            "subscription quota",
            "billing",
            "payment required",
        )
    )


def _extract_error_message(data: Any, fallback: str = "") -> str:
    """Pull a human-readable error string out of any error envelope shape:
    `{"error": {"message": ...}}`, `{"error": "..."}`, `{"error": [...]}`,
    or fall back to the raw response body. Never raises."""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err.get("type") or fallback)
        if isinstance(err, list):
            return ", ".join(str(x) for x in err)[:300] or fallback
        if err:
            return str(err)
        msg = data.get("message")
        if msg:
            return str(msg)
    return fallback


_CLAUDE_KEYWORDS = ("claude", "opus", "sonnet", "haiku")


def _looks_like_claude(model: str) -> bool:
    """Heuristic: does this bare model name name a Claude variant?
    Used to decide whether to send the request in Anthropic-native shape."""
    m = model.lower()
    return any(kw in m for kw in _CLAUDE_KEYWORDS)


def _detect_image_mime(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"
