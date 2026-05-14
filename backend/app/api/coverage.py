"""Coverage review and case drafting API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import PRD, CoverageItem, TestCase
from app.services.case_drafter import draft_case_from_coverage_item

router = APIRouter()


class CoverageReviewIn(BaseModel):
    action: Literal["accept", "reject", "reset"]
    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        return value.strip()


class CoverageUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    scenario: str | None = Field(default=None, min_length=1, max_length=2000)
    rationale: str | None = Field(default=None, max_length=2000)
    risk_type: str | None = Field(default=None, min_length=1, max_length=80)
    coverage_type: str | None = Field(default=None, min_length=1, max_length=80)
    priority: str | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("title", "scenario", "rationale", "risk_type", "coverage_type", "priority")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class CoverageBulkDeleteIn(BaseModel):
    coverage_ids: list[str]


def _actor_id(request: Request) -> str:
    return str((getattr(request.state, "user", None) or {}).get("sub", ""))


async def _active_linked_case_ids(session: AsyncSession, linked_case_ids: set[str]) -> set[str]:
    if not linked_case_ids:
        return set()
    cases = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(linked_case_ids))))
        .scalars()
        .all()
    )
    return {case.case_id for case in cases if case.deleted_at is None}


async def _has_active_linked_case(session: AsyncSession, row: CoverageItem) -> bool:
    if not row.linked_case_id:
        return False
    case = await session.get(TestCase, row.linked_case_id)
    if case is not None and case.deleted_at is None:
        return True
    row.linked_case_id = None
    row.updated_at = datetime.now(UTC)
    return False


@router.get("/")
async def list_coverage(
    request: Request,
    project_id: str | None = None,
    prd_id: str | None = None,
    status: Literal["proposed", "accepted", "rejected", "stale"] | None = None,
    deleted: Literal["active", "deleted", "all"] = "active",
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
    if deleted == "active":
        stmt = stmt.where(CoverageItem.deleted_at.is_(None))
    elif deleted == "deleted":
        stmt = stmt.where(CoverageItem.deleted_at.is_not(None))
    rows = (await session.execute(stmt)).scalars().all()
    prd_ids = {row.prd_id for row in rows}
    prds = (
        (await session.execute(select(PRD).where(PRD.prd_id.in_(prd_ids)))).scalars().all()
        if prd_ids
        else []
    )
    prd_deleted_at = {prd.prd_id: prd.deleted_at for prd in prds}
    active_linked_case_ids = await _active_linked_case_ids(
        session, {row.linked_case_id for row in rows if row.linked_case_id}
    )
    data = []
    for row in rows:
        item = row.model_dump()
        if row.linked_case_id not in active_linked_case_ids:
            item["linked_case_id"] = None
        item["source_prd_deleted_at"] = prd_deleted_at.get(row.prd_id)
        data.append(item)
    return {"data": data, "count": len(rows)}


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
    row.review_note = body.note
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}


@router.patch("/{coverage_id}")
async def update_coverage(
    coverage_id: str,
    body: CoverageUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(CoverageItem, coverage_id)
    if row is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    if row.deleted_at is not None:
        raise HTTPException(status_code=409, detail="restore coverage before editing")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    if await _has_active_linked_case(session, row):
        raise HTTPException(
            status_code=409,
            detail="coverage item has a linked case; handle the linked case before editing coverage",
        )

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"data": row.model_dump()}
    for field, value in updates.items():
        setattr(row, field, value)
    if row.review_status in {"accepted", "rejected"}:
        row.review_status = "proposed"
        row.review_note = ""
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}


@router.post("/bulk-delete")
async def bulk_delete_coverage(
    body: CoverageBulkDeleteIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ids = list(dict.fromkeys(body.coverage_ids))
    rows = (
        (await session.execute(select(CoverageItem).where(CoverageItem.coverage_id.in_(ids))))
        .scalars()
        .all()
    )
    for row in rows:
        await require_project_role(
            getattr(request.state, "user", None), row.project_id, "reviewer", session
        )
    found_ids = {row.coverage_id for row in rows}
    missing = [coverage_id for coverage_id in ids if coverage_id not in found_ids]

    now = datetime.now(UTC)
    deleted: list[str] = []
    skipped_linked: list[str] = []
    skipped_already_deleted: list[str] = []
    for row in rows:
        if await _has_active_linked_case(session, row):
            skipped_linked.append(row.coverage_id)
            continue
        if row.deleted_at is not None:
            skipped_already_deleted.append(row.coverage_id)
            continue
        row.deleted_at = now
        row.deleted_by = _actor_id(request)
        row.delete_reason = "bulk_manual"
        row.updated_at = now
        deleted.append(row.coverage_id)

    await audit(
        actor=getattr(request.state, "user", None),
        action="coverage.bulk_soft_deleted",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="coverage",
        target_id=",".join(deleted[:20]),
        detail=(
            f"deleted={len(deleted)}; skipped_linked={len(skipped_linked)}; "
            f"skipped_already_deleted={len(skipped_already_deleted)}; missing={len(missing)}"
        ),
        session=session,
    )
    await session.commit()
    return {
        "data": {
            "deleted": deleted,
            "skipped_linked": skipped_linked,
            "skipped_already_deleted": skipped_already_deleted,
            "missing": missing,
        }
    }


@router.delete("/{coverage_id}", status_code=204)
async def delete_coverage(
    coverage_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(CoverageItem, coverage_id)
    if row is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    if await _has_active_linked_case(session, row):
        raise HTTPException(
            status_code=409,
            detail="coverage item has a linked case; handle the linked case before deleting coverage",
        )

    row.deleted_at = datetime.now(UTC)
    row.deleted_by = _actor_id(request)
    row.delete_reason = "manual"
    row.updated_at = datetime.now(UTC)
    await audit(
        actor=getattr(request.state, "user", None),
        action="coverage.soft_deleted",
        method=request.method,
        path=request.url.path,
        status_code=204,
        target_type="coverage",
        target_id=coverage_id,
        detail=f"project_id={row.project_id}; prd_id={row.prd_id}",
        session=session,
    )
    await session.commit()


@router.post("/{coverage_id}/restore")
async def restore_coverage(
    coverage_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(CoverageItem, coverage_id)
    if row is None:
        raise HTTPException(status_code=404, detail="coverage item not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    prd = await session.get(PRD, row.prd_id)
    if prd is not None and prd.deleted_at is not None:
        raise HTTPException(status_code=409, detail="restore the source PRD before coverage")
    row.deleted_at = None
    row.deleted_by = ""
    row.delete_reason = ""
    row.updated_at = datetime.now(UTC)
    await audit(
        actor=getattr(request.state, "user", None),
        action="coverage.restored",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="coverage",
        target_id=coverage_id,
        session=session,
    )
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
    if coverage.deleted_at is not None:
        raise HTTPException(status_code=409, detail="restore coverage before drafting a case")
    await require_project_role(
        getattr(request.state, "user", None), coverage.project_id, "reviewer", session
    )
    await _has_active_linked_case(session, coverage)
    try:
        case, reused = await draft_case_from_coverage_item(coverage=coverage, session=session)
    except ValueError as exc:
        status = 409 if "accepted" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"data": case.model_dump(), "reused": reused}
