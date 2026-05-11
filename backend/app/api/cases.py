"""Test case CRUD + review workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import Project, Run, TestCase
from app.obs import EVENTS, get_logger
from app.services.case_generator import CASE_ID_ALLOCATION_LOCK, _mint_case_id, _next_seq
from app.services.run_orchestrator import rollback_run_scope

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


async def _delete_case_run_history(session: AsyncSession, case_id: str) -> int:
    run_ids = (
        (await session.execute(select(Run.run_id).where(Run.case_id == case_id))).scalars().all()
    )
    deleted = 0
    for run_id in run_ids:
        deleted += await rollback_run_scope(session, run_id=run_id, delete_run=True)
    return deleted


ReviewVerb = Literal["approve", "reject", "reset"]
_VERB_TO_STATE: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "reset": "pending",
}
# Status machine is intentionally one-directional from the review queue.
# approve/reject only act on cases that are *awaiting* a verdict (pending or
# the AI-marked stale variant); they refuse to flip an already-reviewed case.
# To go from approved → rejected you must first `reset` back to pending —
# this forces an explicit re-review instead of letting a single click
# silently overwrite a prior human verdict (which is what the previous
# "switch anything that isn't already at target" logic did).
_ALLOWED_SOURCES: dict[str, set[str]] = {
    "approve": {"pending", "stale"},
    "reject": {"pending", "stale"},
    "reset": {"approved", "rejected"},
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


class CaseImport(BaseModel):
    project_id: str = Field(min_length=1)
    cases: list[CaseCreate]


@router.get("/")
async def list_cases(
    request: Request,
    status: Literal["pending", "approved", "rejected", "stale"] | None = None,
    project_id: str | None = None,
    q: str = "",
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(TestCase).order_by(desc(TestCase.created_at), desc(TestCase.case_id))
    base_filters = []
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        base_filters.append(TestCase.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {
                    "data": [],
                    "count": 0,
                    "counts_by_status": {},
                    "total": 0,
                    "truncated": False,
                    "limit": limit,
                    "offset": offset,
                }
            base_filters.append(TestCase.project_id.in_(allowed))
    q = q.strip()
    if q:
        needle = f"%{q}%"
        base_filters.append(
            or_(
                TestCase.case_id.ilike(needle),
                TestCase.name.ilike(needle),
                TestCase.intent.ilike(needle),
                TestCase.module.ilike(needle),
                TestCase.auth_state.ilike(needle),
                cast(TestCase.tags, String).ilike(needle),
            )
        )
    for f in base_filters:
        stmt = stmt.where(f)
    if status:
        stmt = stmt.where(TestCase.review_status == status)
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()

    counts_stmt = select(TestCase.review_status, func.count()).group_by(TestCase.review_status)
    total_stmt = select(func.count()).select_from(TestCase)
    for f in base_filters:
        counts_stmt = counts_stmt.where(f)
        total_stmt = total_stmt.where(f)
    if status:
        total_stmt = total_stmt.where(TestCase.review_status == status)
    counts_rows = (await session.execute(counts_stmt)).all()
    counts: dict[str, int] = {row[0]: row[1] for row in counts_rows}

    # `total` is the full-table count for this project (matching what
    # `counts_by_status` sums to) so the UI can detect "we returned 200
    # rows but the project has 264" — that gap was the cause of the
    # "all (264) / 200 selected" inconsistency users hit before.
    total = int((await session.execute(total_stmt)).scalar_one())
    return {
        "data": [r.model_dump() for r in rows],
        "count": len(rows),
        "counts_by_status": counts,
        "total": total,
        "truncated": len(rows) < total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/export")
async def export_cases(
    request: Request,
    project_id: str | None = None,
    format: Literal["json", "csv"] = "json",
    session: AsyncSession = Depends(get_session),
):
    stmt = select(TestCase).order_by(desc(TestCase.created_at))
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(TestCase.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return JSONResponse({"data": []}) if format == "json" else PlainTextResponse("")
            stmt = stmt.where(TestCase.project_id.in_(allowed))
    rows = (await session.execute(stmt)).scalars().all()
    data = [r.model_dump() for r in rows]
    if format == "json":
        return JSONResponse({"data": data})
    import csv
    import io

    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "case_id",
            "project_id",
            "name",
            "intent",
            "module",
            "priority",
            "review_status",
        ],
    )
    writer.writeheader()
    for row in data:
        writer.writerow({k: row.get(k, "") for k in writer.fieldnames or []})
    return PlainTextResponse(out.getvalue(), media_type="text/csv; charset=utf-8")


@router.post("/import")
async def import_cases(
    body: CaseImport,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    proj = await session.get(Project, body.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"project {body.project_id} not found")
    await require_project_role(
        getattr(request.state, "user", None), body.project_id, "reviewer", session
    )
    created = []
    async with CASE_ID_ALLOCATION_LOCK:
        for item in body.cases:
            payload = item.model_copy(update={"project_id": body.project_id})
            seq = await _next_seq(session, body.project_id)
            case_id = _mint_case_id(seq)
            row = TestCase(
                case_id=case_id,
                project_id=body.project_id,
                name=payload.name[:200],
                intent=payload.intent[:1000],
                module=payload.module[:60],
                tags=payload.tags,
                priority=payload.priority,
                auth_state=payload.auth_state,
                preconditions=payload.preconditions,
                steps=payload.steps,
                assertions=payload.assertions,
                quality={
                    "score": 1.0,
                    "severity": "low",
                    "flags": [],
                    "reviewer_notes": ["imported case"],
                },
                source="imported",
                prompt_version="import",
                model_version="import",
                review_status="pending",
                version=1,
            )
            session.add(row)
            created.append(row)
        await session.commit()
    data = [r.model_dump() for r in created]
    await audit(
        actor=getattr(request.state, "user", None),
        action="case.imported",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="project",
        target_id=body.project_id,
        detail=f"count={len(created)}",
        session=session,
    )
    await session.commit()
    return {"data": data, "count": len(created)}


@router.post("/", status_code=201)
async def create_case(
    body: CaseCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Hand-author a case. Lands in `pending` so it still goes through the
    same review gate AI-generated cases do."""
    proj = await session.get(Project, body.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"project {body.project_id} not found")
    await require_project_role(
        getattr(request.state, "user", None), body.project_id, "reviewer", session
    )

    async with CASE_ID_ALLOCATION_LOCK:
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
            quality={
                "score": 1.0,
                "severity": "low",
                "flags": [],
                "reviewer_notes": ["manual case"],
            },
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
    data = row.model_dump()
    await audit(
        actor=getattr(request.state, "user", None),
        action="case.created",
        method=request.method,
        path=request.url.path,
        status_code=201,
        target_type="case",
        target_id=case_id,
        detail=f"project_id={body.project_id}",
        session=session,
    )
    await session.commit()
    return {"data": data}


