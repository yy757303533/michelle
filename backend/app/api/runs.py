"""Run lifecycle endpoints. Day 6 will fill these in."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_runs() -> dict:
    return {"data": [], "trace_id": None}
