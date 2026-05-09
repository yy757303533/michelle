"""LLM-related endpoints.

GET  /api/llm/health     — config presence per provider (no LLM calls)
GET  /api/llm/runner_status  — which execution loop will runs use?
POST /api/llm/probe      — actually fire a tiny completion via prefer'd provider
                            (returns provider, latency, tokens; for dashboard smoke)
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.agent.claude_env import build_claude_subprocess_env
from app.agent.executor import resolve_executor_status
from app.config import settings
from app.db import get_session
from app.llm import (
    LLMError,
    get_gateway,
)
from app.models import LLMCall
from app.obs import get_logger

router = APIRouter()
_log = get_logger(__name__)


class ProbeRequest(BaseModel):
    prefer: str | None = None
    skip: list[str] | None = None


class ProbeAllRequest(BaseModel):
    providers: list[str] | None = None


@router.get("/health")
async def llm_health() -> dict:
    gw = get_gateway()
    return {
        "data": await gw.health(),
        "available_providers": gw.available_providers,
    }


@router.get("/runner_status")
async def llm_runner_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Resolve the effective execution loop without spending model tokens."""

    resolved = await resolve_executor_status(session)
    env = build_claude_subprocess_env(michelle_run=True)
    base = env.get("ANTHROPIC_BASE_URL", "") or ""
    model = env.get("ANTHROPIC_MODEL", "") or ""

    status = resolved.status
    detail = resolved.detail
    latency_ms = 0

    # Only the legacy Claude CLI path may depend on an Anthropic-compatible
    # local proxy. We probe that proxy cheaply, but never run `claude -p`.
    if resolved.status == "ready" and resolved.resolved_loop == "claude_cli" and base:
        status, detail, latency_ms = await _probe_anthropic_base(base)

    mode = resolved.resolved_loop or "unavailable"
    if mode == "claude_cli" and not base:
        mode = "claude_cli_subscription"
    return {
        "data": {
            "status": status,
            "mode": mode,
            "configured_loop": resolved.configured_loop,
            "resolved_loop": resolved.resolved_loop,
            "generic_available": resolved.generic_available,
            "generic_providers": resolved.generic_providers,
            "claude_cli_available": resolved.claude_cli_available,
            "npx_available": resolved.npx_available,
            "base_url": base,
            "model": model,
            "detail": detail,
            "latency_ms": latency_ms,
        }
    }


async def _probe_anthropic_base(base: str) -> tuple[str, str, int]:
    parsed = urlparse(base)
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    probe_url = f"{base.rstrip('/')}/health/liveliness" if is_local else base

    import time

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            resp = await client.get(probe_url)
        return "ready", f"HTTP {resp.status_code}", int((time.monotonic() - t0) * 1000)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        latency_ms = int((time.monotonic() - t0) * 1000)
        return (
            ("down", "connection refused", latency_ms)
            if is_local
            else ("ready", "remote gateway", latency_ms)
        )
    except (httpx.ReadTimeout, httpx.PoolTimeout, TimeoutError):
        return "starting", "timeout — likely starting up", int((time.monotonic() - t0) * 1000)
    except httpx.HTTPError as exc:
        return (
            "down",
            f"{type(exc).__name__}: {str(exc)[:120]}",
            int((time.monotonic() - t0) * 1000),
        )


@router.post("/probe")
async def llm_probe(req: ProbeRequest) -> dict:
    gw = get_gateway()
    try:
        timeout_seconds = _probe_timeout_seconds(req.prefer)
        if req.prefer:
            client = gw.get(req.prefer)
            if client is None:
                return {
                    "data": {
                        "ok": False,
                        "provider": req.prefer,
                        "error_type": "ProviderUnavailable",
                        "error": f"{req.prefer} is not available; check /api/llm/health",
                    }
                }
            result = await client.chat(
                "Reply with the single word: ok",
                prompt_version="probe_v1",
                max_tokens=10,
                timeout_seconds=timeout_seconds,
            )
        else:
            result = await gw.chat(
                "Reply with the single word: ok",
                prompt_version="probe_v1",
                skip=req.skip,
                max_tokens=10,
                timeout_seconds=timeout_seconds,
            )
        return {
            "data": {
                "ok": True,
                "provider": result.provider,
                "model": result.model,
                "text": result.text[:200],
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
            }
        }
    except LLMError as e:
        return {
            "data": {
                "ok": False,
                "error": str(e)[:300],
                "provider": getattr(e, "provider", None),
                "error_type": type(e).__name__,
            }
        }


