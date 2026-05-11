"""PRD upload + diff + case generation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, require_project_role
from app.db import get_session
from app.models import PRD, PRDGenerationJob, Project
from app.obs import EVENTS, get_logger
from app.runtime_config import (
    get_case_generation_parallelism,
    get_case_generation_preflight_timeout,
    get_case_generation_provider,
)
from app.services.prd_diff import diff_prds
from app.services.prd_generation_worker import (
    create_or_reuse_job,
    kick_off,
    rollback_generated_cases,
)
from app.services.prd_parser import parse_prd

router = APIRouter()
log = get_logger(__name__)


class PRDUploadIn(BaseModel):
    project_id: str
    name: str = ""
    markdown: str
    """Either provide markdown directly OR send a multipart upload (TODO Day 5)."""


class GenerateRequest(BaseModel):
    """Body for /api/prd/{prd_id}/generate."""

    chapter_indices: list[int] | None = None
    """If null → generate for all NEW + MODIFIED chapters (vs prev version)."""

    max_cases_per_chapter: int = Field(default=5, ge=1, le=50)
    generation_timeout_seconds: int = Field(default=180, ge=30, le=1800)
    """Per LLM batch timeout for case generation, not the preflight probe timeout."""
    prefer_provider: str | None = None
    parallelism: int | None = Field(default=None, ge=1, le=3)


@router.get("/")
async def list_prds(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(PRD).order_by(desc(PRD.uploaded_at)).limit(limit)
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(PRD.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": []}
            stmt = stmt.where(PRD.project_id.in_(allowed))
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "data": [
            {
                "prd_id": r.prd_id,
                "project_id": r.project_id,
                "name": r.name,
                "version": r.version,
                "chapter_count": len(r.chapters),
                "uploaded_at": r.uploaded_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/upload")
async def upload_prd(
    body: PRDUploadIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Parse + persist a PRD. If a same-named PRD exists, version-bump it.

    Auto-creates the Project row if missing.
    """
    proj = await session.get(Project, body.project_id)
    if proj is None:
        proj = Project(project_id=body.project_id, name=body.project_id)
        session.add(proj)
    await require_project_role(
        getattr(request.state, "user", None), body.project_id, "reviewer", session
    )

    parsed = parse_prd(body.markdown)

    # Find prior version (latest by uploaded_at) within same project + name
    prior_stmt = (
        select(PRD)
        .where(PRD.project_id == body.project_id)
        .where(PRD.name == (body.name or parsed.title))
        .order_by(desc(PRD.version))
        .limit(1)
    )
    prior = (await session.execute(prior_stmt)).scalars().first()

    diff_summary: dict[str, Any] | None = None
    if prior:
        from app.services.prd_parser import Chapter, ParsedPRD

        prior_chapters = [Chapter(**c) for c in prior.chapters]
        prior_parsed = ParsedPRD(
            title=prior.name,
            frontmatter="",
            preamble="",
            chapters=prior_chapters,
            raw_hash=prior.content_hash,
        )
        diff = diff_prds(prior_parsed, parsed)
        diff_summary = diff.summary()

    new_prd = PRD(
        prd_id=str(uuid4()),
        project_id=body.project_id,
        name=body.name or parsed.title,
        raw_markdown=body.markdown,
        content_hash=parsed.raw_hash,
        chapters=[c.to_dict() for c in parsed.chapters],
        version=(prior.version + 1) if prior else 1,
        prev_version_id=prior.prd_id if prior else None,
    )
    session.add(new_prd)
    await session.commit()
    await session.refresh(new_prd)

    log.info(
        EVENTS.PRD_UPLOADED.name,
        prd_id=new_prd.prd_id,
        project_id=body.project_id,
        chapter_count=len(parsed.chapters),
        hash=parsed.raw_hash[:12],
        version=new_prd.version,
    )
    if diff_summary:
        log.info(
            EVENTS.PRD_CHAPTER_DIFF.name,
            prd_id=new_prd.prd_id,
            **diff_summary,
        )

    return {
        "data": {
            "prd_id": new_prd.prd_id,
            "version": new_prd.version,
            "title": parsed.title,
            "chapters": [
                {
                    "position": c.position,
                    "level": c.level,
                    "title": c.title,
                    "normalized_title": c.normalized_title,
                    "hash": c.hash[:12],
                    "body_chars": len(c.body),
                }
                for c in parsed.chapters
            ],
            "prior_version_id": prior.prd_id if prior else None,
            "diff_summary": diff_summary,
        }
    }


