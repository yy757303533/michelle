"""Pilot-readiness reporting endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_project_role
from app.db import get_session
from app.services.pilot_metrics import collect_pilot_metrics

router = APIRouter()


@router.get("/metrics")
async def get_pilot_metrics(
    request: Request,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
    return {"data": await collect_pilot_metrics(session=session, project_id=project_id)}
