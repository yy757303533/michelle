"""Project CRUD — minimal."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db import get_session
from app.models import Project

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
        existing.updated_at = datetime.now(timezone.utc)
    else:
        existing = Project(**body.model_dump())
        session.add(existing)
    await session.commit()
    return {"data": existing.model_dump()}
