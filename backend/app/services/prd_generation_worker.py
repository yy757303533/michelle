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
import time
from dataclasses import dataclass
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
    GeneratedBatch,
    estimate_target_cases,
    generate_batches_for_chapters,
    is_actionable_chapter,
    persist_generated_batch,
)
from app.services.case_versioning import (
    find_prev_prd,
    mark_stale_for_removed_chapters,
    plan_regeneration,
)
from app.services.prd_parser import Chapter

_log = get_logger(__name__)
_TASKS: dict[str, asyncio.Task] = {}


@dataclass(frozen=True)
class _WorkBatch:
    batch_id: str
    decisions: list[Any]
    chapters: list[Chapter]
    target_cases: int


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
    """Fire-and-forget the worker, de-duped within this backend process.

    Uvicorn reloads and process crashes erase in-memory tasks while the job row
    remains `running`. Calling `kick_off` for an already-active DB row is
    therefore intentional: if this process owns a task it is reused; otherwise
    the worker resumes from persisted progress.
    """
    existing = _TASKS.get(job_id)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_run_job_guarded(job_id))
    _TASKS[job_id] = task
    task.add_done_callback(lambda _task: _TASKS.pop(job_id, None))
    return task


async def _run_job_guarded(job_id: str) -> None:
    try:
        await run_job(job_id)
    except Exception as exc:  # noqa: BLE001
        _log.exception("prd.generation.worker_crashed", job_id=job_id, error=str(exc)[:200])
        async with _db.async_session_maker() as session:
            job = await session.get(PRDGenerationJob, job_id)
            if job is not None and job.status in ("pending", "running"):
                job.status = "failed"
                job.error = f"worker crashed: {type(exc).__name__}: {str(exc)[:300]}"
                job.finished_at = datetime.now(UTC)
                await session.commit()


