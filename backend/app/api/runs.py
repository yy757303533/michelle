"""Run lifecycle endpoints + report serving.

Day 5: list/detail + serve generated HTML report.
Day 6: POST /api/runs to actually trigger a run via claude_runner.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import Run, StepEvent, TestCase
from app.services.report_html import (
    render_report_html,
    render_report_json,
    run_to_report_input,
    write_report_files,
)
from app.storage import run_dir as run_dir_for

router = APIRouter()


@router.get("/")
async def list_runs(
    project_id: str | None = None,
    case_id: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Run).order_by(desc(Run.created_at)).limit(limit)
    if project_id:
        stmt = stmt.where(Run.project_id == project_id)
    if case_id:
        stmt = stmt.where(Run.case_id == case_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows], "count": len(rows)}


@router.get("/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    steps_stmt = select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
    steps = (await session.execute(steps_stmt)).scalars().all()
    return {
        "data": {
            "run": run.model_dump(),
            "steps": [s.model_dump() for s in steps],
        }
    }


@router.get("/{run_id}/report.html")
async def get_run_report_html(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    html_path = await _ensure_report(run, session)
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@router.get("/{run_id}/report.json")
async def get_run_report_json(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    case = await session.get(TestCase, run.case_id)
    case_name = case.name if case else run.case_id
    case_intent = case.intent if case else ""
    case_module = case.module if case else ""

    steps_stmt = select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
    steps = (await session.execute(steps_stmt)).scalars().all()

    rep = run_to_report_input(
        run=run, steps=list(steps), case_name=case_name,
        case_intent=case_intent, case_module=case_module,
    )
    return JSONResponse(content={"data": _json_to_dict(render_report_json(rep))})


# ── Internals ──


async def _ensure_report(run: Run, session: AsyncSession) -> Path:
    """Render report.html + result.json on demand if missing; return html path."""
    rd = run_dir_for(run.project_id, run.run_id)
    html_path = rd / "report.html"
    if html_path.exists() and run.report_html_path:
        return html_path

    case = await session.get(TestCase, run.case_id)
    case_name = case.name if case else run.case_id
    case_intent = case.intent if case else ""
    case_module = case.module if case else ""

    steps_stmt = (
        select(StepEvent)
        .where(StepEvent.run_id == run.run_id)
        .order_by(StepEvent.step_index)
    )
    steps = (await session.execute(steps_stmt)).scalars().all()
    rep = run_to_report_input(
        run=run,
        steps=list(steps),
        case_name=case_name,
        case_intent=case_intent,
        case_module=case_module,
    )
    paths = write_report_files(rep, rd)

    # Persist artifact paths back onto Run
    run.report_html_path = str(paths["html"])
    run.artifacts_dir = str(rd)
    await session.commit()

    # Also keep an inline copy in case the file system path changes
    if not html_path.exists():
        html_path.write_text(render_report_html(rep), encoding="utf-8")

    return html_path


def _json_to_dict(s: str):
    import json
    return json.loads(s)
