"""Project CRUD + aggregate report."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import Project, ProjectMember, Run, StepEvent, TestCase, User
from app.services.report_html import (
    FAIL,
    PASS,
    SKIP,
    ReportInput,
    ResultRow,
    render_report_html,
    run_to_report_input,
)

router = APIRouter()


class ProjectIn(BaseModel):
    # Auto-generated when omitted on create. Required when updating.
    project_id: str | None = None
    name: str
    base_url: str = ""
    login_url: str = ""
    description: str = ""
    default_username: str = ""
    default_password: str | None = None
    clear_default_password: bool = False


class ProjectMemberIn(BaseModel):
    user_id: str
    role: str = "viewer"


def _project_out(row: Project) -> dict:
    data = row.model_dump()
    data["default_password_is_set"] = bool(row.default_password)
    data["default_password"] = ""
    return data


async def _generate_unique_project_id(session: AsyncSession) -> str:
    """Mint a short opaque project_id (`p_<6hex>`). Loops on collision —
    cheap because the surface is tiny (1 in 16M) and bounded retries make
    pathological cases visible instead of hanging."""
    for _ in range(8):
        candidate = "p_" + uuid4().hex[:6]
        if await session.get(Project, candidate) is None:
            return candidate
    # Pathological: fall back to longer suffix so we still succeed.
    return "p_" + uuid4().hex[:12]


@router.get("/")
async def list_projects(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Project)
    allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
    if allowed is not None:
        if not allowed:
            return {"data": []}
        stmt = stmt.where(Project.project_id.in_(allowed))
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [_project_out(r) for r in rows]}


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await require_project_role(getattr(request.state, "user", None), project_id, "viewer", session)
    row = await session.get(Project, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return {"data": _project_out(row)}


@router.post("/")
async def create_or_update_project(
    body: ProjectIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create (project_id absent) or update (project_id present).

    Identity is server-side: clients send `name` + optional config; the
    server picks a stable opaque id. Client-supplied ids are accepted for
    backwards compatibility and for the update path."""
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="name is required")

    payload = body.model_dump(exclude_unset=True)
    pid = (payload.pop("project_id", None) or "").strip()
    clear_password = bool(payload.pop("clear_default_password", False))
    password_supplied = "default_password" in payload
    password_value = payload.pop("default_password", None)

    existing = await session.get(Project, pid) if pid else None
    action = "project.updated" if existing else "project.created"
    if existing:
        await require_project_role(
            getattr(request.state, "user", None), existing.project_id, "admin", session
        )
        for k, v in payload.items():
            setattr(existing, k, v)
        if clear_password:
            existing.default_password = ""
        elif password_supplied and password_value:
            existing.default_password = password_value
        existing.updated_at = datetime.now(UTC)
    else:
        # Mint a new id when client didn't ask for a specific one. This
        # keeps the user-facing form to "name + optional config" — the
        # server owns identity, the user owns the label.
        new_pid = pid or await _generate_unique_project_id(session)
        existing = Project(project_id=new_pid, default_password=password_value or "", **payload)
        session.add(existing)
        actor = getattr(request.state, "user", None)
        if actor and str(actor.get("role")) != "admin":
            session.add(
                ProjectMember(
                    project_id=new_pid,
                    user_id=str(actor.get("sub")),
                    role="admin",
                )
            )
    await session.commit()
    # Refresh so attribute reads after commit don't trip MissingGreenlet under
    # the default expire_on_commit=True session config.
    await session.refresh(existing)
    data = _project_out(existing)
    await audit(
        actor=getattr(request.state, "user", None),
        action=action,
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="project",
        target_id=existing.project_id,
        detail=f"name={existing.name}",
        session=session,
    )
    await session.commit()
    return {"data": data}


