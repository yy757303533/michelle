"""Regression asset API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import accessible_project_ids, require_project_role
from app.db import get_session
from app.models import RegressionAsset, Run
from app.services.regression_assets import (
    RegressionAssetError,
    approve_asset,
    extract_asset_from_passed_run,
    kick_off_asset_replay,
    record_replay_started,
    repair_asset,
)
from app.services.run_orchestrator import create_run_row

router = APIRouter()


class ReplayAssetRequest(BaseModel):
    env: str = "default"
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)


class RepairAssetRequest(BaseModel):
    status: str | None = None
    action_plan: list[dict] | None = None
    locator_candidates: list[dict] | None = None
    assertions: list[dict] | None = None


@router.get("/")
async def list_assets(
    request: Request,
    project_id: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(RegressionAsset).order_by(desc(RegressionAsset.created_at)).limit(500)
    if project_id:
        await require_project_role(
            getattr(request.state, "user", None), project_id, "viewer", session
        )
        stmt = stmt.where(RegressionAsset.project_id == project_id)
    else:
        allowed = await accessible_project_ids(getattr(request.state, "user", None), session)
        if allowed is not None:
            if not allowed:
                return {"data": [], "count": 0}
            stmt = stmt.where(RegressionAsset.project_id.in_(allowed))
    if status:
        stmt = stmt.where(RegressionAsset.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [row.model_dump() for row in rows], "count": len(rows)}


@router.post("/from-run/{run_id}", status_code=201)
async def create_asset_from_run(
    run_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await require_project_role(
        getattr(request.state, "user", None), run.project_id, "reviewer", session
    )
    try:
        asset = await extract_asset_from_passed_run(run_id=run_id, session=session)
    except RegressionAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": asset.model_dump()}


@router.post("/{asset_id}/approve")
async def approve_asset_endpoint(
    asset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    await require_project_role(
        getattr(request.state, "user", None), asset.project_id, "reviewer", session
    )
    return {"data": (await approve_asset(asset_id=asset_id, session=session)).model_dump()}


@router.patch("/{asset_id}")
async def repair_asset_endpoint(
    asset_id: str,
    body: RepairAssetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    await require_project_role(
        getattr(request.state, "user", None), asset.project_id, "reviewer", session
    )
    try:
        repaired = await repair_asset(
            asset_id=asset_id,
            session=session,
            status=body.status,
            action_plan=body.action_plan,
            locator_candidates=body.locator_candidates,
            assertions=body.assertions,
        )
    except RegressionAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": repaired.model_dump()}


@router.post("/{asset_id}/replay", status_code=202)
async def replay_asset(
    asset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    body: ReplayAssetRequest | None = None,
) -> dict:
    body = body or ReplayAssetRequest()
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    await require_project_role(
        getattr(request.state, "user", None), asset.project_id, "reviewer", session
    )
    if asset.status != "approved":
        raise HTTPException(status_code=409, detail="asset must be approved before replay")
    try:
        run = await create_run_row(case_id=asset.case_id, env=body.env, session=session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await record_replay_started(asset=asset, replay_run=run, session=session)
    _ = body.timeout_seconds
    kick_off_asset_replay(run.run_id)
    return {"data": run.model_dump()}
