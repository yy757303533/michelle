"""MiniMax client — fallback channel.

Uses `https://api.minimax.chat/v1/text/chatcompletion_v2` (Day 0 verified
working with the user's key. NOT `.io` — that endpoint refuses the same key).

Two configured models (selected per-call via `model` arg or default in settings):

  - `MiniMax-Text-01`     fast, supports images, cheap. Default.
  - `MiniMax-M2.7`        reasoning model — slower but deeper analysis.
                          Use for diagnosis when subscription Claude isn't
                          available.
"""

from __future__ import annotations

import base64
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


class MiniMaxClient(BaseChatClient):
    name = "minimax"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        default_max_tokens: int = 2000,
    ):
        self.api_key = api_key if api_key is not None else settings.minimax_api_key
        self.base_url = base_url or settings.minimax_base_url
        self.model = model or settings.minimax_model_text
        self.default_max_tokens = default_max_tokens
        self.enabled = bool(self.api_key)

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
        if not self.api_key:
            raise LLMAuthError(
                "MiniMax API key is empty (set MINIMAX_API_KEY)",
                provider=self.name,
            )

        log = _log.bind(provider=self.name, prompt_version=prompt_version, model=self.model)

        # ── Build messages ──
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image:
            b64 = base64.b64encode(image).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content if image else prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature

        timeout = timeout_seconds or 60
        t0 = time.monotonic()

        log.info("llm.completion.start", timeout=timeout)
        try:
            # trust_env=False so we don't accidentally route through a SOCKS/HTTP
            # proxy from env. MiniMax is a public API; in corporate environments
            # that need a proxy, configure httpx explicitly.
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(
                    self.base_url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            latency = int((time.monotonic() - t0) * 1000)
            log.error("llm.completion.timeout", latency_ms=latency)
            raise LLMTimeoutError(
                f"MiniMax timed out after {timeout}s", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - t0) * 1000)
            log.error("llm.completion.network_error", error=str(exc)[:300], latency_ms=latency)
            raise LLMResponseFormatError(
                f"MiniMax network error: {exc}", provider=self.name
            ) from exc

        latency = int((time.monotonic() - t0) * 1000)

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseFormatError(
                f"MiniMax returned non-JSON: {resp.text[:200]}",
                provider=self.name,
            ) from exc

        # MiniMax surfaces errors via base_resp.status_code != 0 (HTTP 200 with
        # error body) AND/OR via HTTP non-2xx with a different shape.
        base_resp = data.get("base_resp") or {}
        bs = base_resp.get("status_code", 0) or 0
        bm = base_resp.get("status_msg", "")

        if resp.status_code == 401 or bs == 2049:
            raise LLMAuthError(f"MiniMax auth: {bm or resp.text[:200]}", provider=self.name)
        if resp.status_code == 429 or "rate" in bm.lower() or "limit" in bm.lower():
            log.warning("llm.completion.rate_limited", base_msg=bm)
            raise RateLimitError(f"MiniMax rate limited: {bm}", provider=self.name)
        if resp.status_code in (402, 403) or "quota" in bm.lower():
            log.warning("llm.completion.quota_exceeded", base_msg=bm)
            raise QuotaExceededError(f"MiniMax quota: {bm}", provider=self.name)
        if resp.status_code >= 400 or bs != 0:
            log.error(
                "llm.completion.failed",
                http_status=resp.status_code,
                base_status=bs,
                base_msg=bm,
            )
            raise LLMResponseFormatError(
                f"MiniMax error: http={resp.status_code} base={bs} msg={bm}",
                provider=self.name,
            )

        # ── Extract content ──
        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseFormatError(
                "MiniMax returned no choices", provider=self.name, detail=data
            )

        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        # Reasoning models also return reasoning_content; we expose via metadata
        reasoning = msg.get("reasoning_content") or ""

        usage = data.get("usage") or {}

        result = LLMResult(
            text=text,
            model=data.get("model") or self.model,
            provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
            latency_ms=latency,
            metadata={
                "id": data.get("id"),
                "finish_reason": choices[0].get("finish_reason"),
                "reasoning_content": reasoning,
                "input_sensitive": data.get("input_sensitive"),
                "output_sensitive": data.get("output_sensitive"),
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
