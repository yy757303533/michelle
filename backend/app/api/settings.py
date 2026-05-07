"""HTTP surface for runtime-mutable platform settings.

Thin REST wrapper around `app.runtime_config`. All actual logic (read,
write, type coercion, env defaults, knob whitelist) lives in
`runtime_config`; this module only handles request validation and JSON
shape. The dependency arrow points api → runtime_config; nothing in
runtime_config or services should ever reach back into this module."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.obs import get_logger
from app.runtime_config import snapshot, update_many

router = APIRouter()
log = get_logger(__name__)


class SettingsUpdate(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=32)
    headless: bool | None = None
    executor_loop: Literal["auto", "generic_openai", "claude_cli"] | None = None


@router.get("/runtime")
async def get_runtime_settings(session: AsyncSession = Depends(get_session)) -> dict:
    return {"data": await snapshot(session)}


@router.put("/runtime")
async def update_runtime_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upsert any knobs present in the body. Unknown keys are rejected by
    Pydantic; null values are ignored so callers can update one knob at
    a time without sending the others."""
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=422, detail="no settings provided")
    log.info("settings.runtime_updated", keys=list(payload.keys()))
    return {"data": await update_many(session, payload)}