@router.delete("/{case_id}", status_code=204)
async def delete_case(
    case_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a case. Approved cases are protected — the contract is
    that approved == human-confirmed, and one accidental click shouldn't
    erase that signal. Reject the case first if you really need it gone."""
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )
    if row.review_status == "approved":
        raise HTTPException(
            status_code=409,
            detail="approved cases cannot be deleted; reject it first if you really mean to remove",
        )
    prior = row.review_status
    run_history_deleted = await _delete_case_run_history(session, case_id)
    await session.delete(row)
    await session.commit()
    log.info(
        "case.deleted",
        case_id=case_id,
        prior_status=prior,
        run_history_deleted=run_history_deleted,
    )


@router.get("/{case_id}")
async def get_case(
    case_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "viewer", session
    )
    return {"data": row.model_dump()}


@router.post("/{case_id}/review")
async def review_case(
    case_id: str,
    body: ReviewAction,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )

    before = row.review_status
    target = _VERB_TO_STATE[body.action]
    if before == target:
        # Already at target — no-op rather than 409, matches the bulk path
        # which silently skips already-at-state rows.
        return {"data": row.model_dump()}
    if before not in _ALLOWED_SOURCES[body.action]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot {body.action} a case in '{before}' state — "
                + (
                    "reset it to pending first if you want to change the verdict"
                    if body.action in {"approve", "reject"}
                    else "reset only applies to approved/rejected cases"
                )
            ),
        )

    row.review_status = target
    row.updated_at = datetime.now(UTC)
    data = row.model_dump()
    log.info(
        EVENTS.REVIEW_CASE_ACTION.name,
        case_id=case_id,
        action=body.action,
        before_state=before,
        after_state=row.review_status,
        note=body.note[:500] if body.note else "",
    )
    await audit(
        actor=getattr(request.state, "user", None),
        action=f"case.{body.action}",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="case",
        target_id=case_id,
        detail=f"{before}->{target}; note={body.note[:500] if body.note else ''}",
        session=session,
    )
    await session.commit()
    return {"data": data}


@router.post("/bulk-review")
async def bulk_review(
    body: BulkReview,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Apply approve/reject/reset to many cases in one transaction.

    `reset` reverts approved/rejected cases back to `pending` — useful when
    a reviewer wants to take back a verdict without having to edit the case
    body. (Edits already auto-reset, but that's a heavier action.)"""
    target = _VERB_TO_STATE[body.action]
    allowed_sources = _ALLOWED_SOURCES[body.action]

    rows = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(body.case_ids))))
        .scalars()
        .all()
    )
    for row in rows:
        await require_project_role(
            getattr(request.state, "user", None), row.project_id, "reviewer", session
        )
    found_ids = {r.case_id for r in rows}
    missing = [cid for cid in body.case_ids if cid not in found_ids]

    now = datetime.now(UTC)
    updated: list[str] = []
    skipped_already_at_state: list[str] = []
    skipped_wrong_state: list[str] = []
    for r in rows:
        if r.review_status == target:
            skipped_already_at_state.append(r.case_id)
            continue
        if r.review_status not in allowed_sources:
            # e.g. trying to approve a `rejected` case — refuse silently in
            # the bulk path so the rest of the batch still applies; surface
            # the count + ids so the UI can tell the user what didn't move.
            skipped_wrong_state.append(r.case_id)
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
    await audit(
        actor=getattr(request.state, "user", None),
        action=f"case.bulk_{body.action}",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="case",
        target_id=",".join(updated[:20]),
        detail=(
            f"updated={len(updated)}; missing={len(missing)}; "
            f"skipped_already={len(skipped_already_at_state)}; "
            f"skipped_wrong_state={len(skipped_wrong_state)}"
        ),
        session=session,
    )
    await session.commit()
    return {
        "data": {
            "updated": updated,
            "skipped_already_at_state": skipped_already_at_state,
            "skipped_wrong_state": skipped_wrong_state,
            "missing": missing,
            "target_state": target,
        }
    }


