"""Run lifecycle endpoints + report serving."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import Run, StepEvent, TestCase
from app.obs import EVENTS, get_logger
from app.services.report_html import (
    render_report_html,
    render_report_json,
    run_to_report_input,
    write_report_files,
)
from app.services.run_orchestrator import (
    DEFAULT_RUN_TIMEOUT,
    create_run_row,
    kick_off,
)
from app.storage import run_dir as run_dir_for

router = APIRouter()
log = get_logger(__name__)


class CreateRunsRequest(BaseModel):
    case_ids: list[str]
    env: str = "default"
    timeout_seconds: int | None = None
    """Override per-call. Defaults to DEFAULT_RUN_TIMEOUT."""


@router.post("/")
async def create_runs(
    body: CreateRunsRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Kick off async execution for one or more cases. Returns run_ids."""
    if not body.case_ids:
        raise HTTPException(status_code=400, detail="case_ids must not be empty")

    runs: list[Run] = []
    timeout = body.timeout_seconds or DEFAULT_RUN_TIMEOUT
    for cid in body.case_ids:
        try:
            run = await create_run_row(case_id=cid, env=body.env, session=session)
            runs.append(run)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()

    # Schedule background execution. Each runs in its own AsyncSession.
    for run in runs:
        kick_off(
            case_id=run.case_id,
            run_id=run.run_id,
            env=run.env,
            timeout_seconds=timeout,
        )
        log.info(EVENTS.RUN_CREATED.name, run_id=run.run_id, case_id=run.case_id, env=run.env)

    return {
        "data": {
            "run_ids": [r.run_id for r in runs],
            "runs": [{"run_id": r.run_id, "case_id": r.case_id, "status": r.status} for r in runs],
        }
    }


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


@router.get("/{run_id}/artifacts/{filename:path}")
async def get_run_artifact(
    run_id: str,
    filename: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve a single artifact file (screenshot, trace.jsonl, etc.) sandboxed
    to the run's directory. Path traversal blocked."""
    from app.storage import run_dir as run_dir_for

    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    base = run_dir_for(run.project_id, run_id).resolve()
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes run directory") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")

    media = "application/octet-stream"
    suffix = target.suffix.lower()
    if suffix == ".png":
        media = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        media = "image/jpeg"
    elif suffix == ".webp":
        media = "image/webp"
    elif suffix in {".html", ".htm"}:
        media = "text/html; charset=utf-8"
    elif suffix == ".json":
        media = "application/json; charset=utf-8"
    elif suffix == ".jsonl":
        media = "application/x-ndjson; charset=utf-8"
    elif suffix == ".txt":
        media = "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media)


@router.get("/{run_id}/artifacts")
async def list_run_artifacts(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List files inside the run's artifacts dir. Used by the trace viewer
    frontend to discover screenshots without fetching one-by-one."""
    from app.storage import run_dir as run_dir_for

    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    base = run_dir_for(run.project_id, run_id).resolve()
    files: list[dict] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            try:
                rel = p.relative_to(base)
            except ValueError:
                continue
            files.append(
                {
                    "name": str(rel).replace("\\", "/"),
                    "size": p.stat().st_size,
                    "is_image": p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"},
                }
            )
    return {"data": files}


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
        run=run,
        steps=list(steps),
        case_name=case_name,
        case_intent=case_intent,
        case_module=case_module,
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
        select(StepEvent).where(StepEvent.run_id == run.run_id).order_by(StepEvent.step_index)
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
