"""Server log configuration for controlled future SSH log collection."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from app.config import settings

Runner = Any


def configured_server_groups() -> list[dict[str, Any]]:
    if not settings.michelle_server_logs_json.strip():
        return []
    try:
        data = json.loads(settings.michelle_server_logs_json)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        servers = data.get("servers") or []
    elif isinstance(data, list):
        servers = data
    else:
        servers = []
    return [server for server in servers if isinstance(server, dict)]


def collect_server_log_placeholders() -> dict[str, Any]:
    servers = configured_server_groups()
    return {
        "configured": bool(servers),
        "servers": [
            {
                "name": str(s.get("name") or s.get("host") or ""),
                "env": str(s.get("env") or ""),
                "roles": list(s.get("roles") or []),
                "log_paths": list(s.get("log_paths") or []),
            }
            for s in servers
        ],
        "snippets": [],
    }


def collect_server_logs(
    *,
    runner: Runner = subprocess.run,
    max_lines: int = 200,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    servers = configured_server_groups()
    snippets: list[dict[str, Any]] = []
    for server in servers:
        host = str(server.get("host") or "")
        user = str(server.get("user") or "")
        name = str(server.get("name") or host)
        if not host or not user:
            continue
        for path in list(server.get("log_paths") or [])[:5]:
            path_s = str(path)
            if not _safe_log_path(path_s):
                continue
            cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                f"{user}@{host}",
                f"tail -n {max_lines} -- {path_s}",
            ]
            try:
                result = runner(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                snippets.append(
                    {"server": name, "path": path_s, "ok": False, "error": str(exc)[:300]}
                )
                continue
            snippets.append(
                {
                    "server": name,
                    "path": path_s,
                    "ok": result.returncode == 0,
                    "text": (result.stdout or "")[-8000:],
                    "error": (result.stderr or "")[-1000:],
                }
            )
    return {
        "configured": bool(servers),
        "servers": [
            {
                "name": str(s.get("name") or s.get("host") or ""),
                "env": str(s.get("env") or ""),
                "roles": list(s.get("roles") or []),
                "log_paths": list(s.get("log_paths") or []),
            }
            for s in servers
        ],
        "snippets": snippets,
    }


def _safe_log_path(path: str) -> bool:
    return bool(path.startswith("/") and "\x00" not in path and "\n" not in path and ".." not in path)
