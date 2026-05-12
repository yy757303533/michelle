"""Run lifecycle endpoints + report serving."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
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
    active_run_ids,
    create_run_row,
    kick_off,
)
from app.services.run_orchestrator import (
    cancel_run as cancel_run_task,
)
from app.storage import run_dir as run_dir_for

router = APIRouter()
log = get_logger(__name__)


def _has_live_case_filter():
    return Run.case_id.in_(select(TestCase.case_id))


def _performance_breakdown(run: Run, steps: list[StepEvent]) -> dict:
    """Computed run timing summary for the UI.

    New generic-loop runs persist per-step latency. Older runs will still show
    counts, with timing buckets omitted when latency was not recorded.
    """
    llm_ms = 0
    browser_ms = 0
    internal_ms = 0
    recorded_ms = 0
    model_turn_events = 0
    model_result_events = 0
    browser_tools = 0
    internal_tools = 0
    screenshots = 0
    snapshots = 0

    for step in steps:
        name = step.tool_name or ""
        latency = step.latency_ms or 0
        if latency:
            recorded_ms += latency
        if name == "model_turn":
            model_turn_events += 1
        elif name == "model_result":
            model_result_events += 1
            llm_ms += latency
        elif name.startswith("browser_"):
            browser_tools += 1
            browser_ms += latency
            if name == "browser_take_screenshot":
                screenshots += 1
            elif name == "browser_snapshot":
                snapshots += 1
        elif name.startswith("email_"):
            internal_tools += 1
            internal_ms += latency

    total_ms = run.duration_ms
    known_ms = llm_ms + browser_ms + internal_ms
    unaccounted_ms = max(total_ms - known_ms, 0) if total_ms is not None else None
    return {
        "duration_ms": total_ms,
        "recorded_step_latency_ms": recorded_ms or None,
        "llm_ms": llm_ms or None,
        "browser_ms": browser_ms or None,
        "internal_tool_ms": internal_ms or None,
        "unaccounted_ms": unaccounted_ms,
        "model_turns": model_result_events or model_turn_events,
        "browser_tools": browser_tools,
        "internal_tools": internal_tools,
        "snapshots": snapshots,
        "screenshots": screenshots,
        "steps": len(steps),
    }


async def _active_run_for_case(session: AsyncSession, *, case_id: str) -> Run | None:
    return (
        (
            await session.execute(
                select(Run)
                .where(Run.case_id == case_id)
                .where(Run.status.in_(["pending", "running"]))
                .order_by(desc(Run.created_at))
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


class CreateRunsRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    env: str = "default"
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    """Override per-call. Defaults to DEFAULT_RUN_TIMEOUT."""


@router.post("/")
async def create_runs(
    body: CreateRunsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Kick off async execution for one or more cases. Returns run_ids."""
    if not body.case_ids:
        raise HTTPException(status_code=400, detail="case_ids must not be empty")

    runs: list[Run] = []
    timeout = body.timeout_seconds or DEFAULT_RUN_TIMEOUT
    for cid in body.case_ids:
        case = await session.get(TestCase, cid)
        if case is None:
            raise HTTPException(status_code=404, detail=f"case {cid} not found")
        if case.review_status != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"case {cid} must be approved before it can run",
            )
        await require_project_role(
            getattr(request.state, "user", None), case.project_id, "reviewer", session
        )
        active = await _active_run_for_case(session, case_id=cid)
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"case {cid} already has an active run ({active.status}: {active.run_id})"),
            )
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
    request: Request,
    project_id: str | None = None,
    case_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Run).where(_has_live_case_filter()).order_by(desc(Run.created_at)).limit(limit)
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(Run.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": [], "count": 0}
            stmt = stmt.where(Run.project_id.in_(allowed))
    if case_id:
        stmt = stmt.where(Run.case_id == case_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows], "count": len(rows)}


