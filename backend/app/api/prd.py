"""PRD upload + chapter diff endpoints. Day 4 will fill these in."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_prds() -> dict:
    """List uploaded PRDs. Stub — Day 4."""
    return {"data": [], "trace_id": None}