async def resume_active_jobs() -> int:
    """Resume pending/running generation jobs after backend startup/reload."""
    async with _db.async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(PRDGenerationJob)
                    .where(PRDGenerationJob.status.in_(["pending", "running"]))
                    .order_by(PRDGenerationJob.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    for job in rows:
        kick_off(job.job_id)
    return len(rows)


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
        if job.status in ("done", "failed", "cancelled"):
            return
        existing_results: list[dict[str, Any]] = list(job.results or [])
        completed_indices = {
            int(row["chapter_index"])
            for row in existing_results
            if isinstance(row, dict) and "chapter_index" in row
        }
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
        generation_timeout: int = max(
            30, min(1800, int(body.get("generation_timeout_seconds", 180)))
        )

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
        if job.started_at is None:
            job.started_at = datetime.now(UTC)
        job.completed_chapters = len(existing_results)
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

        results: list[dict[str, Any]] = existing_results
        work_batches: list[_WorkBatch] = []
        i = 0
        requested_batch_size = int(body.get("batch_chapter_count", 4))
        parallelism = max(1, min(3, int(body.get("parallelism", 1))))
        while i < len(decisions):
            d = decisions[i]
            await session.refresh(job)
            if job.status == "cancelled":
                await rollback_generated_cases(session, job_id)
                log.info("prd.generation.job_cancelled")
                return
            if d.chapter_index in completed_indices:
                i += 1
                continue
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
                await _append_result_row(session, job, results, completed_indices, row)
                i += 1
                continue
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
                await _append_result_row(session, job, results, completed_indices, row)
                i += 1
                continue
            else:
                batch_decisions = [d]
                batch_targets = estimate_target_cases(chap, max_cases=max_cases)
                dynamic_limit = _dynamic_batch_limit(
                    chap,
                    requested_batch_size=requested_batch_size,
                    max_cases=max_cases,
                )
                j = i + 1
                while j < len(decisions) and len(batch_decisions) < dynamic_limit:
                    nd = decisions[j]
                    if nd.chapter_index in completed_indices:
                        j += 1
                        continue
                    nch = Chapter(**prd.chapters[nd.chapter_index])
                    if nd.action != "regenerate" or not is_actionable_chapter(nch):
                        break
                    next_target = estimate_target_cases(nch, max_cases=max_cases)
                    if batch_targets + next_target > max(4, max_cases):
                        break
                    batch_decisions.append(nd)
                    batch_targets += next_target
                    dynamic_limit = min(
                        dynamic_limit,
                        _dynamic_batch_limit(
                            nch,
                            requested_batch_size=requested_batch_size,
                            max_cases=max_cases,
                        ),
                    )
                    j += 1
                chapter_objs = [Chapter(**prd.chapters[bd.chapter_index]) for bd in batch_decisions]
                work_batches.append(
                    _WorkBatch(
                        batch_id=_batch_id(batch_decisions),
                        decisions=batch_decisions,
                        chapters=chapter_objs,
                        target_cases=batch_targets,
                    )
                )
                i += len(batch_decisions)

        await _run_generation_batches(
            session=session,
            job=job,
            project=proj,
            work_batches=work_batches,
            results=results,
            completed_indices=completed_indices,
            max_cases=max_cases,
            prefer_provider=prefer_provider,
            generation_timeout=generation_timeout,
            parallelism=parallelism,
            log=log,
        )
        await session.refresh(job)
        if job.status == "cancelled":
            await rollback_generated_cases(session, job_id)
            log.info("prd.generation.job_cancelled")
            return

        job.status = "done"
        job.finished_at = datetime.now(UTC)
        payload = dict(job.request_payload or {})
        progress = dict(payload.get("progress") or {})
        progress["active_batches"] = []
        progress["eta_seconds"] = 0
        progress["skip_requested"] = False
        payload["progress"] = progress
        job.request_payload = payload
        await session.commit()
        log.info(
            "prd.generation.job_done",
            saved_cases=job.saved_cases,
            chapters_processed=len(results),
        )


def _batch_id(decisions: list[Any]) -> str:
    indices = [int(d.chapter_index) for d in decisions]
    return f"b{indices[0]}-{indices[-1]}"


def _dynamic_batch_limit(
    chapter: Chapter, *, requested_batch_size: int, max_cases: int
) -> int:
    """Keep large/dense chapters out of oversized LLM prompts."""
    requested = max(1, min(8, requested_batch_size))
    target = estimate_target_cases(chapter, max_cases=max_cases)
    body_chars = len((chapter.body or "").strip())
    if body_chars >= 1200 or target >= 5:
        return 1
    if body_chars >= 500 or target >= 4:
        return min(requested, 2)
    return min(requested, 4)


async def _append_result_row(
    session,
    job: PRDGenerationJob,
    results: list[dict[str, Any]],
    completed_indices: set[int],
    row: dict[str, Any],
) -> None:
    job.saved_cases += int(row.get("saved_count") or 0)
    results.append(row)
    completed_indices.add(int(row["chapter_index"]))
    job.results = list(results)
    job.completed_chapters = len(results)
    await session.commit()


async def _run_generation_batches(
    *,
    session,
    job: PRDGenerationJob,
    project: Project,
    work_batches: list[_WorkBatch],
    results: list[dict[str, Any]],
    completed_indices: set[int],
    max_cases: int,
    prefer_provider: str | None,
    generation_timeout: int,
    parallelism: int,
    log,
) -> None:
    if not work_batches:
        return

    started_at_by_batch: dict[str, float] = {}
    latencies: list[float] = []
    pending = list(work_batches)
    active: dict[asyncio.Task, _WorkBatch] = {}
    completed_batches = 0

    async def launch(batch: _WorkBatch) -> None:
        started_at_by_batch[batch.batch_id] = time.monotonic()
        await _update_progress(
            session=session,
            job=job,
            active_batches=list(active.values()) + [batch],
            completed_batches=completed_batches,
            total_batches=len(work_batches),
            latencies=latencies,
            parallelism=parallelism,
        )
        task = asyncio.create_task(
            generate_batches_for_chapters(
                project_name=project.name,
                base_url=project.base_url,
                chapters=batch.chapters,
                max_cases=max_cases,
                prefer_provider=prefer_provider,
                default_username=project.default_username or None,
                default_password=project.default_password or None,
                login_url=project.login_url or None,
                generation_timeout_seconds=generation_timeout,
            )
        )
        active[task] = batch

    while pending or active:
        await session.refresh(job)
        if job.status == "cancelled":
            return
        while pending and len(active) < parallelism:
            await launch(pending.pop(0))
        if not active:
            continue
        done, _ = await asyncio.wait(active.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            batch = active.pop(task)
            latency = time.monotonic() - started_at_by_batch.get(batch.batch_id, time.monotonic())
            latencies.append(latency)
            completed_batches += 1
            await session.refresh(job)
            if job.status == "cancelled":
                return
            skip_ids = set((job.request_payload or {}).get("skip_batch_ids") or [])
            if batch.batch_id in skip_ids:
                await _append_skipped_batch(
                    session=session,
                    job=job,
                    batch=batch,
                    results=results,
                    completed_indices=completed_indices,
                    reason="skipped by user",
                    action="user_skip",
                )
                await _update_progress(
                    session=session,
                    job=job,
                    active_batches=list(active.values()),
                    completed_batches=completed_batches,
                    total_batches=len(work_batches),
                    latencies=latencies,
                    parallelism=parallelism,
                    last_batch=batch,
                    last_latency=latency,
                    last_status="skipped",
                )
                continue

            try:
                generated: list[tuple[Chapter, GeneratedBatch, str]] = task.result()
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "prd.generation.batch_failed",
                    batch_id=batch.batch_id,
                    chapters=[d.chapter_index for d in batch.decisions],
                    error=str(exc)[:200],
                )
                await _append_error_batch(
                    session=session,
                    job=job,
                    batch=batch,
                    results=results,
                    completed_indices=completed_indices,
                    error=str(exc)[:200],
                )
                await _update_progress(
                    session=session,
                    job=job,
                    active_batches=list(active.values()),
                    completed_batches=completed_batches,
                    total_batches=len(work_batches),
                    latencies=latencies,
                    parallelism=parallelism,
                    last_batch=batch,
                    last_latency=latency,
                    last_status="error",
                    last_error=str(exc)[:200],
                )
                continue

            generated_by_id = {
                f"{chapter.level}:{chapter.normalized_title}": (batch_result, model)
                for chapter, batch_result, model in generated
            }
            for decision, chapter in zip(batch.decisions, batch.chapters, strict=True):
                batch_result, model = generated_by_id.get(
                    f"{chapter.level}:{chapter.normalized_title}",
                    (GeneratedBatch(coverage_notes="", cases=[]), "unknown"),
                )
                saved = await persist_generated_batch(
                    session=session,
                    project_id=project.project_id,
                    chapter=chapter,
                    batch=batch_result,
                    model=model,
                    generation_job_id=job.job_id,
                )
                row = {
                    "chapter_index": decision.chapter_index,
                    "chapter_title": decision.title,
                    "saved_count": len(saved),
                    "saved_case_ids": [c.case_id for c in saved],
                    "coverage_notes": batch_result.coverage_notes,
                    "skipped": False,
                    "batch_id": batch.batch_id,
                    "batch_size": len(batch.decisions),
                    "batch_latency_seconds": round(latency, 1),
                }
                await _append_result_row(session, job, results, completed_indices, row)
            await _update_progress(
                session=session,
                job=job,
                active_batches=list(active.values()),
                completed_batches=completed_batches,
                total_batches=len(work_batches),
                latencies=latencies,
                parallelism=parallelism,
                last_batch=batch,
                last_latency=latency,
                last_status="done",
            )


async def _append_skipped_batch(
    *,
    session,
    job: PRDGenerationJob,
    batch: _WorkBatch,
    results: list[dict[str, Any]],
    completed_indices: set[int],
    reason: str,
    action: str,
) -> None:
    for decision in batch.decisions:
        await _append_result_row(
            session,
            job,
            results,
            completed_indices,
            {
                "chapter_index": decision.chapter_index,
                "chapter_title": decision.title,
                "saved_count": 0,
                "skipped": True,
                "skip_reason": reason,
                "skip_action": action,
                "batch_id": batch.batch_id,
            },
        )


async def _append_error_batch(
    *,
    session,
    job: PRDGenerationJob,
    batch: _WorkBatch,
    results: list[dict[str, Any]],
    completed_indices: set[int],
    error: str,
) -> None:
    for decision in batch.decisions:
        await _append_result_row(
            session,
            job,
            results,
            completed_indices,
            {
                "chapter_index": decision.chapter_index,
                "chapter_title": decision.title,
                "saved_count": 0,
                "skipped": False,
                "error": error,
                "batch_id": batch.batch_id,
            },
        )


async def _update_progress(
    *,
    session,
    job: PRDGenerationJob,
    active_batches: list[_WorkBatch],
    completed_batches: int,
    total_batches: int,
    latencies: list[float],
    parallelism: int,
    last_batch: _WorkBatch | None = None,
    last_latency: float | None = None,
    last_status: str | None = None,
    last_error: str | None = None,
) -> None:
    payload = dict(job.request_payload or {})
    progress = dict(payload.get("progress") or {})
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    remaining_batches = max(total_batches - completed_batches - len(active_batches), 0)
    eta = None
    if avg_latency is not None:
        eta = round((remaining_batches / max(1, parallelism)) * avg_latency)
    progress.update(
        {
            "active_batches": [_batch_progress_dict(batch) for batch in active_batches],
            "completed_batches": completed_batches,
            "total_batches": total_batches,
            "parallelism": parallelism,
            "avg_batch_latency_seconds": round(avg_latency, 1) if avg_latency else None,
            "eta_seconds": eta,
        }
    )
    if last_batch is not None:
        progress["last_batch"] = {
            **_batch_progress_dict(last_batch),
            "status": last_status,
            "latency_seconds": round(last_latency or 0, 1),
            "error": last_error,
        }
    skip_ids = set(payload.get("skip_batch_ids") or [])
    progress["skip_requested"] = any(batch.batch_id in skip_ids for batch in active_batches)
    payload["progress"] = progress
    job.request_payload = payload
    await session.commit()


def _batch_progress_dict(batch: _WorkBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "chapter_indices": [int(d.chapter_index) for d in batch.decisions],
        "chapter_titles": [str(d.title) for d in batch.decisions],
        "target_cases": batch.target_cases,
    }


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
