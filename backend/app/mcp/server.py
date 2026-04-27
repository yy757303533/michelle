"""Michelle MCP server skeleton — Day 1 stub.

We declare the surface here so the agent-native intent is captured in code from
day 1. Day 6 fills the bodies (after the REST API for runs is real).

This module is intentionally NOT auto-mounted; users opt in by running
`uv run python -m app.mcp.server` once the implementation lands.
"""

from __future__ import annotations

from typing import Any

from app.obs import get_logger

_log = get_logger(__name__)


def build_mcp_server():
    """Construct an MCP server exposing Michelle tools.

    Returns:
        FastMCP server instance (or None if MCP deps missing).
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        _log.warning(
            "mcp.unavailable",
            note="Install 'mcp' package to enable Michelle's MCP surface",
        )
        return None

    mcp = FastMCP("michelle")

    @mcp.tool()
    async def list_cases(project_id: str, status: str | None = None) -> dict[str, Any]:
        """List Michelle test cases for a project, optionally filtered by review status."""
        # Day 6: query DB and serialise. For now, signal stub.
        return {"data": [], "stub": True}

    @mcp.tool()
    async def get_case(case_id: str) -> dict[str, Any]:
        """Fetch a single test case by id."""
        return {"data": None, "stub": True, "case_id": case_id}

    @mcp.tool()
    async def execute_case(case_id: str, env: str = "default") -> dict[str, Any]:
        """Execute a test case (same path as Web UI 'Run' button). Returns run_id."""
        return {"run_id": None, "stub": True, "case_id": case_id, "env": env}

    @mcp.tool()
    async def get_run(run_id: str) -> dict[str, Any]:
        """Get run status + step events."""
        return {"data": None, "stub": True, "run_id": run_id}

    @mcp.tool()
    async def diagnose(run_id: str) -> dict[str, Any]:
        """Trigger AI diagnosis on a failed run. Returns diagnosis structured fields."""
        return {"diagnosis": None, "stub": True, "run_id": run_id}

    @mcp.tool()
    async def suggest_cases(description: str, max_cases: int = 8) -> dict[str, Any]:
        """Light-weight: propose draft cases for a feature without saving."""
        return {"drafts": [], "stub": True, "description": description, "max_cases": max_cases}

    _log.info("mcp.server.built", tools=6)
    return mcp


if __name__ == "__main__":
    server = build_mcp_server()
    if server is None:
        raise SystemExit("MCP deps not available; run `uv add mcp` first")
    server.run()
