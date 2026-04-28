"""Diagnosis endpoints — Day 11."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.db import get_session
from app.models import Diagnosis, Pattern, Run
from app.services.diagnoser import (
    DiagnoserError,
    diagnose_run,
    record_feedback,
)
from app.services.pattern_store import find_matches_for_run

router = APIRouter()


class GenerateRequest(BaseModel):
    overwrite_existing: bool = False
    prefer_provider: str | None = None


class FeedbackRequest(BaseModel):
    feedback: str  # confirmed | wrong | partially_correct
    note: str = ""


@router.get("/")
async def list_diagnoses(
    run_id: str | None = None,
    case_id: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Diagnosis).order_by(desc(Diagnosis.created_at)).limit(limit)
    if run_id:
        stmt = stmt.where(Diagnosis.run_id == run_id)
    if case_id:
        stmt = stmt.where(Diagnosis.case_id == case_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows]}


@router.get("/patterns/")
async def list_patterns(
    pattern_type: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Pattern).order_by(desc(Pattern.last_hit_at), desc(Pattern.hit_count)).limit(limit)
    if pattern_type:
        stmt = stmt.where(Pattern.pattern_type == pattern_type)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows]}


@router.get("/by-run/{run_id}")
async def get_diagnoses_by_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """All diagnoses + matching sediment patterns for one run."""
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
    body: GenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        diag = await diagnose_run(
            run_id=run_id,
            session=session,
            prefer_provider=body.prefer_provider,
            overwrite_existing=body.overwrite_existing,
        )
    except DiagnoserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": diag.model_dump()}


@router.get("/{diag_id}")
async def get_diagnosis(diag_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(Diagnosis, diag_id)
    if row is None:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    return {"data": row.model_dump()}


@router.post("/{diag_id}/feedback")
async def submit_feedback(
    diag_id: str,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        diag = await record_feedback(
            diag_id=diag_id,
            feedback=body.feedback,
            note=body.note,
            session=session,
        )
    except DiagnoserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": diag.model_dump()}
