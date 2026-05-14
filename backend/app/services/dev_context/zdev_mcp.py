"""zstack-dev-mcp stdio client wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from app.agent.mcp_stdio import StdioMCPClient
from app.config import settings


def build_zdev_mcp_client(config: dict[str, Any] | None = None) -> StdioMCPClient:
    """Build a stdio MCP client for the configured zstack-dev-mcp server."""
    mcp_args = str((config or {}).get("zdev_mcp_args") or settings.michelle_zdev_mcp_args)
    if not mcp_args:
        raise RuntimeError("MICHELLE_ZDEV_MCP_ARGS is not configured")
    workspace_root = str((config or {}).get("workspace_root") or settings.michelle_workspace_root)
    mcp_cwd = str((config or {}).get("zdev_mcp_cwd") or settings.michelle_zdev_mcp_cwd)
    cwd = Path(mcp_cwd or workspace_root or ".").resolve()
    extra_env: dict[str, str] = {}
    if workspace_root:
        extra_env["WORKSPACE_DIR"] = str(Path(workspace_root).resolve())
    return StdioMCPClient(
        command=str((config or {}).get("zdev_mcp_command") or settings.michelle_zdev_mcp_command),
        args=shlex.split(mcp_args),
        cwd=cwd,
        timeout_seconds=int(
            (config or {}).get("zdev_mcp_timeout_seconds")
            or settings.michelle_zdev_mcp_timeout_seconds
        ),
        extra_env=extra_env,
    )


async def call_zdev_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with build_zdev_mcp_client(config) as client:
        return await client.call_tool(name, arguments)
