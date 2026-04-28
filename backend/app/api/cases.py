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
from app.models import Project, TestCase
from app.obs import EVENTS, get_logger
from app.services.case_generator import _mint_case_id, _next_seq

router = APIRouter()
log = get_logger(__name__)

EDITABLE_FIELDS = {
    "name",
    "intent",
    "module",
    "tags",
    "priority",
    "auth_state",
    "preconditions",
    "steps",
    "assertions",
}


ReviewVerb = Literal["approve", "reject", "reset"]
_VERB_TO_STATE: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "reset": "pending",
}
AuthState = Literal["logged-in", "logged-out", "wrong-creds", "public"]


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
    auth_state: AuthState | None = None
    preconditions: list[str] | None = None
    steps: list[dict[str, Any]] | None = None
    assertions: list[dict[str, Any]] | None = None


class CaseCreate(BaseModel):
    """Manually-authored test case. Goes straight to `pending` review so a
    human still confirms it before the platform runs it."""

    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    intent: str = ""
    module: str = ""
    tags: list[str] = Field(default_factory=list)
    priority: Literal["P0", "P1", "P2"] = "P1"
    auth_state: AuthState = "logged-in"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)


class BulkReview(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    action: ReviewVerb


class BulkDelete(BaseModel):
    case_ids: list[str] = Field(min_length=1, max_length=500)


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


@router.post("/", status_code=201)
async def create_case(
    body: CaseCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Hand-author a case. Lands in `pending` so it still goes through the
    same review gate AI-generated cases do."""
    proj = await session.get(Project, body.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"project {body.project_id} not found")

    seq = await _next_seq(session, body.project_id)
    case_id = _mint_case_id(seq)
    row = TestCase(
        case_id=case_id,
        project_id=body.project_id,
        name=body.name[:200],
        intent=body.intent[:1000],
        module=body.module[:60],
        tags=body.tags,
        priority=body.priority,
        auth_state=body.auth_state,
        preconditions=body.preconditions,
        steps=body.steps,
        assertions=body.assertions,
        source="manual",
        prompt_version="manual",
        model_version="manual",
        generated_from=None,
        review_status="pending",
        version=1,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    log.info("case.created.manual", case_id=case_id, project_id=body.project_id)
    return {"data": row.model_dump()}


@router.delete("/{case_id}", status_code=204)
async def delete_case(case_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Hard-delete a case. Approved cases are protected — the contract is
    that approved == human-confirmed, and one accidental click shouldn't
    erase that signal. Reject the case first if you really need it gone."""
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    if row.review_status == "approved":
        raise HTTPException(
            status_code=409,
            detail="approved cases cannot be deleted; reject it first if you really mean to remove",
        )
    prior = row.review_status
    await session.delete(row)
    await session.commit()
    log.info("case.deleted", case_id=case_id, prior_status=prior)


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
    row.review_status = _VERB_TO_STATE[body.action]
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
    """Apply approve/reject/reset to many cases in one transaction.

    `reset` reverts approved/rejected cases back to `pending` — useful when
    a reviewer wants to take back a verdict without having to edit the case
    body. (Edits already auto-reset, but that's a heavier action.)"""
    target = _VERB_TO_STATE[body.action]

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


@router.post("/bulk-delete")
async def bulk_delete(body: BulkDelete, session: AsyncSession = Depends(get_session)) -> dict:
    """Delete many cases in one transaction. Approved cases are skipped
    (same guard as DELETE /<id>) and surfaced in the response so the UI
    can tell the user "we deleted N, kept M approved ones — reject those
    first if you want them gone too" rather than silently no-op."""
    rows = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(body.case_ids))))
        .scalars()
        .all()
    )
    found_ids = {r.case_id for r in rows}
    missing = [cid for cid in body.case_ids if cid not in found_ids]

    deleted: list[str] = []
    skipped_approved: list[str] = []
    for r in rows:
        if r.review_status == "approved":
            skipped_approved.append(r.case_id)
            continue
        await session.delete(r)
        deleted.append(r.case_id)
    await session.commit()
    log.info(
        "case.bulk_deleted",
        deleted_count=len(deleted),
        skipped_approved_count=len(skipped_approved),
        missing_count=len(missing),
    )
    return {
        "data": {
            "deleted": deleted,
            "skipped_approved": skipped_approved,
            "missing": missing,
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
