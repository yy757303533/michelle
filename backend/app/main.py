"""FastAPI entrypoint."""

from __future__ import annotations

import time
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
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request.failed")
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        user = getattr(request.state, "user", None) or {}
        log.info(
            "http.request",
            status_code=response.status_code,
            duration_ms=duration_ms,
            query=request.url.query,
            actor=str(user.get("username", "")),
            actor_role=str(user.get("role", "")),
        )
        return response


class AdminTokenMiddleware(BaseHTTPMiddleware):
    """Lightweight local-admin guard.

    When ADMIN_TOKEN is set, protect all unsafe /api methods plus settings
    reads. This keeps default local dev frictionless while giving shared
    deployments a simple gate around credentials, runtime knobs, and actions.
    """

    async def dispatch(self, request: Request, call_next):
        from app.auth import ROLE_RANK, user_from_request

        if settings.app_env == "test":
            return await call_next(request)

        user = await user_from_request(request)
        request.state.user = user
        auth_enabled = True
        if auth_enabled and request.url.path.startswith("/api"):
            public = request.url.path in {"/api/auth/login", "/api/auth/logout"}
            role = str((user or {}).get("role", ""))
            needs_auth = request.method != "OPTIONS"
            if request.url.path.startswith("/api/auth/users") or request.url.path.startswith(
                "/api/auth/audit"
            ):
                needs_auth = True
                required_rank = ROLE_RANK["admin"]
            elif request.url.path.startswith("/api/settings"):
                required_rank = ROLE_RANK["admin"]
            elif request.url.path.startswith("/api/dev-context"):
                required_rank = ROLE_RANK["admin"]
            else:
                required_rank = (
                    ROLE_RANK["viewer"]
                    if request.method in {"GET", "HEAD"}
                    else ROLE_RANK["reviewer"]
                )
            if not public and needs_auth and not user:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "login required"}},
                )
            if not public and user and ROLE_RANK.get(role, 0) < required_rank:
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "forbidden", "message": "insufficient role"}},
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(EVENTS.APP_STARTED.name, version=__version__, env=settings.app_env)
    await init_db()

    # Wire internal business hooks (run.failed → auto-diagnose, etc.)
    from app.agent.hooks import install_default_hooks
    from app.auth import ensure_bootstrap_admin

    install_default_hooks()
    await ensure_bootstrap_admin()

    # Startup heal: any run still marked `running`/`pending` was orphaned
    # by a previous process (uvicorn --reload, crash, SIGKILL, …). The
    # asyncio task hosting it is gone; safe to declare aborted so the UI
    # shows truth and the case becomes rerunnable.
    from app.services.run_lifecycle import heal_stale_runs

    healed = await heal_stale_runs(reason="previous backend instance exited mid-run")
    if healed:
        log.info("run.lifecycle.startup_healed", count=healed)

    yield

    # Shutdown heal: best-effort cleanup before exit. Covers Ctrl+C; SIGKILL
    # bypasses this path, but the next startup heal will catch those.
    try:
        healed = await heal_stale_runs(reason="backend shutting down")
        if healed:
            log.info("run.lifecycle.shutdown_healed", count=healed)
    except Exception as exc:  # noqa: BLE001 — never block shutdown on this
        log.warning("run.lifecycle.shutdown_heal_failed", error=str(exc)[:200])

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
    app.add_middleware(AdminTokenMiddleware)

    app.include_router(api_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "env": settings.app_env,
            "auth_required": True,
            "providers": {
                "claude-cli": True,
                "codex-cli": settings.codex_enabled,
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
