"""Diagnosis endpoints — Day 11."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, audit, require_project_role
from app.db import get_session
from app.models import Diagnosis, DiagnosisJob, Pattern, Run
from app.services.diagnoser import (
    DiagnoserError,
    diagnose_run,
    record_feedback,
)
from app.services.diagnosis_jobs import create_diagnosis_job, run_diagnosis_job
from app.services.pattern_store import find_matches_for_run
from app.services.report_publish import publish_diagnosis

router = APIRouter()


class GenerateRequest(BaseModel):
    overwrite_existing: bool = False
    prefer_provider: str | None = None
    include_dev_context: bool = False


class FeedbackRequest(BaseModel):
    feedback: Literal["confirmed", "wrong", "partially_correct"]
    feedback_target: Literal["", "pattern", "asset", "case", "coverage"] = ""
    reason: Literal[
        "",
        "category_wrong",
        "evidence_insufficient",
        "fix_not_actionable",
        "model_hallucinated",
        "other",
    ] = ""
    note: str = ""


class PublishRequest(BaseModel):
    target: dict


@router.get("/")
async def list_diagnoses(
    request: Request,
    run_id: str | None = None,
    case_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Diagnosis).order_by(desc(Diagnosis.created_at)).limit(limit)
    if run_id:
        stmt = stmt.where(Diagnosis.run_id == run_id)
    if case_id:
        stmt = stmt.where(Diagnosis.case_id == case_id)
    rows = (await session.execute(stmt)).scalars().all()
    if rows:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            run_ids = [r.run_id for r in rows]
            runs = (
                (await session.execute(select(Run).where(Run.run_id.in_(run_ids)))).scalars().all()
            )
            allowed_runs = {r.run_id for r in runs if r.project_id in allowed}
            rows = [r for r in rows if r.run_id in allowed_runs]
    return {"data": [r.model_dump() for r in rows]}


@router.get("/export")
async def export_diagnoses(
    request: Request,
    run_id: str | None = None,
    case_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    stmt = select(Diagnosis).order_by(desc(Diagnosis.created_at))
    if run_id:
        stmt = stmt.where(Diagnosis.run_id == run_id)
    if case_id:
        stmt = stmt.where(Diagnosis.case_id == case_id)
    rows = (await session.execute(stmt)).scalars().all()
    if rows:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            run_ids = [r.run_id for r in rows]
            runs = (
                (await session.execute(select(Run).where(Run.run_id.in_(run_ids)))).scalars().all()
            )
            allowed_runs = {r.run_id for r in runs if r.project_id in allowed}
            rows = [r for r in rows if r.run_id in allowed_runs]
    return JSONResponse({"data": [r.model_dump() for r in rows]})


@router.get("/patterns/")
async def list_patterns(
    pattern_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Pattern).order_by(desc(Pattern.last_hit_at), desc(Pattern.hit_count)).limit(limit)
    if pattern_type:
        stmt = stmt.where(Pattern.pattern_type == pattern_type)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows]}


@router.get("/by-run/{run_id}")
async def get_diagnoses_by_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """All diagnoses + matching sediment patterns for one run."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "viewer", session
    )
    diags = (
        (
            await session.execute(
                select(Diagnosis)
                .where(Diagnosis.run_id == run_id)
                .order_by(desc(Diagnosis.created_at))
            )
        )
        .scalars()
        .all()
    )
    matches = await find_matches_for_run(run_id=run_id, session=session)
    return {
        "data": {
            "diagnoses": [d.model_dump() for d in diags],
            "pattern_matches": [
                {
                    "pattern_id": p.pattern_id,
                    "pattern_type": p.pattern_type,
                    "title": p.title,
                    "description": p.description,
                    "suggested_action": p.suggested_action,
                    "hit_count": p.hit_count,
                }
                for p in matches
            ],
        }
    }