@router.post("/probe_all")
async def llm_probe_all(req: ProbeAllRequest) -> dict:
    gw = get_gateway()
    requested = set(req.providers or [])
    rows = []
    for provider in gw.available_providers:
        if requested and provider not in requested:
            continue
        client = gw.get(provider)
        if client is None:
            continue
        try:
            result = await client.chat(
                "Reply with the single word: ok",
                prompt_version="probe_all_v1",
                max_tokens=10,
                timeout_seconds=_probe_timeout_seconds(provider),
            )
            rows.append(
                {
                    "provider": provider,
                    "ok": True,
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "text": result.text[:80],
                }
            )
        except LLMError as exc:
            rows.append(
                {
                    "provider": provider,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )
    return {"data": rows}


def _probe_timeout_seconds(provider: str | None) -> int:
    if provider == "codex-cli":
        return settings.codex_timeout_seconds
    return 30


@router.get("/metrics")
async def llm_metrics(
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (await session.execute(select(LLMCall).order_by(desc(LLMCall.created_at)).limit(limit)))
        .scalars()
        .all()
    )
    by_provider: dict[str, dict] = {}
    for row in rows:
        item = by_provider.setdefault(
            row.provider,
            {
                "provider": row.provider,
                "calls": 0,
                "success": 0,
                "failed": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms_total": 0,
                "last_error": "",
            },
        )
        item["calls"] += 1
        item["success" if row.ok else "failed"] += 1
        item["input_tokens"] += row.input_tokens
        item["output_tokens"] += row.output_tokens
        item["cost_usd"] += row.cost_usd or 0
        item["latency_ms_total"] += row.latency_ms
        if not row.ok and not item["last_error"]:
            item["last_error"] = f"{row.error_type}: {row.error_message}"[:300]
    providers = []
    for item in by_provider.values():
        item["failure_rate"] = item["failed"] / item["calls"] if item["calls"] else 0
        item["avg_latency_ms"] = (
            int(item["latency_ms_total"] / item["calls"]) if item["calls"] else 0
        )
        item.pop("latency_ms_total", None)
        providers.append(item)
    providers.sort(key=lambda x: x["calls"], reverse=True)
    totals = {
        "calls": sum(p["calls"] for p in providers),
        "failed": sum(p["failed"] for p in providers),
        "input_tokens": sum(p["input_tokens"] for p in providers),
        "output_tokens": sum(p["output_tokens"] for p in providers),
        "cost_usd": sum(p["cost_usd"] for p in providers),
    }
    totals["failure_rate"] = totals["failed"] / totals["calls"] if totals["calls"] else 0
    latest_errors = [row.model_dump() for row in rows if not row.ok][:10]
    # Keep a cheap DB aggregate here so tests cover the table even when no rows
    # are present; avoids stale Alembic/table wiring going unnoticed.
    total_rows = (await session.execute(select(func.count()).select_from(LLMCall))).scalar_one()
    return {
        "data": {
            "total_rows": total_rows,
            "window": len(rows),
            "totals": totals,
            "providers": providers,
            "latest_errors": latest_errors,
        }
    }


@router.delete("/metrics")
async def clear_llm_metrics(session: AsyncSession = Depends(get_session)) -> dict:
    result = await session.execute(delete(LLMCall))
    await session.commit()
    return {"data": {"deleted": int(result.rowcount or 0)}}
