"""LLMGateway — provider-agnostic chat router with automatic fallback.

Strategy:
  1. Pick the highest-priority enabled client.
  2. Call `chat()`. On RateLimit/Quota/Timeout → log fallback, try next client.
  3. On LLMAuthError or LLMResponseFormatError → bubble up (config or bug).
  4. If all clients fail → re-raise the last `FallbackableLLMError`.

Configuration order (highest priority first):
  - claude-cli (if `claude` binary present + subscription)
  - flywheel   (if FLYWHEEL_TOKEN; quota currently exhausted)
  - minimax    (if MINIMAX_API_KEY)

Selection can be overridden per-call via `prefer=...`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from app.config import settings
from app.llm.base import (
    BaseChatClient,
    FallbackableLLMError,
    LLMError,
    LLMResult,
)
from app.llm.claude_cli import ClaudeCLIClient
from app.llm.flywheel import FlywheelClient
from app.llm.minimax import MiniMaxClient
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


def build_default_clients() -> list[GatewayClient]:
    """Construct clients based on env. Empty configs become disabled clients."""
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
            name="flywheel",
            client=FlywheelClient(),
            priority=20,
            available=bool(settings.flywheel_token),
        )
    )
    out.append(
        GatewayClient(
            name="minimax",
            client=MiniMaxClient(),
            priority=30,
            available=bool(settings.minimax_api_key),
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
        **kwargs,
    ) -> LLMResult:
        """Send a chat request, falling through providers as needed.

        Args:
            prefer: provider name to try first (must be available)
            skip: provider names to never try in this call
        """
        skip_set = set(skip or [])

        ordered: list[GatewayClient] = []
        if prefer:
            for g in self.clients:
                if g.name == prefer and g.available and g.name not in skip_set:
                    ordered.append(g)
                    break
        for g in self.clients:
            if g.available and g.name not in skip_set and g not in ordered:
                ordered.append(g)

        if not ordered:
            raise LLMError(
                "no LLM provider available — check Claude CLI / MINIMAX_API_KEY / FLYWHEEL_TOKEN",
                provider="gateway",
            )

        last_err: FallbackableLLMError | None = None
        for i, g in enumerate(ordered):
            try:
                return await g.client.chat(prompt, prompt_version=prompt_version, **kwargs)
            except FallbackableLLMError as e:
                next_provider = ordered[i + 1].name if i + 1 < len(ordered) else None
                _log.warning(
                    EVENTS.LLM_FALLBACK.name,
                    from_provider=g.name,
                    to_provider=next_provider,
                    reason=type(e).__name__,
                    detail=str(e)[:200],
                )
                last_err = e
                continue
            except LLMError:
                # Non-fallthrough errors bubble immediately (auth, parse).
                _log.error(EVENTS.LLM_FAILED.name, provider=g.name)
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
            out[g.name] = {
                "available": g.available,
                "priority": g.priority,
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
