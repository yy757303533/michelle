"""Runtime-mutable settings — knobs the operator can tune from the dashboard
without restarting the backend. Currently exposes:

- `max_concurrent_runs`: semaphore cap for parallel test runs.

Reads fall back to the .env-level default when the DB row is absent, so a
fresh database boots with sensible values."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import RuntimeSetting
from app.obs import get_logger

router = APIRouter()
log = get_logger(__name__)


# Whitelist of mutable knobs, with type coercion + bounds. Anything not in
# this dict is ignored even if it shows up in the DB (forward-compat) or in
# a PUT request (rejected with 422).
_KNOBS: dict[str, dict[str, Any]] = {
    "max_concurrent_runs": {
        "type": int,
        "min": 1,
        "max": 32,
        "default_attr": "max_concurrent_runs",
        "describe": (
            "How many test cases can execute simultaneously. Each run is one "
            "Chromium + one claude subprocess (~250MB RAM)."
        ),
    },
}


class SettingsUpdate(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=32)


def _env_default(knob: str) -> Any:
    return getattr(settings, _KNOBS[knob]["default_attr"])


async def _read_knob(session: AsyncSession, knob: str) -> Any:
    row = await session.get(RuntimeSetting, knob)
    if row is None:
        return _env_default(knob)
    spec = _KNOBS[knob]
    try:
        return spec["type"](row.value)
    except (TypeError, ValueError):
        return _env_default(knob)


async def get_max_concurrent_runs(session: AsyncSession) -> int:
    """Free-standing helper so run_orchestrator can read the live value
    without going through HTTP."""
    return int(await _read_knob(session, "max_concurrent_runs"))


@router.get("/runtime")
async def get_runtime_settings(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return all knobs with current value + env-default + bounds + describe."""
    out: dict[str, dict[str, Any]] = {}
    for knob, spec in _KNOBS.items():
        out[knob] = {
            "value": await _read_knob(session, knob),
            "default": _env_default(knob),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "describe": spec.get("describe", ""),
        }
    return {"data": out}


@router.put("/runtime")
async def update_runtime_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upsert any knobs present in the body. Unknown keys (anything not in
    SettingsUpdate) are rejected by Pydantic; null values are ignored so
    callers can update one knob at a time without sending the others."""
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=422, detail="no settings provided")

    for knob, value in payload.items():
        existing = await session.get(RuntimeSetting, knob)
        if existing:
            existing.value = str(value)
            existing.updated_at = datetime.now(UTC)
        else:
            session.add(RuntimeSetting(key=knob, value=str(value)))
        log.info("settings.runtime_updated", key=knob, value=str(value))
    await session.commit()
    return await get_runtime_settings(session=session)
