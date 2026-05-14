"""Server log configuration for controlled future SSH log collection."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from app.config import settings
from app.services.dev_context.redaction import redact_sensitive_text

Runner = Any


def configured_server_groups(config_json: str | None = None) -> list[dict[str, Any]]:
    raw = settings.michelle_server_logs_json if config_json is None else config_json
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        servers = data.get("servers") or []
    elif isinstance(data, list):
        servers = data
    else:
        servers = []
    return [server for server in servers if isinstance(server, dict)]


def server_log_security_findings(config_json: str | None = None) -> list[str]:
    findings: list[str] = []
    for server in configured_server_groups(config_json):
        name = str(server.get("name") or server.get("host") or "server")
        if not str(server.get("host") or ""):
            findings.append(f"{name}: missing host")
        if not str(server.get("user") or ""):
            findings.append(f"{name}: missing SSH user")
        paths = list(server.get("log_paths") or [])
        if not paths:
            findings.append(f"{name}: no log_paths configured")
        for path in paths:
            path_s = str(path)
            if not _safe_log_path(path_s):
                findings.append(f"{name}: unsafe log path {path_s!r}")
    return findings


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
    config_json: str | None = None,
    runner: Runner = subprocess.run,
    max_lines: int = 200,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    servers = configured_server_groups(config_json)
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
                    "text": redact_sensitive_text(result.stdout or "", max_chars=8000),
                    "error": redact_sensitive_text(result.stderr or "", max_chars=1000),
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
    return bool(
        path.startswith("/") and "\x00" not in path and "\n" not in path and ".." not in path
    )
