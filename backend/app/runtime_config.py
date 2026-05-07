"""Live-mutable platform settings — the source of truth.

Two-layer config model:

  - **Bootstrap defaults** live in `app.config.Settings` (`.env` / env vars).
    Read at process start; immutable for the process's lifetime.

  - **Runtime overrides** live in the `runtime_settings` SQLite table and
    can change while the backend is running (Dashboard's "Platform
    settings" panel). This module is where reads/writes go.

Application code (e.g. `run_orchestrator`) reads via the typed helpers
here. The HTTP layer (`app.api.settings`) is a thin REST wrapper around
this module — never the other way around. Previously the dependency was
inverted (orchestrator imported from api.settings), which was a code
smell: a service should never reach into a route module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import db as _db
from app.config import settings
from app.models import RuntimeSetting

EXECUTOR_LOOPS = {"auto", "generic_openai", "claude_cli"}


def _coerce_bool(v: Any) -> bool:
    return str(v).lower() in {"true", "1", "yes", "on"}


def _coerce_executor_loop(v: Any) -> str:
    value = str(v or "").strip()
    if value not in EXECUTOR_LOOPS:
        raise ValueError(f"unknown executor_loop: {value}")
    return value


# Whitelist of mutable knobs. Anything not here is invisible to the API
# layer (forward-compat for new knobs without rebuilding the surface,
# but the writer still needs to add an entry here to be storable).
KNOBS: dict[str, dict[str, Any]] = {
    "max_concurrent_runs": {
        "type": int,
        "min": 1,
        "max": 32,
        "describe": (
            "How many test cases can execute simultaneously. Each run is one "
            "Chromium + one claude subprocess (~250MB RAM)."
        ),
    },
    "headless": {
        "type": _coerce_bool,
        "describe": (
            "Run Chromium headless (no visible window). Turn OFF to watch the "
            "agent drive the browser live — useful for debugging selector "
            "drift or auth flows. Slower + breaks if you don't have a display."
        ),
    },
    "executor_loop": {
        "type": _coerce_executor_loop,
        "choices": ["auto", "generic_openai", "claude_cli"],
        "describe": (
            "Execution loop strategy. Auto prefers Michelle's generic "
            "OpenAI-compatible loop, and only falls back to Claude CLI when "
            "no generic provider is configured."
        ),
    },
}


# Per-knob bootstrap defaults: where to find the env-default value when
# no DB row exists yet (fresh install, in-memory test DB, etc).
_BOOTSTRAP_DEFAULTS: dict[str, Any] = {
    "max_concurrent_runs": lambda: settings.max_concurrent_runs,
    "headless": lambda: True,
    "executor_loop": lambda: (
        settings.executor_loop if settings.executor_loop in EXECUTOR_LOOPS else "auto"
    ),
}


def env_default(knob: str) -> Any:
    """Return the bootstrap default for `knob` (env-derived)."""
    fn = _BOOTSTRAP_DEFAULTS.get(knob)
    return fn() if fn else None


async def _read_raw(session: AsyncSession, knob: str) -> Any:
    """Read a knob, falling back to env default. Returns the typed value."""
    row = await session.get(RuntimeSetting, knob)
    if row is None:
        return env_default(knob)
    spec = KNOBS.get(knob)
    if spec is None:
        # Unknown knob persisted by an older version — return raw text
        # so a later read after re-deploy still finds something.
        return row.value
    try:
        return spec["type"](row.value)
    except (TypeError, ValueError):
        return env_default(knob)


# ── Typed accessors used by the application layer ────────────────────────


async def get_max_concurrent_runs(session: AsyncSession | None = None) -> int:
    """Live concurrency cap. Pass a session to read inside an existing
    transaction; otherwise opens its own."""
    if session is not None:
        return int(await _read_raw(session, "max_concurrent_runs"))
    async with _db.async_session_maker() as s:
        return int(await _read_raw(s, "max_concurrent_runs"))


async def get_headless(session: AsyncSession | None = None) -> bool:
    """Whether new browser sessions should run without a visible window."""
    if session is not None:
        return bool(await _read_raw(session, "headless"))
    async with _db.async_session_maker() as s:
        return bool(await _read_raw(s, "headless"))


async def get_executor_loop(session: AsyncSession | None = None) -> str:
    """Configured execution loop strategy: auto | generic_openai | claude_cli."""
    if session is not None:
        return str(await _read_raw(session, "executor_loop"))
    async with _db.async_session_maker() as s:
        return str(await _read_raw(s, "executor_loop"))


# ── Read/write surface used by the HTTP layer ────────────────────────────


async def snapshot(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Return all whitelisted knobs with current value + default + bounds.

    Shape matches what the dashboard panel expects. Adding a knob = one
    KNOBS entry + one _BOOTSTRAP_DEFAULTS entry; the HTTP surface picks
    it up automatically."""
    out: dict[str, dict[str, Any]] = {}
    for key, spec in KNOBS.items():
        out[key] = {
            "value": await _read_raw(session, key),
            "default": env_default(key),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "choices": spec.get("choices"),
            "describe": spec.get("describe", ""),
        }
    return out


async def update_many(session: AsyncSession, updates: dict[str, Any]) -> dict[str, dict]:
    """Upsert multiple knobs in one go. Unknown keys are silently dropped
    (HTTP layer does the strict validation via Pydantic Literal types)."""
    now = datetime.now(UTC)
    for key, value in updates.items():
        if key not in KNOBS:
            continue
        existing = await session.get(RuntimeSetting, key)
        if existing is not None:
            existing.value = str(value)
            existing.updated_at = now
        else:
            session.add(RuntimeSetting(key=key, value=str(value)))
    await session.commit()
    return await snapshot(session)
