"""API routes — registered with the FastAPI app from main.py."""

from fastapi import APIRouter

from app.api import (
    auth,
    case_feedback,
    cases,
    coverage,
    diagnosis,
    llm,
    prd,
    projects,
    regression_assets,
    runs,
    settings,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(prd.router, prefix="/prd", tags=["prd"])
api_router.include_router(coverage.router, prefix="/coverage", tags=["coverage"])
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(regression_assets.router, prefix="/regression-assets", tags=["regression-assets"])
api_router.include_router(case_feedback.router, prefix="/case-draft-feedback", tags=["case-draft-feedback"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(diagnosis.router, prefix="/diagnosis", tags=["diagnosis"])
api_router.include_router(llm.router, prefix="/llm", tags=["llm"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])

__all__ = ["api_router"]
