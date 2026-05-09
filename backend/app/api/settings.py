"""HTTP surface for runtime-mutable platform settings.

Thin REST wrapper around `app.runtime_config`. All actual logic (read,
write, type coercion, env defaults, knob whitelist) lives in
`runtime_config`; this module only handles request validation and JSON
shape. The dependency arrow points api → runtime_config; nothing in
runtime_config or services should ever reach back into this module."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit
from app.db import get_session
from app.obs import get_logger
from app.runtime_config import snapshot, update_many

router = APIRouter()
log = get_logger(__name__)


class SettingsUpdate(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=32)
    headless: bool | None = None
    executor_loop: Literal["auto", "generic_openai", "claude_cli"] | None = None
    case_generation_provider: (
        Literal[
            "auto",
            "claude-cli",
            "codex-cli",
        ]
        | None
    ) = None
    case_generation_preflight_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    case_execution_provider: (
        Literal[
            "auto",
            "claude-cli",
            "codex-cli",
        ]
        | None
    ) = None
    diagnosis_provider: (
        Literal[
            "auto",
            "claude-cli",
            "codex-cli",
        ]
        | None
    ) = None
    email_enabled: bool | None = None
    email_on_run_completed: bool | None = None
    email_on_diagnosis_generated: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_use_tls: bool | None = None
    smtp_use_ssl: bool | None = None
    email_subject_prefix: str | None = None
    webhook_enabled: bool | None = None
    webhook_url: str | None = None
    webhook_kind: Literal["generic", "feishu", "wecom"] | None = None
    artifact_retention_days: int | None = Field(default=None, ge=1, le=365)


class ArtifactCleanupRequest(BaseModel):
    retention_days: int = Field(ge=1, le=365)
    dry_run: bool = True


@router.get("/runtime")
async def get_runtime_settings(session: AsyncSession = Depends(get_session)) -> dict:
    return {"data": await snapshot(session)}


@router.put("/runtime")
async def update_runtime_settings(
    body: SettingsUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upsert any knobs present in the body. Unknown keys are rejected by
    Pydantic; null values are ignored so callers can update one knob at
    a time without sending the others."""
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=422, detail="no settings provided")
    log.info("settings.runtime_updated", keys=list(payload.keys()))
    data = await update_many(session, payload)
    await audit(
        actor=getattr(request.state, "user", None),
        action="settings.runtime_updated",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="settings",
        target_id="runtime",
        detail="keys=" + ",".join(sorted(payload)),
        session=session,
    )
    await session.commit()
    return {"data": data}


@router.post("/email/test")
async def send_test_email(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.email_notifications import send_test_email

    result = await send_test_email(session=session)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    await audit(
        actor=getattr(request.state, "user", None),
        action="settings.email_test_sent",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="settings",
        target_id="email",
        session=session,
    )
    await session.commit()
    return {"data": result}


@router.post("/webhook/test")
async def send_test_webhook(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.email_notifications import send_test_webhook

    result = await send_test_webhook(session=session)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    await audit(
        actor=getattr(request.state, "user", None),
        action="settings.webhook_test_sent",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="settings",
        target_id="webhook",
        session=session,
    )
    await session.commit()
    return {"data": result}


@router.post("/artifacts/cleanup")
async def cleanup_artifacts_endpoint(
    body: ArtifactCleanupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.services.artifact_cleanup import cleanup_artifacts

    result = await cleanup_artifacts(
        session=session,
        retention_days=body.retention_days,
        dry_run=body.dry_run,
    )
    await audit(
        actor=getattr(request.state, "user", None),
        action="settings.artifacts_cleanup_dry_run"
        if body.dry_run
        else "settings.artifacts_cleanup_executed",
        method=request.method,
        path=request.url.path,
        status_code=200,
        target_type="artifacts",
        target_id="cleanup",
        detail=(
            f"retention_days={body.retention_days}; dry_run={body.dry_run}; "
            f"candidate_runs={len(result.candidates)}; deleted_runs={result.deleted_runs}; "
            f"deleted_bytes={result.deleted_bytes}"
        ),
        session=session,
    )
    await session.commit()
    return {"data": result.to_dict()}


@router.get("/selfcheck")
async def selfcheck(
    include_mcp_probe: bool = Query(default=False),
    include_llm_probe: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    import shutil

    from app.agent.executor import resolve_executor_status
    from app.agent.mcp_stdio import probe_playwright_mcp
    from app.auth import admin_security_findings
    from app.db import database_summary
    from app.llm import LLMError, get_gateway
    from app.runtime_config import get_email_config

    runtime = await snapshot(session)
    email = await get_email_config(session)
    executor = await resolve_executor_status(session)
    db = database_summary()
    admin_findings = admin_security_findings()
    mcp_probe = None
    if include_mcp_probe and shutil.which("npx") is not None:
        mcp_probe = await probe_playwright_mcp(timeout_seconds=60)
    llm_probe = None
    if include_llm_probe:
        try:
            result = await get_gateway().chat(
                "Reply with the single word: ok",
                prompt_version="selfcheck_v1",
                max_tokens=10,
                timeout_seconds=20,
            )
            llm_probe = {
                "ok": True,
                "detail": f"{result.provider}/{result.model} {result.latency_ms}ms",
                "elapsed_ms": result.latency_ms,
            }
        except LLMError as exc:
            llm_probe = {
                "ok": False,
                "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                "elapsed_ms": None,
            }

    checks = [
        {"name": "backend", "ok": True, "detail": "FastAPI responding"},
        {
            "name": "admin_security",
            "ok": not admin_findings,
            "detail": "ok" if not admin_findings else "; ".join(admin_findings),
        },
        {
            "name": "database",
            "ok": True,
            "detail": f"{db['dialect']} via {db['driver']} ({db['url']})",
        },
        {
            "name": "executor",
            "ok": executor.status == "ready",
            "detail": executor.detail,
        },
        {
            "name": "playwright_mcp_prereq",
            "ok": shutil.which("npx") is not None,
            "detail": "npx available" if shutil.which("npx") else "npx missing",
        },
        {
            "name": "playwright_mcp_probe",
            "ok": bool(mcp_probe and mcp_probe.get("ok")),
            "detail": (
                str(mcp_probe.get("detail"))
                if mcp_probe
                else "not run; pass include_mcp_probe=true"
            ),
            "elapsed_ms": mcp_probe.get("elapsed_ms") if mcp_probe else None,
        },
        {
            "name": "llm_probe",
            "ok": bool(llm_probe and llm_probe.get("ok")),
            "detail": (
                str(llm_probe.get("detail"))
                if llm_probe
                else "not run; pass include_llm_probe=true"
            ),
            "elapsed_ms": llm_probe.get("elapsed_ms") if llm_probe else None,
        },
        {
            "name": "smtp",
            "ok": bool(email.get("host") and email.get("from_addr") and email.get("to_addrs")),
            "detail": "configured" if email.get("host") else "not configured",
        },
        {
            "name": "webhook",
            "ok": bool(email.get("webhook_enabled") and email.get("webhook_url")),
            "detail": "configured" if email.get("webhook_url") else "not configured",
        },
    ]
    return {"data": {"checks": checks, "runtime": runtime}}
