"""Executor-loop strategy resolution.

UI exposes three operator choices:

* `auto` — prefer Michelle's generic OpenAI-compatible loop; fall back to
  Claude CLI only when no generic provider is configured.
* `generic_openai` — Michelle-owned JSON-action loop driven by the LLM gateway.
* `claude_cli` — legacy compatibility path using Claude CLI's built-in loop.

This module is intentionally side-effect light. It never runs `claude -p` just
to decide availability; it only checks config/binaries and lets an actual run
surface execution-time failures.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.llm.gateway import get_gateway
from app.runtime_config import get_executor_loop

ExecutorLoop = str

GENERIC_PROVIDER_SKIP = {"claude-cli", "codex-cli", "minimax"}


@dataclass(frozen=True)
class ExecutorStatus:
    status: str  # ready | starting | down | unknown
    configured_loop: str
    resolved_loop: str | None
    detail: str
    generic_available: bool
    generic_providers: list[str]
    claude_cli_available: bool
    npx_available: bool


def generic_openai_providers() -> list[str]:
    """Available LLM gateway providers suitable for the generic loop.

    The first implementation is a JSON-action loop, so it does not require
    native function-calling yet. We still exclude local subscription CLIs and
    the native MiniMax client because this strategy is meant to be portable via
    OpenAI-compatible provider/gateway config.
    """

    gw = get_gateway()
    return [name for name in gw.available_providers if name not in GENERIC_PROVIDER_SKIP]


def _binary_available(binary: str) -> bool:
    if "/" in binary:
        p = Path(binary).expanduser()
        return p.is_file() and p.exists()
    return shutil.which(binary) is not None


def claude_cli_available() -> bool:
    return _binary_available(settings.claude_cli_path or "claude")


def npx_available() -> bool:
    return _binary_available("npx")


async def resolve_executor_status(session: AsyncSession | None = None) -> ExecutorStatus:
    configured = await get_executor_loop(session)
    providers = generic_openai_providers()
    generic_available = bool(providers)
    claude_available = claude_cli_available()
    has_npx = npx_available()

    if configured == "generic_openai":
        resolved = "generic_openai"
        if not generic_available:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail="no OpenAI-compatible provider configured for generic loop",
                generic_available=False,
                generic_providers=providers,
                claude_cli_available=claude_available,
                npx_available=has_npx,
            )
        if not has_npx:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail="npx not found; @playwright/mcp cannot start",
                generic_available=True,
                generic_providers=providers,
                claude_cli_available=claude_available,
                npx_available=False,
            )
        return ExecutorStatus(
            status="ready",
            configured_loop=configured,
            resolved_loop=resolved,
            detail=f"generic loop ready via {providers[0]}",
            generic_available=True,
            generic_providers=providers,
            claude_cli_available=claude_available,
            npx_available=has_npx,
        )

    if configured == "claude_cli":
        resolved = "claude_cli"
        if not claude_available:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail=f"claude CLI not found: {settings.claude_cli_path or 'claude'}",
                generic_available=generic_available,
                generic_providers=providers,
                claude_cli_available=False,
                npx_available=has_npx,
            )
        if not has_npx:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail="npx not found; @playwright/mcp cannot start",
                generic_available=generic_available,
                generic_providers=providers,
                claude_cli_available=True,
                npx_available=False,
            )
        return ExecutorStatus(
            status="ready",
            configured_loop=configured,
            resolved_loop=resolved,
            detail="Claude CLI loop ready (legacy compatibility mode)",
            generic_available=generic_available,
            generic_providers=providers,
            claude_cli_available=True,
            npx_available=has_npx,
        )

    # Auto: prefer generic when configured. If generic is configured but a
    # shared dependency (npx) is missing, block instead of silently falling
    # through. If no generic provider exists, then use Claude CLI as explicit
    # legacy fallback.
    if generic_available:
        resolved = "generic_openai"
        if not has_npx:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail="generic provider configured, but npx is missing",
                generic_available=True,
                generic_providers=providers,
                claude_cli_available=claude_available,
                npx_available=False,
            )
        return ExecutorStatus(
            status="ready",
            configured_loop=configured,
            resolved_loop=resolved,
            detail=f"auto selected generic loop via {providers[0]}",
            generic_available=True,
            generic_providers=providers,
            claude_cli_available=claude_available,
            npx_available=has_npx,
        )

    if claude_available:
        resolved = "claude_cli"
        if not has_npx:
            return ExecutorStatus(
                status="down",
                configured_loop=configured,
                resolved_loop=resolved,
                detail="Claude CLI is installed, but npx is missing",
                generic_available=False,
                generic_providers=providers,
                claude_cli_available=True,
                npx_available=False,
            )
        return ExecutorStatus(
            status="ready",
            configured_loop=configured,
            resolved_loop=resolved,
            detail="auto selected Claude CLI loop because no generic provider is configured",
            generic_available=False,
            generic_providers=providers,
            claude_cli_available=True,
            npx_available=has_npx,
        )

    return ExecutorStatus(
        status="down",
        configured_loop=configured,
        resolved_loop=None,
        detail="no generic provider configured and claude CLI is unavailable",
        generic_available=False,
        generic_providers=providers,
        claude_cli_available=False,
        npx_available=has_npx,
    )
