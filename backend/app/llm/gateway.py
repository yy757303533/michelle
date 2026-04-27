"""LLMGateway — provider-agnostic chat router with automatic fallback.

Strategy:
  1. Pick the highest-priority enabled client.
  2. Call `chat()`. On RateLimit/Quota/Timeout → log fallback, try next client.
  3. On LLMAuthError or LLMResponseFormatError → bubble up (config or bug).
  4. If all clients fail → re-raise the last `FallbackableLLMError`.

Configuration order (highest priority first; lower number = higher priority):

  10  claude-cli   subscription, $0 main path
  15  codex-cli    OpenAI subscription, secondary CLI
  20  flywheel     premium proxy (Opus / GPT-5.x) when quota allows
  25  deepseek     cheap reasoning + chat (OpenAI-compatible)
  30  qwen         Alibaba DashScope (OpenAI-compatible mode)
  35  glm          智谱 (OpenAI-compatible)
  40  kimi         Moonshot (OpenAI-compatible)
  45  gemini       Google (OpenAI-compatible)
  50  minimax      original Day-3 fallback, supports vision natively
  60  relay        any OpenAI-compatible relay (OneAPI/NewAPI/OpenRouter…)

Selection can be overridden per-call via `prefer=...`. All providers are
opt-in: empty API keys/binaries → disabled, never tried.
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
from app.llm.codex_cli import CodexCLIClient
from app.llm.flywheel import FlywheelClient
from app.llm.minimax import MiniMaxClient
from app.llm.openai_compatible import OpenAICompatibleClient
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


def _oai(name: str, key: str, base: str, model: str, *, supports_image: bool = False) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        name=name,
        api_key=key,
        base_url=base,
        default_model=model,
        supports_image=supports_image,
    )


def build_default_clients() -> list[GatewayClient]:
    """Construct clients based on env. Empty configs become disabled clients."""
    out: list[GatewayClient] = []

    out.append(GatewayClient(
        name="claude-cli", client=ClaudeCLIClient(),
        priority=10, available=_claude_binary_present(),
    ))
    out.append(GatewayClient(
        name="codex-cli", client=CodexCLIClient(binary=settings.codex_cli_path or "codex"),
        priority=15, available=settings.codex_enabled and _codex_binary_present(),
    ))
    out.append(GatewayClient(
        name="flywheel", client=FlywheelClient(),
        priority=20, available=bool(settings.flywheel_token),
    ))
    out.append(GatewayClient(
        name="deepseek",
        client=_oai("deepseek", settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model),
        priority=25, available=settings.has_deepseek,
    ))
    out.append(GatewayClient(
        name="qwen",
        client=_oai("qwen", settings.qwen_api_key, settings.qwen_base_url, settings.qwen_model, supports_image=True),
        priority=30, available=settings.has_qwen,
    ))
    out.append(GatewayClient(
        name="glm",
        client=_oai("glm", settings.glm_api_key, settings.glm_base_url, settings.glm_model, supports_image=True),
        priority=35, available=settings.has_glm,
    ))
    out.append(GatewayClient(
        name="kimi",
        client=_oai("kimi", settings.kimi_api_key, settings.kimi_base_url, settings.kimi_model, supports_image=True),
        priority=40, available=settings.has_kimi,
    ))
    out.append(GatewayClient(
        name="gemini",
        client=_oai("gemini", settings.gemini_api_key, settings.gemini_base_url, settings.gemini_model, supports_image=True),
        priority=45, available=settings.has_gemini,
    ))
    out.append(GatewayClient(
        name="minimax", client=MiniMaxClient(),
        priority=50, available=bool(settings.minimax_api_key),
    ))
    if settings.has_relay:
        out.append(GatewayClient(
            name=settings.relay_name or "relay",
            client=_oai(settings.relay_name or "relay", settings.relay_api_key, settings.relay_base_url, settings.relay_model),
            priority=60, available=True,
        ))
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
