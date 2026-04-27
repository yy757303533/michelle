"""API routes — registered with the FastAPI app from main.py."""

from fastapi import APIRouter

from app.api import cases, diagnosis, llm, prd, runs

api_router = APIRouter(prefix="/api")
api_router.include_router(prd.router, prefix="/prd", tags=["prd"])
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(diagnosis.router, prefix="/diagnosis", tags=["diagnosis"])
api_router.include_router(llm.router, prefix="/llm", tags=["llm"])

__all__ = ["api_router"]
