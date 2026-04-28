"""Generic OpenAI-compatible HTTP client.

Most modern Chinese providers (Kimi/Moonshot, Qwen/DashScope, DeepSeek, GLM,
Gemini's `/v1beta/openai`, Doubao, ZhipuAI) plus all proxy gateways
(Flywheel, OneAPI, NewAPI, ChatGPT-Next-Web, OpenRouter, …) speak the OpenAI
chat-completions wire format. One class handles all of them — only the
endpoint URL, API key, default model, and whether-to-send-images differ.

Usage:

    client = OpenAICompatibleClient(
        name="kimi",
        api_key=settings.kimi_api_key,
        base_url="https://api.moonshot.cn/v1/chat/completions",
        default_model="kimi-k2-0905-preview",
    )
    result = await client.chat("hello", prompt_version="probe_v1")

The gateway picks up any enabled providers automatically — see `gateway.py`.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

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

_QUOTA_WORDS = ("quota", "subscription quota", "exceed", "insufficient_user_quota")
_RATE_WORDS = ("rate", "throttle", "too many requests")


class OpenAICompatibleClient(BaseChatClient):
    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        default_model: str,
        default_max_tokens: int = 2000,
        supports_image: bool = False,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.default_max_tokens = default_max_tokens
        self.supports_image = supports_image
        self.extra_headers = extra_headers or {}
        self.enabled = bool(api_key)

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
            raise LLMAuthError(f"{self.name}: api key empty", provider=self.name)

        log = _log.bind(provider=self.name, prompt_version=prompt_version, model=self.default_model)

        # ── messages ──
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})

        if image and not self.supports_image:
            # Refuse rather than silently strip — caller (typically the
            # diagnoser) requested vision and the result would be misleading
            # if we ran the call as text-only.
            raise LLMResponseFormatError(
                f"{self.name} does not support image input; route this call to a vision provider",
                provider=self.name,
            )

        if image and self.supports_image:
            import base64

            b64 = base64.b64encode(image).decode("ascii")
            mime = _detect_image_mime(image)
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        timeout = timeout_seconds or 90
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(self.base_url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"{self.name} timed out after {timeout}s", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMResponseFormatError(
                f"{self.name} network error: {exc}", provider=self.name
            ) from exc

        latency = int((time.monotonic() - t0) * 1000)

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseFormatError(
                f"{self.name} returned non-JSON: {resp.text[:200]}",
                provider=self.name,
            ) from exc

        # OpenAI-compat error envelope: {"error": {"message", "type", "code"}}
        if resp.status_code == 401:
            raise LLMAuthError(
                f"{self.name} auth: {_err_msg(data) or resp.text[:200]}",
                provider=self.name,
            )
        if resp.status_code == 402 or _is_quota_error(data):
            log.warning("llm.completion.quota_exceeded", error=_err_msg(data)[:300])
            raise QuotaExceededError(
                f"{self.name} quota: {_err_msg(data)[:200]}", provider=self.name
            )
        if resp.status_code == 429 or _is_rate_error(data):
            raise RateLimitError(
                f"{self.name} rate limit: {_err_msg(data)[:200]}", provider=self.name
            )
        if resp.status_code >= 400:
            raise LLMResponseFormatError(
                f"{self.name} error: http={resp.status_code} body={resp.text[:200]}",
                provider=self.name,
            )

        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseFormatError(
                f"{self.name} returned no choices",
                provider=self.name,
                detail=data,
            )
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        # Some providers (DeepSeek-R1, MiniMax-M2.7, Gemini thinking) attach reasoning
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""

        usage = data.get("usage") or {}
        result = LLMResult(
            text=text,
            model=data.get("model") or self.default_model,
            provider=self.name,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency,
            metadata={
                "id": data.get("id"),
                "finish_reason": choices[0].get("finish_reason"),
                "reasoning_content": reasoning,
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _err_msg(data: dict[str, Any]) -> str:
    err = data.get("error") or {}
    return err.get("message", "") if isinstance(err, dict) else str(err)


def _is_quota_error(data: dict[str, Any]) -> bool:
    err = data.get("error") or {}
    if not isinstance(err, dict):
        return False
    # `code` can be int (HTTP status) or str — coerce defensively.
    typ = str(err.get("type") or "").lower()
    msg = str(err.get("message") or "").lower()
    code = str(err.get("code") or "").lower()
    return any(w in typ or w in msg or w in code for w in _QUOTA_WORDS)


def _is_rate_error(data: dict[str, Any]) -> bool:
    err = data.get("error") or {}
    if not isinstance(err, dict):
        return False
    typ = str(err.get("type") or "").lower()
    msg = str(err.get("message") or "").lower()
    return any(w in typ or w in msg for w in _RATE_WORDS)


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
