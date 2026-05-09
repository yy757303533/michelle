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
LLM_PROVIDER_CHOICES = {
    "auto",
    "claude-cli",
    "codex-cli",
}
EXECUTION_PROVIDER_CHOICES = {
    "auto",
    "claude-cli",
    "codex-cli",
}


def _coerce_bool(v: Any) -> bool:
    return str(v).lower() in {"true", "1", "yes", "on"}


def _coerce_executor_loop(v: Any) -> str:
    value = str(v or "").strip()
    if value not in EXECUTOR_LOOPS:
        raise ValueError(f"unknown executor_loop: {value}")
    return value


def _coerce_int(v: Any) -> int:
    return int(v)


def _coerce_str(v: Any) -> str:
    return str(v or "")


def _coerce_llm_provider(v: Any) -> str:
    value = str(v or "auto").strip()
    if value not in LLM_PROVIDER_CHOICES:
        raise ValueError(f"unknown LLM provider: {value}")
    return value


def _coerce_execution_provider(v: Any) -> str:
    value = str(v or "auto").strip()
    if value not in EXECUTION_PROVIDER_CHOICES:
        raise ValueError(f"unknown execution provider: {value}")
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
    "case_generation_provider": {
        "type": _coerce_llm_provider,
        "choices": [
            "auto",
            "claude-cli",
            "codex-cli",
        ],
        "describe": (
            "Preferred LLM provider for PRD-to-case generation. Auto follows "
            "gateway priority and fallback order."
        ),
    },
    "case_generation_preflight_timeout_seconds": {
        "type": _coerce_int,
        "min": 5,
        "max": 300,
        "describe": (
            "Seconds to wait for the PRD-to-case generation provider preflight "
            "before failing fast."
        ),
    },
    "case_execution_provider": {
        "type": _coerce_execution_provider,
        "choices": [
            "auto",
            "claude-cli",
            "codex-cli",
        ],
        "describe": (
            "Preferred execution provider. claude-cli uses Claude CLI Loop; "
            "all other providers use Michelle Loop."
        ),
    },
    "diagnosis_provider": {
        "type": _coerce_llm_provider,
        "choices": [
            "auto",
            "claude-cli",
            "codex-cli",
        ],
        "describe": "Preferred LLM provider for failed-run AI diagnosis.",
    },
    "email_enabled": {
        "type": _coerce_bool,
        "describe": "Enable SMTP email notifications.",
    },
    "email_on_run_completed": {
        "type": _coerce_bool,
        "describe": "Send an email when a run reaches its final status.",
    },
    "email_on_diagnosis_generated": {
        "type": _coerce_bool,
        "describe": "Send an email when AI diagnosis finishes.",
    },
    "smtp_host": {
        "type": _coerce_str,
        "describe": "SMTP server hostname.",
    },
    "smtp_port": {
        "type": _coerce_int,
        "min": 1,
        "max": 65535,
        "describe": "SMTP server port.",
    },
    "smtp_username": {
        "type": _coerce_str,
        "describe": "SMTP username. Leave empty if your server does not require auth.",
    },
    "smtp_password": {
        "type": _coerce_str,
        "secret": True,
        "describe": "SMTP password or app password. Stored locally in runtime settings.",
    },
    "smtp_from": {
        "type": _coerce_str,
        "describe": "Sender email address.",
    },
    "smtp_to": {
        "type": _coerce_str,
        "describe": "Recipient email addresses, separated by comma or newline.",
    },
    "smtp_use_tls": {
        "type": _coerce_bool,
        "describe": "Use STARTTLS after connecting.",
    },
    "smtp_use_ssl": {
        "type": _coerce_bool,
        "describe": "Use SMTP over SSL from the start.",
    },
    "email_subject_prefix": {
        "type": _coerce_str,
        "describe": "Prefix added to all Michelle notification subjects.",
    },
    "webhook_enabled": {
        "type": _coerce_bool,
        "describe": "Enable generic webhook notifications.",
    },
    "webhook_url": {
        "type": _coerce_str,
        "secret": True,
        "describe": "Webhook URL for Feishu/WeCom/Slack-compatible bots or custom receivers.",
    },
    "webhook_kind": {
        "type": _coerce_str,
        "choices": ["generic", "feishu", "wecom"],
        "describe": "Webhook payload style.",
    },
    "artifact_retention_days": {
        "type": _coerce_int,
        "min": 1,
        "max": 365,
        "describe": (
            "Delete run artifact directories older than this many days. "
            "Pending/running runs are always skipped."
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
    "case_generation_provider": lambda: "auto",
    "case_generation_preflight_timeout_seconds": lambda: 20,
    "case_execution_provider": lambda: "auto",
    "diagnosis_provider": lambda: "auto",
    "email_enabled": lambda: False,
    "email_on_run_completed": lambda: True,
    "email_on_diagnosis_generated": lambda: True,
    "smtp_host": lambda: "",
    "smtp_port": lambda: 587,
    "smtp_username": lambda: "",
    "smtp_password": lambda: "",
    "smtp_from": lambda: "",
    "smtp_to": lambda: "",
    "smtp_use_tls": lambda: True,
    "smtp_use_ssl": lambda: False,
    "email_subject_prefix": lambda: "[Michelle]",
    "webhook_enabled": lambda: False,
    "webhook_url": lambda: "",
    "webhook_kind": lambda: "generic",
    "artifact_retention_days": lambda: 30,
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


async def get_case_generation_provider(session: AsyncSession | None = None) -> str | None:
    """Preferred provider for PRD-to-case generation. None means gateway auto."""
    if session is not None:
        value = str(await _read_raw(session, "case_generation_provider"))
        return None if value == "auto" else value
    async with _db.async_session_maker() as s:
        value = str(await _read_raw(s, "case_generation_provider"))
        return None if value == "auto" else value


async def get_case_generation_preflight_timeout(session: AsyncSession | None = None) -> int:
    """Provider preflight timeout for PRD-to-case generation."""
    if session is not None:
        return int(await _read_raw(session, "case_generation_preflight_timeout_seconds"))
    async with _db.async_session_maker() as s:
        return int(await _read_raw(s, "case_generation_preflight_timeout_seconds"))


async def get_case_execution_provider(session: AsyncSession | None = None) -> str | None:
    """Preferred provider for generic case execution. None means gateway auto."""
    if session is not None:
        value = str(await _read_raw(session, "case_execution_provider"))
        return None if value == "auto" else value
    async with _db.async_session_maker() as s:
        value = str(await _read_raw(s, "case_execution_provider"))
        return None if value == "auto" else value


async def get_diagnosis_provider(session: AsyncSession | None = None) -> str | None:
    """Preferred provider for failed-run diagnosis. None means diagnosis default."""
    if session is not None:
        value = str(await _read_raw(session, "diagnosis_provider"))
        return None if value == "auto" else value
    async with _db.async_session_maker() as s:
        value = str(await _read_raw(s, "diagnosis_provider"))
        return None if value == "auto" else value


async def get_email_config(session: AsyncSession | None = None) -> dict[str, Any]:
    """Return typed email notification settings."""

    async def _build(s: AsyncSession) -> dict[str, Any]:
        return {
            "enabled": bool(await _read_raw(s, "email_enabled")),
            "on_run_completed": bool(await _read_raw(s, "email_on_run_completed")),
            "on_diagnosis_generated": bool(await _read_raw(s, "email_on_diagnosis_generated")),
            "host": str(await _read_raw(s, "smtp_host")),
            "port": int(await _read_raw(s, "smtp_port")),
            "username": str(await _read_raw(s, "smtp_username")),
            "password": str(await _read_raw(s, "smtp_password")),
            "from_addr": str(await _read_raw(s, "smtp_from")),
            "to_addrs": str(await _read_raw(s, "smtp_to")),
            "use_tls": bool(await _read_raw(s, "smtp_use_tls")),
            "use_ssl": bool(await _read_raw(s, "smtp_use_ssl")),
            "subject_prefix": str(await _read_raw(s, "email_subject_prefix")),
            "webhook_enabled": bool(await _read_raw(s, "webhook_enabled")),
            "webhook_url": str(await _read_raw(s, "webhook_url")),
            "webhook_kind": str(await _read_raw(s, "webhook_kind")),
        }

    if session is not None:
        return await _build(session)
    async with _db.async_session_maker() as s:
        return await _build(s)


# ── Read/write surface used by the HTTP layer ────────────────────────────


async def snapshot(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Return all whitelisted knobs with current value + default + bounds.

    Shape matches what the dashboard panel expects. Adding a knob = one
    KNOBS entry + one _BOOTSTRAP_DEFAULTS entry; the HTTP surface picks
    it up automatically."""
    out: dict[str, dict[str, Any]] = {}
    for key, spec in KNOBS.items():
        out[key] = {
            "value": "" if spec.get("secret") else await _read_raw(session, key),
            "default": env_default(key),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "choices": spec.get("choices"),
            "describe": spec.get("describe", ""),
        }
        if spec.get("secret"):
            row = await session.get(RuntimeSetting, key)
            out[key]["is_set"] = bool(row and row.value)
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
