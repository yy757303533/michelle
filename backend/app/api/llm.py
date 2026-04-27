"""LLM-related endpoints.

  GET  /api/llm/health     — config presence per provider (no LLM calls)
  POST /api/llm/probe      — actually fire a tiny completion via prefer'd provider
                              (returns provider, latency, tokens; for dashboard smoke)
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

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
