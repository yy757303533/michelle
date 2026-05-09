"""Executor-loop strategy resolution.

UI exposes a single execution-model choice:

* `case_execution_provider=claude-cli` — use the legacy Claude CLI browser loop.
* any other value — use Michelle's JSON-action loop and route model calls to
  that provider.

`executor_loop` still exists as a hidden legacy override for old deployments.

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
from app.runtime_config import get_case_execution_provider, get_executor_loop

ExecutorLoop = str

GENERIC_PROVIDER_SKIP = {"claude-cli"}


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

    Claude CLI is excluded because selecting it means using Claude's own
    browser loop. Codex CLI can drive Michelle's JSON-action loop.
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
    execution_provider = await get_case_execution_provider(session)
    configured = await get_executor_loop(session)
    providers = generic_openai_providers()
    generic_available = bool(providers)
    claude_available = claude_cli_available()
    has_npx = npx_available()

    if execution_provider == "claude-cli":
        return _resolve_claude_cli(
            configured="provider:claude-cli",
            generic_available=generic_available,
            providers=providers,
            claude_available=claude_available,
            has_npx=has_npx,
            detail_ready="Claude CLI loop ready because Execute cases = claude-cli",
        )

    if execution_provider is not None:
        resolved = "generic_openai"
        if execution_provider not in providers:
            return ExecutorStatus(
                status="down",
                configured_loop=f"provider:{execution_provider}",
                resolved_loop=resolved,
                detail=f"selected execution provider is not available: {execution_provider}",
                generic_available=False,
                generic_providers=providers,
                claude_cli_available=claude_available,
                npx_available=has_npx,
            )
        if not has_npx:
            return ExecutorStatus(
                status="down",
                configured_loop=f"provider:{execution_provider}",
                resolved_loop=resolved,
                detail="npx not found; @playwright/mcp cannot start",
                generic_available=True,
                generic_providers=providers,
                claude_cli_available=claude_available,
                npx_available=False,
            )
        return ExecutorStatus(
            status="ready",
            configured_loop=f"provider:{execution_provider}",
            resolved_loop=resolved,
            detail=f"Michelle Loop ready via {execution_provider}",
            generic_available=True,
            generic_providers=providers,
            claude_cli_available=claude_available,
            npx_available=has_npx,
        )

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
        return _resolve_claude_cli(
            configured=configured,
            generic_available=generic_available,
            providers=providers,
            claude_available=claude_available,
            has_npx=has_npx,
            detail_ready="Claude CLI loop ready (legacy compatibility mode)",
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


def _resolve_claude_cli(
    *,
    configured: str,
    generic_available: bool,
    providers: list[str],
    claude_available: bool,
    has_npx: bool,
    detail_ready: str,
) -> ExecutorStatus:
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
        detail=detail_ready,
        generic_available=generic_available,
        generic_providers=providers,
        claude_cli_available=True,
        npx_available=has_npx,
    )