@router.get("/queue")
async def get_run_queue(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = (
        select(Run)
        .where(_has_live_case_filter())
        .where(Run.status.in_(["pending", "running"]))
        .order_by(Run.created_at)
        .limit(limit)
    )
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(Run.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": [], "count": 0, "active_task_count": len(active_run_ids())}
            stmt = stmt.where(Run.project_id.in_(allowed))
    rows = (await session.execute(stmt)).scalars().all()
    active = active_run_ids()
    now = datetime.now(UTC)
    data = []
    for idx, row in enumerate(rows, start=1):
        item = row.model_dump()
        item["queue_position"] = idx if row.status == "pending" else None
        item["has_live_task"] = row.run_id in active
        item["cancelable"] = row.status in {"pending", "running"}
        started = row.started_at or row.created_at
        age_seconds = int((now - started).total_seconds()) if started else 0
        item["age_seconds"] = age_seconds
        item["stuck_hint"] = row.status == "running" and (
            (row.run_id not in active and age_seconds > 30) or age_seconds > DEFAULT_RUN_TIMEOUT
        )
        data.append(item)
    return {
        "data": data,
        "count": len(data),
        "active_task_count": len(active),
    }


@router.get("/trends")
async def get_run_trends(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Run).where(_has_live_case_filter()).order_by(desc(Run.created_at)).limit(limit)
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(Run.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {
                    "data": {
                        "total": 0,
                        "terminal": 0,
                        "pass_rate": None,
                        "flaky_rate": None,
                        "avg_duration_ms": None,
                        "by_status": {},
                        "by_day": [],
                        "top_projects": [],
                    }
                }
            stmt = stmt.where(Run.project_id.in_(allowed))
    rows = list((await session.execute(stmt)).scalars().all())

    by_status: dict[str, int] = {}
    by_day: dict[str, dict[str, int]] = {}
    by_project: dict[str, int] = {}
    durations: list[int] = []
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        day = row.created_at.date().isoformat()
        by_day.setdefault(day, {})
        by_day[day][row.status] = by_day[day].get(row.status, 0) + 1
        by_project[row.project_id] = by_project.get(row.project_id, 0) + 1
        if row.duration_ms is not None:
            durations.append(row.duration_ms)

    total = len(rows)
    failed_like = sum(by_status.get(s, 0) for s in ("failed", "flaky", "aborted"))
    passed = by_status.get("passed", 0)
    terminal = passed + failed_like
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else None
    return {
        "data": {
            "total": total,
            "terminal": terminal,
            "pass_rate": (passed / terminal) if terminal else None,
            "flaky_rate": (by_status.get("flaky", 0) / terminal) if terminal else None,
            "avg_duration_ms": avg_duration_ms,
            "by_status": by_status,
            "by_day": [{"date": day, **counts} for day, counts in sorted(by_day.items())],
            "top_projects": sorted(
                [{"project_id": k, "count": v} for k, v in by_project.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:10],
        }
    }


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is not None:
        await require_project_role(
            getattr(request.state, "user", None), run.project_id, "reviewer", session
        )
    ok = await cancel_run_task(run_id=run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="run is not pending/running or does not exist")
    await audit(
        actor=getattr(request.state, "user", None),
        action="run.cancelled",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="run",
        target_id=run_id,
        session=session,
    )
    await session.commit()
    return {"data": {"run_id": run_id, "status": "cancelled", "rolled_back": True}}


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )
    steps_stmt = select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
    steps = (await session.execute(steps_stmt)).scalars().all()
    case = await session.get(TestCase, run.case_id)
    return {
        "data": {
            "run": run.model_dump(),
            "steps": [s.model_dump() for s in steps],
            "failure_context": _failure_context(steps),
            "failure_summary": _failure_summary(run, steps, case),
            "performance": _performance_breakdown(run, steps),
        }
    }


@router.get("/{run_id}/report.html")
async def get_run_report_html(
    run_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )

    html_path = await _ensure_report(run, session)
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@router.get("/{run_id}/artifacts/{filename:path}")
async def get_run_artifact(
    run_id: str,
    filename: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve a single artifact file (screenshot, trace.jsonl, etc.) sandboxed
    to the run's directory. Path traversal blocked."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )

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
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=target.name,
        )
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
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List files inside the run's artifacts dir. Used by the trace viewer
    frontend to discover screenshots without fetching one-by-one."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )

    base = run_dir_for(run.project_id, run_id).resolve()
    files: list[dict] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            try:
                rel = p.relative_to(base)
            except ValueError:
                continue
            name = str(rel).replace("\\", "/")
            files.append(
                {
                    "name": name,
                    "size": p.stat().st_size,
                    "is_image": p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"},
                    "kind": _artifact_kind(name),
                }
            )
    return {"data": files}


@router.get("/{run_id}/report.json")
async def get_run_report_json(
    run_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )

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

    # Defensive: write_report_files may have failed to land for any reason
    # (FS hiccup, race with cleanup); rebuild the file in that case so the
    # caller gets a real document instead of a 404.
    if not html_path.exists():
        html_path.write_text(render_report_html(rep), encoding="utf-8")

    return html_path


def _json_to_dict(s: str):
    import json

    return json.loads(s)


def _artifact_kind(name: str) -> str:
    lower = name.lower()
    suffix = Path(lower).suffix
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "screenshot"
    if suffix in {".log", ".txt"} or "log" in lower:
        return "log"
    if suffix == ".jsonl" or "trace" in lower:
        return "trace"
    if suffix in {".html", ".htm"}:
        return "report"
    if suffix in {".json", ".xml", ".csv"}:
        return "data"
    return "other"


def _failure_context(steps: list[StepEvent]) -> dict | None:
    failed = next((s for s in steps if s.status == "failed"), None)
    if failed is None:
        return None
    evidence = ""
    if isinstance(failed.tool_result, dict):
        evidence = str(
            failed.tool_result.get("evidence") or failed.tool_result.get("result_text") or ""
        )
    return {
        "step_index": failed.step_index,
        "phase": getattr(failed, "phase", "action"),
        "tool_name": failed.tool_name,
        "intent": failed.intent,
        "error_message": failed.error_message,
        "evidence": evidence[:1000],
    }


def _failure_summary(run: Run, steps: list[StepEvent], case: TestCase | None) -> dict:
    if run.status in {"passed", "running", "pending"}:
        return {
            "category": None,
            "owner": None,
            "confidence": 0.0,
            "signals": [],
            "next_action": "",
        }

    message = (run.error_message or "").lower()
    failed = next((s for s in steps if s.status == "failed"), None)
    failed_text = ""
    if failed is not None:
        failed_text = " ".join(
            str(part or "")
            for part in (
                failed.tool_name,
                failed.intent,
                failed.error_message,
                failed.tool_result,
            )
        ).lower()
    case_flags = []
    if case and isinstance(case.quality, dict):
        raw_flags = case.quality.get("flags") or []
        if isinstance(raw_flags, list):
            case_flags = [str(flag) for flag in raw_flags]

    signals: list[str] = []
    category = "unknown"
    owner = "unknown"
    confidence = 0.4
    next_action = "Run AI diagnosis or inspect the failed step evidence."

    if "timed out" in message and ("codex" in message or "claude" in message or "llm" in message):
        category = "llm_timeout"
        owner = "executor"
        confidence = 0.9
        signals.append("llm_timeout")
        next_action = "retry the run; if repeated, reduce LLM turns or switch provider"
    elif "screenshot" in failed_text and "timeout" in failed_text:
        category = "environment_error"
        owner = "environment"
        confidence = 0.8
        signals.append("screenshot_timeout")
        next_action = "retry; screenshot timeout should not block core assertions"
    elif any(
        flag
        in {
            "missing_prd_evidence",
            "weak_prd_traceability",
            "too_few_steps",
            "missing_assertions",
            "account_entry_should_use_login_url",
            "redundant_login_steps",
        }
        for flag in case_flags
    ):
        category = "bad_case"
        owner = "case"
        confidence = 0.85
        signals.extend(case_flags)
        next_action = "mark the case feedback reason, fix generation rules, then regenerate"
    elif failed is not None and failed.tool_name == "assertion":
        category = "product_bug"
        owner = "product"
        confidence = 0.65
        signals.append("assertion_failed")
        next_action = "inspect assertion evidence and confirm whether the product violates the PRD"
    elif run.status == "aborted":
        category = "executor_error"
        owner = "executor"
        confidence = 0.65
        signals.append("run_aborted")
        next_action = "inspect executor logs and retry after transient errors are fixed"

    return {
        "category": category,
        "owner": owner,
        "confidence": confidence,
        "signals": signals,
        "next_action": next_action,
    }
