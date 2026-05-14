"""Collect developer-context evidence for a failed run."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import Run, StepEvent, TestCase
from app.runtime_config import get_dev_context_config
from app.services.dev_context.code_search import (
    configured_code_repos,
    extract_failure_keywords,
    search_workspace_code,
)
from app.services.dev_context.external import collect_external_context
from app.services.dev_context.server_logs import collect_server_logs


async def collect_run_dev_context(*, run_id: str, session: AsyncSession) -> dict[str, Any]:
    run = await session.get(Run, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    case = await session.get(TestCase, run.case_id)
    steps = (
        (
            await session.execute(
                select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    keyword_parts: list[str] = []
    for step in steps[-10:]:
        keyword_parts.extend(
            [
                step.error_message or "",
                str((step.tool_result or {}).get("page_url") or ""),
                step.intent or "",
            ]
        )
    keyword_parts.extend(
        [
            run.error_message or "",
            getattr(case, "name", "") or "",
            getattr(case, "intent", "") or "",
            getattr(case, "module", "") or "",
        ]
    )
    keywords = extract_failure_keywords(keyword_parts)
    cfg = await get_dev_context_config(session)
    repos = configured_code_repos(str(cfg["code_repos"]))
    candidate_files = search_workspace_code(
        workspace_root=str(cfg["workspace_root"]),
        repos=repos,
        keywords=keywords,
        max_files=int(cfg["max_files"]),
        max_matches_per_file=int(cfg["max_matches_per_file"]),
    )

    async def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        from app.services.dev_context.zdev_mcp import call_zdev_tool

        return await call_zdev_tool(name, args, config=cfg)

    external_context = await collect_external_context(
        text_parts=keyword_parts,
        call_tool=_call_tool,
        config=cfg,
    )
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "keywords": keywords,
        "code_context": {
            "workspace_root": str(cfg["workspace_root"]),
            "repos": repos,
            "candidate_files": candidate_files,
        },
        "external_context": external_context,
        "server_logs": collect_server_logs(config_json=str(cfg["server_logs_json"])),
    }
