"""Background worker that runs a PRD-to-cases generation pass.

A single `run_job(job_id)` coroutine pulls a `PRDGenerationJob` row,
walks the requested chapters, and persists results back onto the same
row as it goes — so the frontend can poll status without holding an
HTTP connection for 7-45 minutes.

This is the asynchronous counterpart to the previous synchronous
`POST /api/prd/<prd_id>/generate` handler. The handler now creates the
job + spawns this task and returns immediately."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db import async_session_maker
from app.models import PRD, PRDGenerationJob, Project
from app.obs import get_logger
from app.services.case_generator import generate_cases_for_chapter
from app.services.case_versioning import (
    find_prev_prd,
    mark_stale_for_removed_chapters,
    plan_regeneration,
)
from app.services.prd_parser import Chapter

_log = get_logger(__name__)


async def create_job(
    *,
    prd_id: str,
    project_id: str,
    request_payload: dict[str, Any],
    total_chapters: int,
) -> str:
    """Insert a `pending` job row, return its id. The caller schedules
    `run_job(job_id)` as an asyncio task once the row is committed."""
    job_id = "gen_" + uuid4().hex[:12]
    async with async_session_maker() as session:
        job = PRDGenerationJob(
            job_id=job_id,
            prd_id=prd_id,
            project_id=project_id,
            status="pending",
            total_chapters=total_chapters,
            completed_chapters=0,
            saved_cases=0,
            results=[],
            request_payload=request_payload,
        )
        session.add(job)
        await session.commit()
    _log.info(
        "prd.generation.job_created",
        job_id=job_id,
        prd_id=prd_id,
        total_chapters=total_chapters,
    )
    return job_id


def kick_off(job_id: str) -> asyncio.Task:
    """Fire-and-forget the worker. Returns the task so callers can keep a
    reference if they want; we don't gate anything on it."""
    return asyncio.create_task(run_job(job_id))


async def run_job(job_id: str) -> None:
    """Walk the chapters listed in the job's request_payload, persist
    cases, update the job row after each chapter so the UI sees progress.

    Idempotent on completion: if the job is already terminal (done /
    failed) we exit immediately. Doesn't try to handle re-runs of
    half-finished jobs — that's a startup heal concern (see
    `run_lifecycle.heal_stale_runs` for the pattern); jobs left in
    running across a restart are marked failed with reason."""
    log = _log.bind(job_id=job_id)
    async with async_session_maker() as session:
        job = await session.get(PRDGenerationJob, job_id)
        if job is None:
            log.error("prd.generation.job_missing")
            return
        if job.status in ("done", "failed"):
            return
        prd = await session.get(PRD, job.prd_id)
        proj = await session.get(Project, job.project_id) if prd else None
        if prd is None or proj is None:
            job.status = "failed"
            job.error = "prd or project missing"
            job.finished_at = datetime.now(UTC)
            await session.commit()
            return

        body: dict[str, Any] = dict(job.request_payload or {})
        indices: list[int] | None = body.get("chapter_indices")
        max_cases: int = int(body.get("max_cases_per_chapter", 8))
        prefer_provider: str | None = body.get("prefer_provider")

        if indices is None:
            indices = list(range(len(prd.chapters)))
        indices = sorted({i for i in indices if 0 <= i < len(prd.chapters)})

        prev_prd = await find_prev_prd(session, prd)
        await mark_stale_for_removed_chapters(
            session=session, new_prd=prd, prev_prd=prev_prd
        )

        decisions = await plan_regeneration(
            session=session, new_prd=prd, prev_prd=prev_prd, chapter_indices=indices
        )

        # Mark running + commit so the polling client sees the transition
        # before the slow LLM loop begins.
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await session.commit()

        results: list[dict[str, Any]] = []
        for d in decisions:
            chap = Chapter(**prd.chapters[d.chapter_index])
            row: dict[str, Any]
            if d.action != "regenerate":
                row = {
                    "chapter_index": d.chapter_index,
                    "chapter_title": d.title,
                    "saved_count": 0,
                    "skipped": True,
                    "skip_reason": d.reason,
                    "skip_action": d.action,
                    "existing_case_ids": d.existing_case_ids,
                }
            else:
                try:
                    saved, batch = await generate_cases_for_chapter(
                        project_id=prd.project_id,
                        project_name=proj.name,
                        base_url=proj.base_url,
                        chapter=chap,
                        session=session,
                        max_cases=max_cases,
                        prefer_provider=prefer_provider,
                        default_username=proj.default_username or None,
                        default_password=proj.default_password or None,
                    )
                    row = {
                        "chapter_index": d.chapter_index,
                        "chapter_title": d.title,
                        "saved_count": len(saved),
                        "saved_case_ids": [c.case_id for c in saved],
                        "coverage_notes": batch.coverage_notes,
                        "skipped": False,
                    }
                    job.saved_cases += len(saved)
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "prd.generation.chapter_failed",
                        chapter_index=d.chapter_index,
                        error=str(exc)[:200],
                    )
                    row = {
                        "chapter_index": d.chapter_index,
                        "chapter_title": d.title,
                        "saved_count": 0,
                        "skipped": False,
                        "error": str(exc)[:200],
                    }
            results.append(row)
            job.results = list(results)  # SQLAlchemy JSON column needs assignment to detect change
            job.completed_chapters = len(results)
            await session.commit()

        job.status = "done"
        job.finished_at = datetime.now(UTC)
        await session.commit()
        log.info(
            "prd.generation.job_done",
            saved_cases=job.saved_cases,
            chapters_processed=len(results),
        )
