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
            raise LLMAuthError(
                "Flywheel token is empty (set FLYWHEEL_TOKEN)", provider=self.name
            )

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
        # otherwise we send Anthropic-native image blocks (the bare names we've
        # observed in the catalog are all Claude variants).
        is_namespaced = "/" in self.model
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if image:
            import base64

            b64 = base64.b64encode(image).decode("ascii")
            if is_namespaced:
                content_blocks: list[dict[str, Any]] = [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
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
                            "media_type": "image/png",
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

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseFormatError(
                f"Flywheel returned non-JSON: {resp.text[:200]}",
                provider=self.name,
            ) from exc

        # Flywheel error envelope is OpenAI-compatible: {"error":{"message":...,"type":...,"code":...}}
        if resp.status_code == 401:
            raise LLMAuthError(
                f"Flywheel auth failure: {resp.text[:200]}", provider=self.name
            )
        if resp.status_code == 402 or _is_quote_error(data):
            err = (data.get("error") or {}).get("message", resp.text)
            log.warning("llm.completion.quota_exceeded", error=err[:300])
            raise QuotaExceededError(
                f"Flywheel quota exceeded: {err[:200]}", provider=self.name
            )
        if resp.status_code == 429:
            raise RateLimitError(
                f"Flywheel rate limited: {resp.text[:200]}", provider=self.name
            )
        if resp.status_code >= 400:
            raise LLMResponseFormatError(
                f"Flywheel error: http={resp.status_code} body={resp.text[:200]}",
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
        prompt_tokens = (
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        completion_tokens = (
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )

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


def _is_quote_error(data: dict[str, Any]) -> bool:
    err = data.get("error") or {}
    typ = (err.get("type") or "").lower()
    msg = (err.get("message") or "").lower()
    return "quote_exceeded" in typ or "quota" in msg or "subscription quota" in msg