@router.get("/{prd_id}")
async def get_prd(
    prd_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return PRD with the same compact chapter shape the upload endpoint
    emits, so the frontend can re-hydrate page state from a URL `?prd_id=`
    deep link without caring whether the data came from a fresh upload or
    from history."""
    row = await session.get(PRD, prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "viewer", session
    )
    chapters = [
        {
            "position": c.get("position"),
            "level": c.get("level"),
            "title": c.get("title"),
            "normalized_title": c.get("normalized_title"),
            "hash": (c.get("hash") or "")[:12],
            "body_chars": len(c.get("body") or ""),
            "body": c.get("body") or "",
        }
        for c in row.chapters
    ]
    return {
        "data": {
            "prd_id": row.prd_id,
            "project_id": row.project_id,
            "name": row.name,
            "title": row.name,
            "version": row.version,
            "uploaded_at": row.uploaded_at.isoformat(),
            "raw_markdown": row.raw_markdown,
            "chapters": chapters,
            "prior_version_id": row.prev_version_id,
            "diff_summary": None,
        }
    }


@router.delete("/{prd_id}", status_code=204)
async def delete_prd(
    prd_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a PRD record. Generated TestCases keep living — their
    `generated_from` is just a string label, not a FK, so cases survive
    on purpose: the user often wants to keep approved cases even after
    pruning duplicate uploads.

    Children that referenced this row via `prev_version_id` get the
    pointer cleared so the version chain doesn't dangle."""
    row = await session.get(PRD, prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    children = (
        (await session.execute(select(PRD).where(PRD.prev_version_id == prd_id))).scalars().all()
    )
    for c in children:
        c.prev_version_id = None
    await session.delete(row)
    await session.commit()
    log.info(
        "prd.deleted",
        prd_id=prd_id,
        project_id=row.project_id,
        children_unchained=len(children),
    )


@router.post("/{prd_id}/generate", status_code=202)
async def generate_cases(
    prd_id: str,
    body: GenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Kick off case generation in the background. Returns immediately
    with a job_id; client polls /api/prd/jobs/<job_id> for progress.

    Generation for a 90-chapter PRD is 7-45 minutes of sequential LLM
    calls — far past any reasonable HTTP timeout. The job row in
    `prd_generation_jobs` carries status + per-chapter results so a
    page reload mid-generation finds everything intact.
    """
    prd = await session.get(PRD, prd_id)
    if prd is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    proj = await session.get(Project, prd.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await require_project_role(
        getattr(request.state, "user", None), prd.project_id, "reviewer", session
    )

    indices = body.chapter_indices
    if indices is None:
        indices = list(range(len(prd.chapters)))
    indices = sorted({i for i in indices if 0 <= i < len(prd.chapters)})
    if not indices:
        raise HTTPException(status_code=400, detail="no valid chapter_indices")

    prefer_provider = body.prefer_provider or await get_case_generation_provider(session)
    preflight_timeout = await get_case_generation_preflight_timeout(session)
    parallelism = body.parallelism or await get_case_generation_parallelism(session)
    job_id, created = await create_or_reuse_job(
        prd_id=prd_id,
        project_id=prd.project_id,
        request_payload={
            "chapter_indices": indices,
            "max_cases_per_chapter": body.max_cases_per_chapter,
            "generation_timeout_seconds": body.generation_timeout_seconds,
            "prefer_provider": prefer_provider,
            "preflight_timeout_seconds": preflight_timeout,
            "parallelism": parallelism,
        },
        total_chapters=len(indices),
    )
    if created:
        kick_off(job_id)
        log.info("prd.generation.job_kicked_off", prd_id=prd_id, job_id=job_id)
    else:
        kick_off(job_id)
        log.info("prd.generation.job_reused", prd_id=prd_id, job_id=job_id)
    job = await session.get(PRDGenerationJob, job_id)
    return {
        "data": {
            "job_id": job_id,
            "prd_id": prd_id,
            "status": job.status if job else "pending",
            "total_chapters": job.total_chapters if job else len(indices),
            "reused": not created,
        }
    }


@router.get("/jobs/{job_id}")
async def get_generation_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Poll a generation job's status. Frontend uses 1-2s interval while
    `pending` / `running`, then stops once `done` / `failed`."""
    job = await session.get(PRDGenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await require_project_role(
        getattr(request.state, "user", None), job.project_id, "viewer", session
    )
    return {"data": job.model_dump()}


@router.post("/jobs/{job_id}/skip-current")
async def skip_current_generation_batch(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await session.get(PRDGenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await require_project_role(
        getattr(request.state, "user", None), job.project_id, "reviewer", session
    )
    if job.status in ("done", "failed", "cancelled"):
        return {"data": job.model_dump()}
    payload = dict(job.request_payload or {})
    progress = dict(payload.get("progress") or {})
    active_batches = [
        b for b in progress.get("active_batches") or [] if isinstance(b, dict)
    ]
    skip_ids = set(payload.get("skip_batch_ids") or [])
    for batch in active_batches:
        if batch.get("batch_id"):
            skip_ids.add(str(batch["batch_id"]))
    payload["skip_batch_ids"] = sorted(skip_ids)
    progress["skip_requested"] = True
    payload["progress"] = progress
    job.request_payload = payload
    await session.commit()
    await session.refresh(job)
    log.info("prd.generation.batch_skip_requested", prd_id=job.prd_id, job_id=job.job_id)
    return {"data": job.model_dump()}


@router.post("/jobs/{job_id}/cancel")
async def cancel_generation_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await session.get(PRDGenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await require_project_role(
        getattr(request.state, "user", None), job.project_id, "reviewer", session
    )
    if job.status in ("done", "failed", "cancelled"):
        return {"data": job.model_dump()}
    job.status = "cancelled"
    job.error = "cancelled by user"
    job.finished_at = datetime.now(UTC)
    await session.commit()
    rolled_back = await rollback_generated_cases(session, job.job_id)
    job = await session.get(PRDGenerationJob, job_id)
    if job is not None:
        job.error = f"cancelled by user; rolled back {rolled_back} generated cases"
        await session.commit()
        await session.refresh(job)
    else:
        raise HTTPException(status_code=404, detail="job not found")
    log.info("prd.generation.job_cancelled", prd_id=job.prd_id, job_id=job.job_id)
    return {"data": job.model_dump()}


@router.get("/{prd_id}/jobs")
async def list_jobs_for_prd(
    prd_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """All generation jobs for one PRD, newest first. Used to surface
    "currently generating…" / "last generation finished N min ago"
    on the PRD detail UI."""
    prd = await session.get(PRD, prd_id)
    if prd is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    await require_project_role(
        getattr(request.state, "user", None), prd.project_id, "viewer", session
    )
    rows = (
        (
            await session.execute(
                select(PRDGenerationJob)
                .where(PRDGenerationJob.prd_id == prd_id)
                .order_by(desc(PRDGenerationJob.created_at))
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"data": [r.model_dump() for r in rows]}
