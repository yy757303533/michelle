"""FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app import __version__
from app.api import api_router
from app.config import settings
from app.db import init_db
from app.obs import (
    EVENTS,
    bind_request_context,
    get_logger,
    setup_logging,
    setup_tracing,
)

setup_logging()
log = get_logger("app.main")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Bind a trace_id (from OTel) into log context for the duration of the request."""

    async def dispatch(self, request: Request, call_next):
        from uuid import uuid4

        # Generate a request_id; OTel will provide its own trace_id which our
        # logger picks up via _add_trace_id processor.
        request_id = uuid4().hex
        bind_request_context(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request.failed")
            raise
        response.headers["X-Request-Id"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(EVENTS.APP_STARTED.name, version=__version__, env=settings.app_env)
    await init_db()

    # Wire internal business hooks (run.failed → auto-diagnose, etc.)
    from app.agent.hooks import install_default_hooks

    install_default_hooks()

    yield
    log.info(EVENTS.APP_SHUTDOWN.name)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Michelle",
        description="AI-native Web Test Platform",
        version=__version__,
        lifespan=lifespan,
    )

    setup_tracing(app)

    # CORS for the Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(TraceIdMiddleware)

    app.include_router(api_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "env": settings.app_env,
            "providers": {
                "claude": True,
                "minimax": settings.has_minimax,
                "flywheel": settings.has_flywheel,
                "logfire": settings.has_logfire,
            },
        }

    @app.exception_handler(Exception)
    async def unhandled_exc_handler(_request: Request, exc: Exception):
        log.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": str(exc)[:200]}},
        )

    return app


app = create_app()
