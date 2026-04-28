"""Project CRUD + aggregate report."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import Project, Run, StepEvent, TestCase
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
    project_id: str
    name: str
    base_url: str = ""
    description: str = ""
    default_username: str = ""
    default_password: str = ""


@router.get("/")
async def list_projects(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(Project))).scalars().all()
    return {"data": [r.model_dump() for r in rows]}


@router.get("/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(Project, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return {"data": row.model_dump()}


@router.post("/")
async def create_or_update_project(
    body: ProjectIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    existing = await session.get(Project, body.project_id)
    if existing:
        for k, v in body.model_dump().items():
            setattr(existing, k, v)
        existing.updated_at = datetime.now(UTC)
    else:
        existing = Project(**body.model_dump())
        session.add(existing)
    await session.commit()
    return {"data": existing.model_dump()}


@router.get("/{project_id}/report.html", response_class=HTMLResponse)
async def project_aggregate_report(
    project_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """One self-contained HTML report aggregating the latest run per case for this project.

    Drives from Run rows newest-first, deduped by case_id (one row per case).
    """
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

    case_ids = [r.case_id for r in latest]
    cases = (
        (await session.execute(select(TestCase).where(TestCase.case_id.in_(case_ids))))
        .scalars()
        .all()
    )
    case_by_id = {c.case_id: c for c in cases}

    rows: list[ResultRow] = []
    for r in latest:
        case = case_by_id.get(r.case_id)
        if r.status == "passed":
            status = PASS
        elif r.status in {"failed", "aborted", "flaky"}:
            status = FAIL
        else:
            status = SKIP

        # Reuse the run-level adapter to pick up screenshot + error string
        steps = (
            (
                await session.execute(
                    select(StepEvent)
                    .where(StepEvent.run_id == r.run_id)
                    .order_by(StepEvent.step_index)
                )
            )
            .scalars()
            .all()
        )
        if case is None:
            rows.append(
                ResultRow(
                    case_id=r.case_id, title=r.case_id, status=status, error=r.error_message or ""
                )
            )
            continue
        single = run_to_report_input(
            run=r,
            steps=list(steps),
            case_name=case.name,
            case_intent=case.intent,
            case_module=case.module,
        )
        # Take the (one) row we'd have rendered for this single run
        rows.extend(single.rows)

    rep = ReportInput(
        project=proj.name or project_id,
        run_id=f"{project_id}-aggregate-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}",
        excel_path="latest run per case",
        rows=rows,
    )
    return HTMLResponse(content=render_report_html(rep))
