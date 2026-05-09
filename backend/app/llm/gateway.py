"""LLMGateway — provider-agnostic chat router with automatic fallback.

Strategy:
  1. Pick the highest-priority enabled client.
  2. Call `chat()`. On RateLimit/Quota/Timeout → log fallback, try next client.
  3. On LLMAuthError or LLMResponseFormatError → bubble up (config or bug).
  4. If all clients fail → re-raise the last `FallbackableLLMError`.

Supported provider order (highest priority first; lower number = higher priority):

  10  claude-cli   subscription, $0 main path
  15  codex-cli    OpenAI subscription, secondary CLI

Selection can be overridden per-call via `prefer=...`. All providers are
opt-in: empty API keys/binaries → disabled, never tried.

Other provider clients remain in the codebase behind tests, but are not part
of the default product surface until we intentionally re-enable them.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass

from app.config import settings
from app.llm.base import (
    BaseChatClient,
    FallbackableLLMError,
    LLMError,
    LLMResult,
)
from app.llm.claude_cli import ClaudeCLIClient
from app.llm.codex_cli import CodexCLIClient
from app.obs import EVENTS, get_logger

_log = get_logger(__name__)


@dataclass
class GatewayClient:
    name: str
    client: BaseChatClient
    priority: int  # lower = higher priority
    available: bool


def _claude_binary_present() -> bool:
    return shutil.which(settings.claude_cli_path or "claude") is not None


def _codex_binary_present() -> bool:
    return shutil.which(settings.codex_cli_path or "codex") is not None


def build_default_clients() -> list[GatewayClient]:
    """Construct the supported provider set.

    Keep this list intentionally small for internal rollout. Extra provider
    clients still exist as implementation modules, but they are not exposed or
    used unless we explicitly add them back here.
    """
    out: list[GatewayClient] = []

    out.append(
        GatewayClient(
            name="claude-cli",
            client=ClaudeCLIClient(),
            priority=10,
            available=_claude_binary_present(),
        )
    )
    out.append(
        GatewayClient(
            name="codex-cli",
            client=CodexCLIClient(binary=settings.codex_cli_path or "codex"),
            priority=15,
            available=settings.codex_enabled and _codex_binary_present(),
        )
    )
    return sorted(out, key=lambda g: g.priority)


class LLMGateway:
    def __init__(self, clients: list[GatewayClient] | None = None):
        self.clients = clients or build_default_clients()

    @property
    def available_providers(self) -> list[str]:
        return [g.name for g in self.clients if g.available]

    def get(self, name: str) -> BaseChatClient | None:
        for g in self.clients:
            if g.name == name and g.available:
                return g.client
        return None

    async def chat(
        self,
        prompt: str,
        *,
        prompt_version: str,
        prefer: str | None = None,
        skip: list[str] | None = None,
        fallback: bool = True,
        **kwargs,
    ) -> LLMResult:
        """Send a chat request, falling through providers as needed.

        Args:
            prefer: provider name to try first (must be available)
            skip: provider names to never try in this call
            fallback: when False, only the selected first provider is tried
        """
        skip_set = set(skip or [])

        ordered: list[GatewayClient] = []
        if prefer:
            for g in self.clients:
                if g.name == prefer and g.available and g.name not in skip_set:
                    ordered.append(g)
                    break
        if fallback:
            for g in self.clients:
                if g.available and g.name not in skip_set and g not in ordered:
                    ordered.append(g)
        elif not prefer:
            for g in self.clients:
                if g.available and g.name not in skip_set:
                    ordered.append(g)
                    break

        if not ordered:
            raise LLMError(
                "no LLM provider available — check Claude CLI or CODEX_ENABLED/CODEX_CLI_PATH",
                provider="gateway",
            )

        last_err: FallbackableLLMError | None = None
        for i, g in enumerate(ordered):
            t0 = time.monotonic()
            try:
                result = await g.client.chat(prompt, prompt_version=prompt_version, **kwargs)
                await _record_llm_call(
                    provider=result.provider or g.name,
                    model=result.model,
                    prompt_version=prompt_version,
                    ok=True,
                    latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                )
                return result
            except FallbackableLLMError as e:
                await _record_llm_call(
                    provider=e.provider or g.name,
                    model="",
                    prompt_version=prompt_version,
                    ok=False,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                next_provider = ordered[i + 1].name if i + 1 < len(ordered) else None
                if next_provider is not None:
                    _log.warning(
                        EVENTS.LLM_FALLBACK.name,
                        from_provider=g.name,
                        to_provider=next_provider,
                        reason=type(e).__name__,
                        detail=str(e)[:200],
                    )
                last_err = e
                continue
            except LLMError as e:
                # Non-fallthrough errors bubble immediately (auth, parse).
                _log.error(EVENTS.LLM_FAILED.name, provider=g.name)
                await _record_llm_call(
                    provider=g.name,
                    model="",
                    prompt_version=prompt_version,
                    ok=False,
                    error_type="LLMError",
                    error_message=str(e),
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                raise

        # All providers exhausted
        _log.error(
            EVENTS.LLM_FAILED.name,
            tried=[g.name for g in ordered],
            error=str(last_err)[:300] if last_err else "all-fallthroughs",
        )
        if last_err:
            raise last_err
        raise LLMError("all providers exhausted with no recoverable error", provider="gateway")

    async def health(self) -> dict[str, dict]:
        """Per-provider availability + last error if recently failed.

        Note: this just reports `available` (config presence). Full liveness
        probe would actually call `chat()`; that's done lazily by `/api/llm/health`.
        """
        out: dict[str, dict] = {}
        for g in self.clients:
            detail = "available"
            if g.name == "claude-cli" and not g.available:
                detail = f"binary not found: {settings.claude_cli_path or 'claude'}"
            if g.name == "codex-cli" and not settings.codex_enabled:
                detail = "disabled by CODEX_ENABLED=false"
            elif g.name == "codex-cli" and not g.available:
                detail = f"binary not found: {settings.codex_cli_path or 'codex'}"
            out[g.name] = {
                "available": g.available,
                "priority": g.priority,
                "detail": detail,
            }
        return out


# ── Module-level singleton ──
_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


def reset_gateway() -> None:
    """For tests / config changes."""
    global _gateway
    _gateway = None


async def _record_llm_call(
    *,
    provider: str,
    model: str,
    prompt_version: str,
    ok: bool,
    error_type: str = "",
    error_message: str = "",
    latency_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = None,
) -> None:
    try:
        from app.db import async_session_maker
        from app.models import LLMCall

        async with async_session_maker() as session:
            session.add(
                LLMCall(
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                    ok=ok,
                    error_type=error_type,
                    error_message=error_message[:500],
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - metrics must not break LLM calls
        _log.debug("llm.metrics.record_failed", error=str(exc)[:200])
