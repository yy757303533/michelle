"""Test case CRUD + review workflow."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import TestCase
from app.obs import EVENTS, get_logger

router = APIRouter()
log = get_logger(__name__)


class ReviewAction(BaseModel):
    action: str  # approve | reject
    note: str = ""


@router.get("/")
async def list_cases(
    status: str | None = None,
    project_id: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(TestCase).order_by(desc(TestCase.created_at)).limit(limit)
    if status:
        stmt = stmt.where(TestCase.review_status == status)
    if project_id:
        stmt = stmt.where(TestCase.project_id == project_id)
    rows = (await session.execute(stmt)).scalars().all()

    counts_stmt = select(TestCase.review_status, func.count()).group_by(TestCase.review_status)
    if project_id:
        counts_stmt = counts_stmt.where(TestCase.project_id == project_id)
    counts_rows = (await session.execute(counts_stmt)).all()
    counts = {row[0]: row[1] for row in counts_rows}

    return {
        "data": [r.model_dump() for r in rows],
        "count": len(rows),
        "counts_by_status": counts,
    }


@router.get("/{case_id}")
async def get_case(
    case_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return {"data": row.model_dump()}


@router.post("/{case_id}/review")
async def review_case(
    case_id: str,
    body: ReviewAction,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")

    before = row.review_status
    if body.action == "approve":
        row.review_status = "approved"
    elif body.action == "reject":
        row.review_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")

    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    log.info(
        EVENTS.REVIEW_CASE_ACTION.name,
        case_id=case_id,
        action=body.action,
        before_state=before,
        after_state=row.review_status,
    )
    return {"data": row.model_dump()}
