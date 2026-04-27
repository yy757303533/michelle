"""Diagnosis endpoints. Day 11 will fill these in."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_diagnoses() -> dict:
    return {"data": [], "trace_id": None}
