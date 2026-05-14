"""Dispatch PRD import requests to concrete source providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.runtime_config import get_dev_context_config
from app.services.dev_context.zdev_mcp import call_zdev_tool
from app.services.prd_sources.gitlab_mcp import fetch_gitlab_file_via_mcp
from app.services.prd_sources.models import PRDSourceDocument
from app.services.prd_sources.workspace_file import fetch_workspace_file


async def fetch_prd_source(
    source: dict[str, Any],
    *,
    session: AsyncSession | None = None,
) -> PRDSourceDocument:
    source_type = str(source.get("type") or "")
    if source_type == "markdown":
        markdown = str(source.get("markdown") or "")
        if not markdown.strip():
            raise ValueError("markdown source requires non-empty markdown")
        return PRDSourceDocument(
            markdown=markdown,
            suggested_name=str(source.get("name") or ""),
            source_ref={"source_type": "markdown"},
        )
    if source_type == "workspace":
        workspace_root = settings.michelle_workspace_root
        if session is not None:
            workspace_root = str((await get_dev_context_config(session))["workspace_root"])
        if not workspace_root:
            raise ValueError("MICHELLE_WORKSPACE_ROOT is not configured")
        return fetch_workspace_file(
            root=Path(workspace_root),
            repo=str(source.get("repo") or ""),
            file_path=str(source.get("file_path") or ""),
            ref=str(source.get("ref") or "") or None,
        )
    if source_type == "gitlab_mcp":
        cfg = await get_dev_context_config(session) if session is not None else None

        async def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await call_zdev_tool(name, args, config=cfg)

        return await fetch_gitlab_file_via_mcp(
            url=str(source.get("url") or "") or None,
            project=str(source.get("project") or "") or None,
            file_path=str(source.get("file_path") or "") or None,
            ref=str(source.get("ref") or "") or None,
            call_tool=_call_tool,
        )
    raise ValueError("unsupported PRD source type")
