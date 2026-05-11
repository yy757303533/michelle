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

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app import db as _db
from app.llm import get_gateway
from app.models import PRD, PRDGenerationJob, Project, TestCase
from app.obs import get_logger
from app.services.case_generator import (
    generate_cases_for_chapter,
    generate_cases_for_chapters,
    is_actionable_chapter,
)
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
    job_id, _created = await create_or_reuse_job(
        prd_id=prd_id,
        project_id=project_id,
        request_payload=request_payload,
        total_chapters=total_chapters,
    )
    return job_id


async def create_or_reuse_job(
    *,
    prd_id: str,
    project_id: str,
    request_payload: dict[str, Any],
    total_chapters: int,
) -> tuple[str, bool]:
    """Create a job unless the PRD already has active generation.

    Returns `(job_id, created)`. The unique partial index on
    `prd_generation_jobs.prd_id` closes the double-click / page-refresh race
    for pending/running jobs.
    """
    active = await get_active_job(prd_id)
    if active is not None:
        return active.job_id, False

    job_id = "gen_" + uuid4().hex[:12]
    async with _db.async_session_maker() as session:
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
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            active = await get_active_job(prd_id)
            if active is not None:
                return active.job_id, False
            raise
    _log.info(
        "prd.generation.job_created",
        job_id=job_id,
        prd_id=prd_id,
        total_chapters=total_chapters,
    )
    return job_id, True


