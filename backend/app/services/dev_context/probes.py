"""Best-effort Dev Context integration probes."""

from __future__ import annotations

import time
from typing import Any

from app.services.dev_context.server_logs import collect_server_logs
from app.services.dev_context.zdev_mcp import build_zdev_mcp_client


async def probe_zdev_mcp(config: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with build_zdev_mcp_client(config) as client:
            tools = await client.list_tools()
    except Exception as exc:  # noqa: BLE001 - operator-facing probe
        return {
            "ok": False,
            "detail": str(exc)[:500],
            "tools": [],
            "elapsed_ms": _elapsed_ms(started),
        }
    tool_names = [tool.name for tool in tools]
    return {
        "ok": True,
        "detail": f"{len(tool_names)} tools",
        "tools": tool_names,
        "elapsed_ms": _elapsed_ms(started),
    }


def probe_server_logs(config_json: str) -> dict[str, Any]:
    started = time.monotonic()
    result = collect_server_logs(config_json=config_json, max_lines=20, timeout_seconds=8)
    snippets = result.get("snippets") or []
    ok_count = sum(1 for snippet in snippets if snippet.get("ok"))
    configured = bool(result.get("configured"))
    ok = configured and bool(snippets) and ok_count == len(snippets)
    detail = (
        "not configured"
        if not configured
        else f"{ok_count}/{len(snippets)} snippets ok"
        if snippets
        else "configured but no readable snippets"
    )
    return {
        "ok": ok,
        "detail": detail,
        "configured": configured,
        "servers": result.get("servers") or [],
        "snippets": len(snippets),
        "elapsed_ms": _elapsed_ms(started),
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
