"""LLM Gateway — base abstractions.

Three layers:

  ┌────────────────────────────────────────────────────┐
  │ Application code (services/, agent/, mcp/)         │
  │   gateway = get_gateway(); await gateway.chat(...) │
  └─────────────────┬──────────────────────────────────┘
                    │
  ┌─────────────────▼──────────────────────────────────┐
  │ LLMGateway: routes one logical chat() call to a    │
  │ chain of clients, fall through on Rate/Quota/Timeout│
  └─────────────────┬──────────────────────────────────┘
                    │ each client implements:
                    │   async def chat(prompt, **) -> LLMResult
                    ▼
  ┌────────────────────────────────────────────────────┐
  │ ClaudeCLIClient | MiniMaxClient | FlywheelClient   │
  └────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class LLMResult(BaseModel):
    """A single chat completion outcome."""

    text: str = ""
    """Final assistant text. Empty string if model returned no content."""

    model: str = ""
    """Model identifier as reported by the provider (e.g. 'claude-opus-4-7', 'MiniMax-Text-01')."""

    provider: str = ""
    """Logical provider name (e.g. 'claude-cli', 'minimax', 'flywheel')."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    latency_ms: int = 0
    """Wall-clock latency from chat() entry to result return."""

    cost_usd: float | None = None
    """Provider-reported cost (Claude CLI returns this; HTTP providers usually don't)."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Provider-specific extras: session_id, finish_reason, raw_response, ..."""


# ──────────────────────── Errors ────────────────────────


class LLMError(Exception):
    """Base class for all LLM gateway errors."""

    def __init__(self, message: str, *, provider: str = "", detail: Any = None):
        super().__init__(message)
        self.provider = provider
        self.detail = detail


class FallbackableLLMError(LLMError):
    """Errors that the gateway should swallow + try the next provider."""


class RateLimitError(FallbackableLLMError):
    """Provider returned a rate-limit / throttling signal. Try next provider."""


class QuotaExceededError(FallbackableLLMError):
    """Provider's subscription/quota exhausted. Try next provider."""


class LLMTimeoutError(FallbackableLLMError):
    """Provider timed out at the network/process level. Try next provider."""


class LLMAuthError(LLMError):
    """Authentication / invalid key. Do NOT fall through (config issue)."""


class LLMResponseFormatError(LLMError):
    """Provider returned an unparseable response. Don't fall through (likely a bug)."""


# ──────────────────────── Base client ────────────────────────


class BaseChatClient(ABC):
    """Each provider implements `chat`. The gateway never instantiates this directly."""

    name: str = ""
    """Logical provider key. MUST be set by subclasses."""

    enabled: bool = True
    """If False, gateway skips this client."""

    @abstractmethod
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
        """Send `prompt` to the provider, return LLMResult.

        Args:
            prompt: user prompt content
            prompt_version: caller-supplied label (e.g. "case_gen_v1") so logs/sediment can correlate
            system: optional system prompt
            image: optional PNG/JPEG bytes for multimodal providers
            max_tokens: provider-specific cap on output tokens
            temperature: 0..2; None to use provider default
            json_mode: hint provider to return strict JSON if it supports it
            timeout_seconds: per-call timeout override

        Raises:
            RateLimitError / QuotaExceededError / LLMTimeoutError → fallthrough
            LLMAuthError / LLMResponseFormatError → bubble up
        """
        ...

    async def health(self) -> bool:
        """Quick liveness probe. Default: try a 5-token call. Subclasses may override."""
        try:
            res = await self.chat(
                "reply: ok",
                prompt_version="_health_v1",
                max_tokens=5,
                timeout_seconds=15,
            )
            return bool(res.text)
        except Exception:
            return False
