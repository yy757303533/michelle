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
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.claude_env import build_claude_subprocess_env
from app.agent.executor import resolve_executor_status
from app.db import get_session
from app.llm import (
    LLMError,
    get_gateway,
)
from app.obs import get_logger

router = APIRouter()
_log = get_logger(__name__)


class ProbeRequest(BaseModel):
    prefer: str | None = None
    skip: list[str] | None = None


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
        result = await gw.chat(
            "Reply with the single word: ok",
            prompt_version="probe_v1",
            prefer=req.prefer,
            skip=req.skip,
            max_tokens=10,
            timeout_seconds=30,
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
