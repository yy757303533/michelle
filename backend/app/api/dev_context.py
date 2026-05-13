"""Developer context status and integration endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter

from app.config import settings
from app.services.dev_context.code_search import configured_code_repos
from app.services.dev_context.server_logs import (
    configured_server_groups,
    server_log_security_findings,
)
from app.services.dev_context.workspace import inspect_workspace

router = APIRouter()


@router.get("/status")
async def get_dev_context_status() -> dict:
    workspace = inspect_workspace(settings.michelle_workspace_root)
    mcp_args = settings.michelle_zdev_mcp_args.strip()
    mcp_cwd = settings.michelle_zdev_mcp_cwd.strip()
    mcp_entry = _first_existing_arg_path(mcp_args, cwd=mcp_cwd)
    findings = _dev_context_security_findings(
        workspace_ok=workspace.ok,
        mcp_args=mcp_args,
        mcp_cwd=mcp_cwd,
    )
    return {
        "data": {
            "workspace": {
                "enabled": workspace.enabled,
                "ok": workspace.ok,
                "root": workspace.root,
                "detail": workspace.detail,
                "repos": [repo.__dict__ for repo in workspace.repos],
            },
            "zdev_mcp": {
                "configured": bool(settings.michelle_zdev_mcp_args),
                "command": settings.michelle_zdev_mcp_command,
                "command_available": shutil.which(settings.michelle_zdev_mcp_command) is not None,
                "cwd": settings.michelle_zdev_mcp_cwd,
                "cwd_exists": bool(mcp_cwd and Path(mcp_cwd).exists()),
                "entrypoint": str(mcp_entry) if mcp_entry else "",
                "entrypoint_exists": bool(mcp_entry and mcp_entry.exists()),
            },
            "code_search": {
                "repos": configured_code_repos(),
                "max_files": settings.michelle_dev_context_max_files,
                "max_matches_per_file": settings.michelle_dev_context_max_matches_per_file,
            },
            "server_logs": {
                "configured": bool(configured_server_groups()),
                "servers": [
                    {
                        "name": str(s.get("name") or s.get("host") or ""),
                        "env": str(s.get("env") or ""),
                        "roles": list(s.get("roles") or []),
                        "log_paths": list(s.get("log_paths") or []),
                    }
                    for s in configured_server_groups()
                ],
            },
            "security": {
                "ok": not findings,
                "findings": findings,
                "boundary": [
                    "DevContext status is intended for authenticated operators.",
                    "Server logs are read-only and limited to configured log_paths.",
                    "Log output is redacted before being sent to diagnosis evidence.",
                    "Michelle does not expose an arbitrary SSH shell.",
                ],
            },
        }
    }


def _first_existing_arg_path(args: str, *, cwd: str = "") -> Path | None:
    for part in args.split():
        candidate = Path(part)
        if candidate.suffix in {".js", ".mjs", ".cjs"} or "/" in part:
            if not candidate.is_absolute() and cwd:
                return Path(cwd) / candidate
            return candidate
    return None


def _dev_context_security_findings(
    *,
    workspace_ok: bool,
    mcp_args: str,
    mcp_cwd: str,
) -> list[str]:
    findings: list[str] = []
    if not settings.michelle_workspace_root:
        findings.append("MICHELLE_WORKSPACE_ROOT is not configured")
    elif not workspace_ok:
        findings.append("workspace root is configured but not healthy")
    if not mcp_args:
        findings.append("MICHELLE_ZDEV_MCP_ARGS is not configured")
    entrypoint = _first_existing_arg_path(mcp_args, cwd=mcp_cwd)
    if entrypoint and not entrypoint.exists():
        findings.append(f"zstack-dev-mcp entrypoint does not exist: {entrypoint}")
    if mcp_cwd and not Path(mcp_cwd).exists():
        findings.append(f"zstack-dev-mcp cwd does not exist: {mcp_cwd}")
    findings.extend(server_log_security_findings())
    return findings
