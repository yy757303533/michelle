"""Developer context status and integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
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
        }
    }
