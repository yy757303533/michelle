"""Test case CRUD + review workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import TestCase
from app.obs import EVENTS, get_logger

router = APIRouter()
log = get_logger(__name__)

EDITABLE_FIELDS = {
    "name",
    "intent",
    "module",
    "tags",
    "priority",
    "preconditions",
    "steps",
    "assertions",
}


ReviewVerb = Literal["approve", "reject"]


class ReviewAction(BaseModel):
    action: ReviewVerb
    note: str = ""


class CaseEdit(BaseModel):
    """Partial edit of a case. Any field present overwrites + is added to
    manual_edited_fields, which protects it from future LLM regenerations.
    `extra='forbid'` rejects unknown fields up front so typos surface as 422
    rather than silently dropping the change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    intent: str | None = None
    module: str | None = None
    tags: list[str] | None = None
    priority: str | None = None
    preconditions: list[str] | None = None
    steps: list[dict[str, Any]] | None = None
    assertions: list[dict[str, Any]] | None = None


class BulkReview(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    action: ReviewVerb


@router.get("/")
async def list_cases(
    status: Literal["pending", "approved", "rejected", "stale"] | None = None,
    project_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
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
async def get_case(case_id: str, session: AsyncSession = Depends(get_session)) -> dict:
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
    row.review_status = "approved" if body.action == "approve" else "rejected"
    row.updated_at = datetime.now(UTC)
    await session.commit()
    log.info(
        EVENTS.REVIEW_CASE_ACTION.name,
        case_id=case_id,
        action=body.action,
        before_state=before,
        after_state=row.review_status,
        note=body.note[:500] if body.note else "",
    )
    return {"data": row.model_dump()}


@router.post("/bulk-review")
async def bulk_review(body: BulkReview, session: AsyncSession = Depends(get_session)) -> dict:
    """Apply approve/reject to many cases in one transaction."""
    target = "approved" if body.action == "approve" else "rejected"

    rows = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(body.case_ids))))
        .scalars()
        .all()
    )
    found_ids = {r.case_id for r in rows}
    missing = [cid for cid in body.case_ids if cid not in found_ids]

    now = datetime.now(UTC)
    updated: list[str] = []
    for r in rows:
        if r.review_status == target:
            continue
        before = r.review_status
        r.review_status = target
        r.updated_at = now
        updated.append(r.case_id)
        log.info(
            EVENTS.REVIEW_CASE_ACTION.name,
            case_id=r.case_id,
            action=body.action,
            before_state=before,
            after_state=target,
            via="bulk",
        )
    await session.commit()
    return {
        "data": {
            "updated": updated,
            "skipped_already_at_state": [
                cid for cid in body.case_ids if cid in found_ids and cid not in updated
            ],
            "missing": missing,
            "target_state": target,
        }
    }


@router.patch("/{case_id}")
async def edit_case(
    case_id: str,
    body: CaseEdit,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Partial edit. Each field set in the body is added to
    manual_edited_fields, which protects it from future LLM regenerations.

    Edits don't change review_status; the user re-reviews after editing.
    """
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")

    payload = body.model_dump(exclude_unset=True, exclude_none=True)
    if not payload:
        return {"data": row.model_dump(), "edited_fields": []}

    bad_fields = set(payload) - EDITABLE_FIELDS
    if bad_fields:
        raise HTTPException(status_code=400, detail=f"non-editable fields: {sorted(bad_fields)}")

    edited: list[str] = []
    for k, v in payload.items():
        if getattr(row, k) != v:
            setattr(row, k, v)
            edited.append(k)

    if edited:
        # dict.fromkeys preserves insertion order — set() would scramble the
        # field list across calls and make diffs harder to reason about.
        merged = list(dict.fromkeys([*row.manual_edited_fields, *edited]))
        row.manual_edited_fields = merged
        # Edits revert review back to pending if the case was already approved/rejected
        # so a human re-confirms the change. Plain field touch on a pending case stays pending.
        if row.review_status in {"approved", "rejected"}:
            row.review_status = "pending"
        row.updated_at = datetime.now(UTC)
        await session.commit()
        log.info(
            "case.edited",
            case_id=case_id,
            edited_fields=edited,
            now_pending=(row.review_status == "pending"),
        )

    return {"data": row.model_dump(), "edited_fields": edited}
