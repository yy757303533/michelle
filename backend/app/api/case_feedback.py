"""Case generation feedback endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import CaseGenerationFeedback, TestCase

router = APIRouter()

FeedbackCategory = Literal[
    "prompt_rule_missing",
    "prd_context_missing",
    "hallucinated_requirement",
    "missed_requirement",
    "wrong_auth_state",
    "not_browser_executable",
    "duplicate_or_low_value",
    "executor_limitation",
]


class FeedbackCreate(BaseModel):
    case_id: str = Field(min_length=1)
    category: FeedbackCategory
    note: str = ""
    evidence: str = ""


class FeedbackResolve(BaseModel):
    status: Literal["open", "resolved"] = "resolved"
    resolved_by_commit: str | None = None


@router.get("/")
async def list_case_generation_feedback(
    request: Request,
    project_id: str | None = None,
    status: Literal["open", "resolved"] | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(CaseGenerationFeedback).order_by(desc(CaseGenerationFeedback.created_at))
    summary_stmt = (
        select(CaseGenerationFeedback.category, CaseGenerationFeedback.status, func.count())
        .group_by(CaseGenerationFeedback.category, CaseGenerationFeedback.status)
    )
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(CaseGenerationFeedback.project_id == project_id)
        summary_stmt = summary_stmt.where(CaseGenerationFeedback.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": [], "count": 0, "summary": []}
            stmt = stmt.where(CaseGenerationFeedback.project_id.in_(allowed))
            summary_stmt = summary_stmt.where(CaseGenerationFeedback.project_id.in_(allowed))
    if status:
        stmt = stmt.where(CaseGenerationFeedback.status == status)
    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    summary_rows = (await session.execute(summary_stmt)).all()
    return {
        "data": [r.model_dump() for r in rows],
        "count": len(rows),
        "summary": [
            {"category": category, "status": row_status, "count": count}
            for category, row_status, count in summary_rows
        ],
    }


@router.post("/", status_code=201)
async def create_case_generation_feedback(
    body: FeedbackCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    case = await session.get(TestCase, body.case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    await require_project_role(
        getattr(request.state, "user", None), case.project_id, "reviewer", session
    )
    row = CaseGenerationFeedback(
        case_id=case.case_id,
        project_id=case.project_id,
        generated_from=case.generated_from,
        generation_job_id=case.generation_job_id,
        category=body.category,
        note=body.note[:2000],
        evidence=body.evidence[:2000],
        extra={
            "case_name": case.name,
            "prompt_version": case.prompt_version,
            "model_version": case.model_version,
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await audit(
        actor=getattr(request.state, "user", None),
        action="case_generation_feedback.created",
        method=request.method,
        path=request.url.path,
        status_code=201,
        target_type="case",
        target_id=case.case_id,
        detail=f"category={body.category}",
        session=session,
    )
    await session.commit()
    return {"data": row.model_dump()}


@router.patch("/{feedback_id}")
async def update_case_generation_feedback(
    feedback_id: str,
    body: FeedbackResolve,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(CaseGenerationFeedback, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    row.status = body.status
    row.resolved_by_commit = body.resolved_by_commit
    row.resolved_at = datetime.now(UTC) if body.status == "resolved" else None
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}
