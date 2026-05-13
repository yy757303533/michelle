"""Developer context status and integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.services.dev_context.code_search import configured_code_repos
from app.services.dev_context.server_logs import configured_server_groups
from app.services.dev_context.workspace import inspect_workspace

router = APIRouter()


@router.get("/status")
async def get_dev_context_status() -> dict:
    workspace = inspect_workspace(settings.michelle_workspace_root)
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
                "cwd": settings.michelle_zdev_mcp_cwd,
            },
            "code_search": {
                "repos": configured_code_repos(),
                "max_files": settings.michelle_dev_context_max_files,
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
        }
    }