@router.post("/bulk-delete")
async def bulk_delete(
    body: BulkDelete,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete many cases in one transaction. Approved cases are skipped
    (same guard as DELETE /<id>) and surfaced in the response so the UI
    can tell the user "we deleted N, kept M approved ones — reject those
    first if you want them gone too" rather than silently no-op."""
    rows = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(body.case_ids))))
        .scalars()
        .all()
    )
    for row in rows:
        await require_project_role(
            getattr(request.state, "user", None), row.project_id, "reviewer", session
        )
    found_ids = {r.case_id for r in rows}
    missing = [cid for cid in body.case_ids if cid not in found_ids]

    deleted: list[str] = []
    skipped_approved: list[str] = []
    run_history_deleted = 0
    for r in rows:
        if r.review_status == "approved":
            skipped_approved.append(r.case_id)
            continue
        run_history_deleted += await _delete_case_run_history(session, r.case_id)
        await session.delete(r)
        deleted.append(r.case_id)
    await session.commit()
    log.info(
        "case.bulk_deleted",
        deleted_count=len(deleted),
        skipped_approved_count=len(skipped_approved),
        missing_count=len(missing),
        run_history_deleted=run_history_deleted,
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
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Partial edit. Each field set in the body is added to
    manual_edited_fields, which protects it from future LLM regenerations.

    Edits don't change review_status; the user re-reviews after editing.
    """
    row = await session.get(TestCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    await require_project_role(
        getattr(request.state, "user", None), row.project_id, "reviewer", session
    )

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
