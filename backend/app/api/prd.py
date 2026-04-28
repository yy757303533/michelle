"""PRD upload + diff + case generation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import PRD, Project
from app.obs import EVENTS, get_logger
from app.services.case_generator import generate_cases_for_chapter
from app.services.prd_diff import diff_prds
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

    max_cases_per_chapter: int = Field(default=8, ge=1, le=50)
    prefer_provider: str | None = None


@router.get("/")
async def list_prds(
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(PRD).order_by(desc(PRD.uploaded_at)).limit(limit)
    if project_id:
        stmt = stmt.where(PRD.project_id == project_id)
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
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Parse + persist a PRD. If a same-named PRD exists, version-bump it.

    Auto-creates the Project row if missing.
    """
    proj = await session.get(Project, body.project_id)
    if proj is None:
        proj = Project(project_id=body.project_id, name=body.project_id)
        session.add(proj)

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
async def get_prd(prd_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(PRD, prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    return {
        "data": {
            "prd_id": row.prd_id,
            "project_id": row.project_id,
            "name": row.name,
            "version": row.version,
            "uploaded_at": row.uploaded_at.isoformat(),
            "chapters": row.chapters,
        }
    }


@router.post("/{prd_id}/generate")
async def generate_cases(
    prd_id: str,
    body: GenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Diff-aware case regeneration.

    For each requested chapter:
      - approved cases exist  → skip (never auto-overwrite human review)
      - chapter unchanged vs prev PRD → skip (no LLM call)
      - otherwise → call LLM and persist new cases

    Also marks cases from removed chapters as `review_status="stale"` so the
    review UI can surface them for human decision.
    """
    prd = await session.get(PRD, prd_id)
    if prd is None:
        raise HTTPException(status_code=404, detail="PRD not found")
    proj = await session.get(Project, prd.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.case_versioning import (
        find_prev_prd,
        mark_stale_for_removed_chapters,
        plan_regeneration,
    )
    from app.services.prd_parser import Chapter

    indices = body.chapter_indices
    if indices is None:
        indices = list(range(len(prd.chapters)))
    # dedupe + clamp to valid range. Without dedupe, callers passing
    # `[1,1,2]` would double-process chapter 1.
    indices = sorted({i for i in indices if 0 <= i < len(prd.chapters)})
    if not indices:
        return {"data": {"prd_id": prd_id, "results": [], "total_cases": 0}}

    prev_prd = await find_prev_prd(session, prd)
    stale_marked = await mark_stale_for_removed_chapters(
        session=session, new_prd=prd, prev_prd=prev_prd
    )
    # If every chapter ends up skipped, no later commit will fire — flush the
    # stale-marked rows now so they aren't rolled back when the request ends.
    if stale_marked:
        await session.commit()

    decisions = await plan_regeneration(
        session=session, new_prd=prd, prev_prd=prev_prd, chapter_indices=indices
    )

    results: list[dict[str, Any]] = []
    total = 0
    for d in decisions:
        chap = Chapter(**prd.chapters[d.chapter_index])

        if d.action != "regenerate":
            results.append(
                {
                    "chapter_index": d.chapter_index,
                    "chapter_title": d.title,
                    "saved_count": 0,
                    "skipped": True,
                    "skip_reason": d.reason,
                    "skip_action": d.action,
                    "existing_case_ids": d.existing_case_ids,
                }
            )
            log.info(
                "prd.generation.chapter_skipped",
                prd_id=prd_id,
                chapter_index=d.chapter_index,
                action=d.action,
                reason=d.reason,
            )
            continue

        try:
            saved, batch = await generate_cases_for_chapter(
                project_id=prd.project_id,
                project_name=proj.name,
                base_url=proj.base_url,
                chapter=chap,
                session=session,
                max_cases=body.max_cases_per_chapter,
                prefer_provider=body.prefer_provider,
            )
            results.append(
                {
                    "chapter_index": d.chapter_index,
                    "chapter_title": d.title,
                    "saved_count": len(saved),
                    "saved_case_ids": [c.case_id for c in saved],
                    "coverage_notes": batch.coverage_notes,
                    "skipped": False,
                }
            )
            total += len(saved)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "case.generation.failed",
                chapter_index=d.chapter_index,
                title=d.title,
                error=str(exc)[:200],
            )
            results.append(
                {
                    "chapter_index": d.chapter_index,
                    "chapter_title": d.title,
                    "saved_count": 0,
                    "skipped": False,
                    "error": str(exc)[:200],
                }
            )

    log.info(
        "prd.generation.batch_complete",
        prd_id=prd_id,
        chapters_processed=len(indices),
        chapters_regenerated=sum(
            1 for r in results if not r.get("skipped") and r.get("saved_count", 0) > 0
        ),
        chapters_skipped=sum(1 for r in results if r.get("skipped")),
        total_cases=total,
        stale_marked=len(stale_marked),
    )
    return {
        "data": {
            "prd_id": prd_id,
            "results": results,
            "total_cases": total,
            "stale_marked_case_ids": stale_marked,
            "uploaded_at": datetime.now(UTC).isoformat(),
        }
    }
