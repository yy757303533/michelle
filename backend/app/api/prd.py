"""PRD upload, diff, and coverage analysis endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import PRD, CoverageItem, DesignGenerationJob, Project
from app.obs import EVENTS, get_logger
from app.services.prd_diff import diff_prds
from app.services.prd_parser import parse_prd

router = APIRouter()
log = get_logger(__name__)


class PRDUploadIn(BaseModel):
    project_id: str
    name: str = ""
    markdown: str
    """Either provide markdown directly OR send a multipart upload (TODO Day 5)."""


class AnalyzeRequest(BaseModel):
    """Body for /api/prd/{prd_id}/analyze."""

    chapter_indices: list[int] | None = None
    prefer_provider: str | None = None
    output_language: Literal["auto", "zh", "en"] = "auto"
    replace_unreviewed: bool = False


def _actor_id(request: Request) -> str:
    return str((getattr(request.state, "user", None) or {}).get("sub", ""))


@router.get("/")
async def list_prds(
    request: Request,
    project_id: str | None = None,
    deleted: Literal["active", "deleted", "all"] = "active",
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
    if deleted == "active":
        stmt = stmt.where(PRD.deleted_at.is_(None))
    elif deleted == "deleted":
        stmt = stmt.where(PRD.deleted_at.is_not(None))
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
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
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
            "raw_markdown": new_prd.raw_markdown,
            "chapters": [
                {
                    "position": c.position,
                    "level": c.level,
                    "title": c.title,
                    "normalized_title": c.normalized_title,
                    "hash": c.hash[:12],
                    "body_chars": len(c.body),
                    "body": c.body,
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
            "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
            "raw_markdown": row.raw_markdown,
            "chapters": chapters,
            "prior_version_id": row.prev_version_id,
            "diff_summary": None,
        }
    }


@router.post("/{prd_id}/analyze")
async def analyze_prd(
    prd_id: str,
    body: AnalyzeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    prd = await session.get(PRD, prd_id)
    if prd is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    if prd.deleted_at is not None:
        raise HTTPException(status_code=409, detail="restore PRD before analyzing coverage")
    await require_project_role(
        getattr(request.state, "user", None), prd.project_id, "reviewer", session
    )

    from app.runtime_config import get_test_design_provider
    from app.services.test_design_planner import analyze_prd_chapters

    replaced_count = 0
    if body.replace_unreviewed:
        now = datetime.now(UTC)
        replace_stmt = (
            select(CoverageItem)
            .where(CoverageItem.prd_id == prd_id)
            .where(CoverageItem.deleted_at.is_(None))
            .where(CoverageItem.linked_case_id.is_(None))
            .where(CoverageItem.review_status.in_(["proposed", "rejected", "stale"]))
        )
        if body.chapter_indices is not None:
            replace_stmt = replace_stmt.where(CoverageItem.chapter_index.in_(body.chapter_indices))
        replace_rows = (await session.execute(replace_stmt)).scalars().all()
        for row in replace_rows:
            row.deleted_at = now
            row.deleted_by = _actor_id(request)
            row.delete_reason = "regenerate_coverage"
            row.updated_at = now
        replaced_count = len(replace_rows)

    result = await analyze_prd_chapters(
        session=session,
        prd=prd,
        chapter_indices=body.chapter_indices,
        prefer_provider=body.prefer_provider or await get_test_design_provider(session) or "auto",
        output_language=body.output_language,
    )
    return {
        "data": {
            "prd_id": prd.prd_id,
            "project_id": prd.project_id,
            "requirements_created": len(result.requirements),
            "coverage_created": len(result.coverage),
            "coverage_replaced": replaced_count,
            "requirement_ids": [row.requirement_id for row in result.requirements],
            "coverage_ids": [row.coverage_id for row in result.coverage],
        }
    }


@router.delete("/{prd_id}", status_code=204)
async def delete_prd(
    prd_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete a PRD record. Generated coverage/cases/runs stay available
    as downstream evidence; lists hide the PRD by default until restored."""
    row = await session.get(PRD, prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    row.deleted_at = datetime.now(UTC)
    row.deleted_by = _actor_id(request)
    row.delete_reason = "manual"
    await audit(
        actor=getattr(request.state, "user", None),
        action="prd.soft_deleted",
        method=request.method,
        path=request.url.path,
        status_code=204,
        target_type="prd",
        target_id=prd_id,
        detail=f"project_id={row.project_id}",
        session=session,
    )
    await session.commit()
    log.info(
        "prd.soft_deleted",
        prd_id=prd_id,
        project_id=row.project_id,
    )


@router.post("/{prd_id}/restore")
async def restore_prd(
    prd_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(PRD, prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    row.deleted_at = None
    row.deleted_by = ""
    row.delete_reason = ""
    await audit(
        actor=getattr(request.state, "user", None),
        action="prd.restored",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="prd",
        target_id=prd_id,
        session=session,
    )
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}


@router.get("/jobs/{job_id}")
async def get_generation_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Poll a generation job's status. Frontend uses 1-2s interval while
    `pending` / `running`, then stops once `done` / `failed`."""
    job = await session.get(DesignGenerationJob, job_id)
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
    job = await session.get(DesignGenerationJob, job_id)
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
    log.info("design.generation.batch_skip_requested", prd_id=job.prd_id, job_id=job.job_id)
    return {"data": job.model_dump()}


@router.post("/jobs/{job_id}/cancel")
async def cancel_generation_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await session.get(DesignGenerationJob, job_id)
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
    await session.refresh(job)
    log.info("design.generation.job_cancelled", prd_id=job.prd_id, job_id=job.job_id)
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
                select(DesignGenerationJob)
                .where(DesignGenerationJob.prd_id == prd_id)
                .order_by(desc(DesignGenerationJob.created_at))
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"data": [r.model_dump() for r in rows]}