@router.post("/by-run/{run_id}/generate")
async def trigger_diagnosis_for_run(
    run_id: str,
    request: Request,
    body: GenerateRequest = Body(default_factory=GenerateRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "reviewer", session
    )
    try:
        diag = await diagnose_run(
            run_id=run_id,
            session=session,
            prefer_provider=body.prefer_provider,
            overwrite_existing=body.overwrite_existing,
            include_dev_context=body.include_dev_context,
        )
    except DiagnoserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit(
        actor=getattr(request.state, "user", None),
        action="diagnosis.generated",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="diagnosis",
        target_id=diag.diag_id,
        detail=f"run_id={run_id}; include_dev_context={body.include_dev_context}",
        session=session,
    )
    await session.commit()
    return {"data": diag.model_dump()}


@router.post("/by-run/{run_id}/generate-dev-context")
async def trigger_dev_context_diagnosis_for_run(
    run_id: str,
    request: Request,
    body: GenerateRequest = Body(default_factory=GenerateRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "reviewer", session
    )
    try:
        diag = await diagnose_run(
            run_id=run_id,
            session=session,
            prefer_provider=body.prefer_provider,
            overwrite_existing=body.overwrite_existing,
            include_dev_context=True,
        )
    except DiagnoserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    evidence_pack = diag.evidence_pack or {}
    code_files = len((evidence_pack.get("code_context") or {}).get("candidate_files") or [])
    server_logs = evidence_pack.get("server_logs") or {}
    external = evidence_pack.get("external_context") or {}
    log_snippets = len(server_logs.get("snippets") or [])
    external_count = sum(
        len(external.get(key) or []) for key in ("jira", "confluence", "ci", "jenkins")
    )
    await audit(
        actor=getattr(request.state, "user", None),
        action="diagnosis.dev_context_generated",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="diagnosis",
        target_id=diag.diag_id,
        detail=f"run_id={run_id}; candidate_files={code_files}; server_log_snippets={log_snippets}",
        session=session,
    )
    if external_count:
        await audit(
            actor=getattr(request.state, "user", None),
            action="dev_context.external_evidence_collected",
            method=request.method,
            path=request.url.path,
            status_code=200,
            target_type="run",
            target_id=run_id,
            detail=f"jira={len(external.get('jira') or [])}; confluence={len(external.get('confluence') or [])}; ci={len(external.get('ci') or [])}; jenkins={len(external.get('jenkins') or [])}",
            session=session,
        )
    if log_snippets:
        await audit(
            actor=getattr(request.state, "user", None),
            action="dev_context.server_logs_read",
            method=request.method,
            path=request.url.path,
            status_code=200,
            target_type="run",
            target_id=run_id,
            detail=f"snippets={log_snippets}",
            session=session,
        )
    await session.commit()
    return {"data": diag.model_dump()}


@router.post("/by-run/{run_id}/jobs")
async def create_diagnosis_job_for_run(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: GenerateRequest = Body(default_factory=GenerateRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "reviewer", session
    )
    job = await create_diagnosis_job(
        run=run,
        actor=getattr(request.state, "user", None),
        include_dev_context=body.include_dev_context,
        overwrite_existing=body.overwrite_existing,
        prefer_provider=body.prefer_provider,
        session=session,
    )
    await audit(
        actor=getattr(request.state, "user", None),
        action="diagnosis.job_created",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="diagnosis_job",
        target_id=job.job_id,
        detail=f"run_id={run_id}; include_dev_context={body.include_dev_context}",
        session=session,
    )
    await session.commit()
    background_tasks.add_task(
        run_diagnosis_job,
        job_id=job.job_id,
        actor=getattr(request.state, "user", None),
    )
    return {"data": job.model_dump()}


@router.get("/jobs/{job_id}")
async def get_diagnosis_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await session.get(DiagnosisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="diagnosis job not found")
    await require_project_role(
        getattr(request.state, "user", None), job.project_id, "viewer", session
    )
    return {"data": job.model_dump()}


@router.get("/{diag_id}")
async def get_diagnosis(
    diag_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Diagnosis, diag_id)
    if row is None:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    run = await session.get(Run, row.run_id)
    if run is not None:
        await require_project_role(
            getattr(request.state, "user", None), run.project_id, "viewer", session
        )
    return {"data": row.model_dump()}


@router.post("/{diag_id}/feedback")
async def submit_feedback(
    diag_id: str,
    body: FeedbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        row = await session.get(Diagnosis, diag_id)
        if row is None:
            raise HTTPException(status_code=404, detail="diagnosis not found")
        run = await session.get(Run, row.run_id)
        if run is not None:
            await require_project_role(
                getattr(request.state, "user", None), run.project_id, "reviewer", session
            )
        diag = await record_feedback(
            diag_id=diag_id,
            feedback=body.feedback,
            feedback_target=body.feedback_target,
            reason=body.reason,
            note=body.note,
            session=session,
        )
    except DiagnoserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = diag.model_dump()
    await audit(
        actor=getattr(request.state, "user", None),
        action="diagnosis.feedback",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="diagnosis",
        target_id=diag_id,
        detail=f"feedback={body.feedback}; reason={body.reason or ''}",
        session=session,
    )
    await session.commit()
    return {"data": data}


@router.post("/{diag_id}/publish")
async def publish_diagnosis_result(
    diag_id: str,
    body: PublishRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Diagnosis, diag_id)
    if row is None:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    run = await session.get(Run, row.run_id)
    if run is not None:
        await require_project_role(
            getattr(request.state, "user", None), run.project_id, "reviewer", session
        )
    try:
        result = await publish_diagnosis(diag=row, target=body.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    await audit(
        actor=getattr(request.state, "user", None),
        action="diagnosis.published",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="diagnosis",
        target_id=diag_id,
        detail=f"target_type={result.get('target_type')}",
        session=session,
    )
    await session.commit()
    return {"data": result}
