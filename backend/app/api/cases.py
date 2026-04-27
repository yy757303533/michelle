"""Test case CRUD + review workflow. Day 7-8 will fill these in."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db import get_session
from app.models import TestCase

router = APIRouter()


@router.get("/")
async def list_cases(
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    project_id: str | None = None,
) -> dict:
    stmt = select(TestCase)
    if status:
        stmt = stmt.where(TestCase.review_status == status)
    if project_id:
        stmt = stmt.where(TestCase.project_id == project_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [r.model_dump() for r in rows], "count": len(rows)}