@router.get("/{project_id}/members")
async def list_project_members(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await require_project_role(getattr(request.state, "user", None), project_id, "admin", session)
    rows = (
        (await session.execute(select(ProjectMember).where(ProjectMember.project_id == project_id)))
        .scalars()
        .all()
    )
    users = (await session.execute(select(User))).scalars().all()
    by_id = {u.user_id: u for u in users}
    return {
        "data": [
            {
                **row.model_dump(),
                "username": by_id.get(row.user_id).username
                if by_id.get(row.user_id)
                else row.user_id,
            }
            for row in rows
        ]
    }


@router.put("/{project_id}/members")
async def upsert_project_member(
    project_id: str,
    body: ProjectMemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await require_project_role(getattr(request.state, "user", None), project_id, "admin", session)
    if body.role not in {"viewer", "reviewer", "admin"}:
        raise HTTPException(status_code=422, detail="role must be viewer/reviewer/admin")
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if await session.get(User, body.user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    row = (
        (
            await session.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == body.user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        row = ProjectMember(project_id=project_id, user_id=body.user_id, role=body.role)
        session.add(row)
    else:
        row.role = body.role
    await audit(
        actor=getattr(request.state, "user", None),
        action="project.member_upserted",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="project",
        target_id=project_id,
        detail=f"user_id={body.user_id}; role={body.role}",
        session=session,
    )
    await session.commit()
    await session.refresh(row)
    return {"data": row.model_dump()}


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def delete_project_member(
    project_id: str,
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    await require_project_role(getattr(request.state, "user", None), project_id, "admin", session)
    row = (
        (
            await session.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="member not found")
    await session.delete(row)
    await audit(
        actor=getattr(request.state, "user", None),
        action="project.member_removed",
        method=request.method,
        path=request.url.path,
        status_code=204,
        target_type="project",
        target_id=project_id,
        detail=f"user_id={user_id}",
        session=session,
    )
    await session.commit()


@router.get("/{project_id}/report.html", response_class=HTMLResponse)
async def project_aggregate_report(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """One self-contained HTML report aggregating the latest run per case for this project.

    Drives from Run rows newest-first, deduped by case_id (one row per case).
    """
    await require_project_role(getattr(request.state, "user", None), project_id, "viewer", session)
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")

    runs = (
        (
            await session.execute(
                select(Run)
                .where(Run.project_id == project_id)
                .order_by(desc(Run.created_at))
                .limit(limit * 4)  # over-fetch then dedupe
            )
        )
        .scalars()
        .all()
    )

    seen: set[str] = set()
    latest: list[Run] = []
    for r in runs:
        if r.case_id in seen:
            continue
        seen.add(r.case_id)
        latest.append(r)
        if len(latest) >= limit:
            break

    if not latest:
        rep = ReportInput(
            project=proj.name or project_id,
            run_id=f"{project_id}-aggregate-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}",
            excel_path="latest run per case",
            rows=[],
        )
        return HTMLResponse(content=render_report_html(rep))

    case_ids = [r.case_id for r in latest]
    run_ids = [r.run_id for r in latest]

    cases = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(case_ids))))
        .scalars()
        .all()
    )
    case_by_id = {c.case_id: c for c in cases}

    # Single batched query for all StepEvents — replaces N+1 (one query per
    # run) which would issue `limit` queries and dominate the report render
    # latency at limit=50.
    step_rows = (
        (
            await session.execute(
                select(StepEvent)
                .where(StepEvent.run_id.in_(run_ids))
                .order_by(StepEvent.run_id, StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    steps_by_run: dict[str, list[StepEvent]] = defaultdict(list)
    for s in step_rows:
        steps_by_run[s.run_id].append(s)

    rows: list[ResultRow] = []
    for r in latest:
        case = case_by_id.get(r.case_id)
        if case is None:
            if r.status == "passed":
                status = PASS
            elif r.status in {"failed", "aborted", "flaky"}:
                status = FAIL
            else:
                status = SKIP
            rows.append(
                ResultRow(
                    case_id=r.case_id, title=r.case_id, status=status, error=r.error_message or ""
                )
            )
            continue
        single = run_to_report_input(
            run=r,
            steps=steps_by_run.get(r.run_id, []),
            case_name=case.name,
            case_intent=case.intent,
            case_module=case.module,
        )
        rows.extend(single.rows)

    rep = ReportInput(
        project=proj.name or project_id,
        run_id=f"{project_id}-aggregate-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}",
        excel_path="latest run per case",
        rows=rows,
    )
    return HTMLResponse(content=render_report_html(rep))
