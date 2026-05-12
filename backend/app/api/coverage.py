"""Coverage review and case drafting API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, require_project_role
from app.db import get_session
from app.models import CoverageItem
from app.services.case_drafter import draft_case_from_coverage_item

router = APIRouter()


class CoverageReviewIn(BaseModel):
    action: Literal["accept", "reject", "reset"]


@router.get("/")
async def list_coverage(
    request: Request,
    project_id: str | None = None,
    prd_id: str | None = None,
    status: Literal["proposed", "accepted", "rejected", "stale"] | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(CoverageItem).order_by(desc(CoverageItem.created_at)).limit(limit)
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(CoverageItem.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": [], "count": 0}
            stmt = stmt.where(CoverageItem.project_id.in_(allowed))
    if prd_id:
        stmt = stmt.where(CoverageItem.prd_id == prd_id)
    if status:
        stmt = stmt.where(CoverageItem.review_status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [row.model_dump() for row in rows], "count": len(rows)}


@router.post("/{coverage_id}/review")
async def review_coverage(
    coverage_id: str,
    body: CoverageReviewIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(CoverageItem, coverage_id)
    if row is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )

    row.review_status = {
        "accept": "accepted",
        "reject": "rejected",
        "reset": "proposed",
    }[body.action]
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}


@router.post("/{coverage_id}/draft-case", status_code=201)
async def draft_case_from_coverage(
    coverage_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    coverage = await session.get(CoverageItem, coverage_id)
    if coverage is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    await require_project_role(
        getattr(request.state, "user", None), coverage.project_id, "reviewer", session
    )
    try:
        case, reused = await draft_case_from_coverage_item(coverage=coverage, session=session)
    except ValueError as exc:
        status = 409 if "accepted" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"data": case.model_dump(), "reused": reused}
