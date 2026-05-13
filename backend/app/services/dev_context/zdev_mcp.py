"""zstack-dev-mcp stdio client wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from app.agent.mcp_stdio import StdioMCPClient
from app.config import settings


def build_zdev_mcp_client() -> StdioMCPClient:
    """Build a stdio MCP client for the configured zstack-dev-mcp server."""
    if not settings.michelle_zdev_mcp_args:
        raise RuntimeError("MICHELLE_ZDEV_MCP_ARGS is not configured")
    cwd = Path(settings.michelle_zdev_mcp_cwd or settings.michelle_workspace_root or ".").resolve()
    extra_env: dict[str, str] = {}
    if settings.michelle_workspace_root:
        extra_env["WORKSPACE_DIR"] = str(Path(settings.michelle_workspace_root).resolve())
    return StdioMCPClient(
        command=settings.michelle_zdev_mcp_command,
        args=shlex.split(settings.michelle_zdev_mcp_args),
        cwd=cwd,
        timeout_seconds=settings.michelle_zdev_mcp_timeout_seconds,
        extra_env=extra_env,
    )


async def call_zdev_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with build_zdev_mcp_client() as client:
        return await client.call_tool(name, arguments)