async def get_active_job(prd_id: str) -> PRDGenerationJob | None:
    async with _db.async_session_maker() as session:
        return (
            (
                await session.execute(
                    select(PRDGenerationJob)
                    .where(PRDGenerationJob.prd_id == prd_id)
                    .where(PRDGenerationJob.status.in_(["pending", "running"]))
                    .order_by(PRDGenerationJob.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )


async def rollback_generated_cases(session, job_id: str) -> int:
    """Delete cases produced by a cancelled generation job.

    Only pending, unedited AI rows are removed. If a human has already
    approved/edited a case, it has left the batch's rollback ownership.
    """
    stmt = (
        select(TestCase)
        .where(TestCase.generation_job_id == job_id)
        .where(TestCase.source == "ai-generated")
        .where(TestCase.review_status == "pending")
    )
    rows = (await session.execute(stmt)).scalars().all()
    deleted = 0
    for row in rows:
        if row.manual_edited_fields:
            continue
        await session.delete(row)
        deleted += 1
    await session.commit()
    return deleted


def kick_off(job_id: str) -> asyncio.Task:
    """Fire-and-forget the worker. Returns the task so callers can keep a
    reference if they want; we don't gate anything on it."""
    return asyncio.create_task(run_job(job_id))


async def run_job(job_id: str) -> None:
    """Walk the chapters listed in the job's request_payload, persist
    cases, update the job row after each chapter so the UI sees progress.

    Idempotent on completion: if the job is already terminal (done /
    failed / cancelled) we exit immediately. Doesn't try to handle re-runs of
    half-finished jobs — that's a startup heal concern (see
    `run_lifecycle.heal_stale_runs` for the pattern); jobs left in
    running across a restart are marked failed with reason."""
    log = _log.bind(job_id=job_id)
    async with _db.async_session_maker() as session:
        job = await session.get(PRDGenerationJob, job_id)
        if job is None:
            log.error("prd.generation.job_missing")
            return
        if job.status in ("running", "done", "failed", "cancelled"):
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
        preflight_timeout: int = int(body.get("preflight_timeout_seconds", 20))

        if indices is None:
            indices = list(range(len(prd.chapters)))
        indices = sorted({i for i in indices if 0 <= i < len(prd.chapters)})

        prev_prd = await find_prev_prd(session, prd)
        await mark_stale_for_removed_chapters(session=session, new_prd=prd, prev_prd=prev_prd)

        decisions = await plan_regeneration(
            session=session, new_prd=prd, prev_prd=prev_prd, chapter_indices=indices
        )

        # Mark running + commit so the polling client sees the transition
        # before the slow LLM loop begins.
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await session.commit()

        actionable_generation = any(
            d.action == "regenerate"
            and is_actionable_chapter(Chapter(**prd.chapters[d.chapter_index]))
            for d in decisions
        )
        if actionable_generation:
            try:
                await _preflight_llm_provider(prefer_provider, preflight_timeout)
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = (
                    "LLM provider unavailable before generation: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
                job.finished_at = datetime.now(UTC)
                await session.commit()
                log.error("prd.generation.provider_unavailable", error=job.error)
                return

        results: list[dict[str, Any]] = []
        i = 0
        batch_size = int(body.get("batch_chapter_count", 4))
        while i < len(decisions):
            d = decisions[i]
            await session.refresh(job)
            if job.status == "cancelled":
                await rollback_generated_cases(session, job_id)
                log.info("prd.generation.job_cancelled")
                return
            chap = Chapter(**prd.chapters[d.chapter_index])
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
                saved_count = 0
            elif not is_actionable_chapter(chap):
                row = {
                    "chapter_index": d.chapter_index,
                    "chapter_title": d.title,
                    "saved_count": 0,
                    "skipped": True,
                    "skip_reason": "chapter has no browser-actionable requirement",
                    "skip_action": "non_actionable",
                    "coverage_notes": (
                        "Skipped before LLM call: this chapter looks like metadata, "
                        "navigation scaffolding, or otherwise has no browser-actionable requirement."
                    ),
                }
                saved_count = 0
            else:
                try:
                    batch_decisions = [d]
                    j = i + 1
                    while j < len(decisions) and len(batch_decisions) < max(1, batch_size):
                        nd = decisions[j]
                        nch = Chapter(**prd.chapters[nd.chapter_index])
                        if nd.action != "regenerate" or not is_actionable_chapter(nch):
                            break
                        batch_decisions.append(nd)
                        j += 1

                    if len(batch_decisions) == 1:
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
                            login_url=proj.login_url or None,
                            generation_job_id=job_id,
                        )
                        batch_rows = [
                            {
                                "chapter_index": d.chapter_index,
                                "chapter_title": d.title,
                                "saved_count": len(saved),
                                "saved_case_ids": [c.case_id for c in saved],
                                "coverage_notes": batch.coverage_notes,
                                "skipped": False,
                            }
                        ]
                    else:
                        chapter_objs = [
                            Chapter(**prd.chapters[bd.chapter_index]) for bd in batch_decisions
                        ]
                        generated = await generate_cases_for_chapters(
                            project_id=prd.project_id,
                            project_name=proj.name,
                            base_url=proj.base_url,
                            chapters=chapter_objs,
                            session=session,
                            max_cases=max_cases,
                            prefer_provider=prefer_provider,
                            default_username=proj.default_username or None,
                            default_password=proj.default_password or None,
                            login_url=proj.login_url or None,
                            generation_job_id=job_id,
                        )
                        generated_by_id = {
                            f"{chapter.level}:{chapter.normalized_title}": (saved, batch)
                            for chapter, saved, batch in generated
                        }
                        batch_rows = []
                        for bd, chapter_obj in zip(batch_decisions, chapter_objs, strict=True):
                            saved, batch = generated_by_id.get(
                                f"{chapter_obj.level}:{chapter_obj.normalized_title}",
                                ([], None),
                            )
                            batch_rows.append(
                                {
                                    "chapter_index": bd.chapter_index,
                                    "chapter_title": bd.title,
                                    "saved_count": len(saved),
                                    "saved_case_ids": [c.case_id for c in saved],
                                    "coverage_notes": batch.coverage_notes if batch else "",
                                    "skipped": False,
                                    "batch_size": len(batch_decisions),
                                }
                            )

                    for batch_row in batch_rows:
                        await session.refresh(job)
                        if job.status == "cancelled":
                            await rollback_generated_cases(session, job_id)
                            log.info("prd.generation.job_cancelled")
                            return
                        job.saved_cases += int(batch_row["saved_count"])
                        results.append(batch_row)
                        job.results = list(results)
                        job.completed_chapters = len(results)
                        await session.commit()
                    i += len(batch_decisions)
                    continue
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
                    saved_count = 0
            await session.refresh(job)
            if job.status == "cancelled":
                await rollback_generated_cases(session, job_id)
                log.info("prd.generation.job_cancelled")
                return
            job.saved_cases += saved_count
            results.append(row)
            job.results = list(results)  # SQLAlchemy JSON column needs assignment to detect change
            job.completed_chapters = len(results)
            await session.commit()
            i += 1

        job.status = "done"
        job.finished_at = datetime.now(UTC)
        await session.commit()
        log.info(
            "prd.generation.job_done",
            saved_cases=job.saved_cases,
            chapters_processed=len(results),
        )


async def _preflight_llm_provider(
    prefer_provider: str | None, timeout_seconds: int = 20
) -> None:
    """Fail fast before the per-chapter loop if no LLM can answer.

    Without this, a dead CLI provider burns the full chapter timeout for every
    generation attempt, which looks like "0 cases saved" even though nothing is
    capable of producing output.
    """
    await get_gateway().chat(
        "Reply with exactly: ok",
        prompt_version="case_gen_preflight_v1",
        prefer=prefer_provider,
        fallback=prefer_provider is None,
        max_tokens=5,
        timeout_seconds=max(5, min(300, timeout_seconds)),
    )
