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
    "test_design_provider": {
        "type": _coerce_llm_provider,
        "choices": [
            "auto",
            "claude-cli",
            "codex-cli",
        ],
        "describe": (
            "Preferred LLM provider for PRD-to-coverage analysis. Auto follows "
            "gateway priority and fallback order."
        ),
    },
    "test_design_preflight_timeout_seconds": {
        "type": _coerce_int,
        "min": 5,
        "max": 300,
        "describe": (
            "Seconds to wait for the PRD-to-coverage analysis provider preflight "
            "before failing fast."
        ),
    },
    "case_drafting_provider": {
        "type": _coerce_llm_provider,
        "choices": [
            "auto",
            "claude-cli",
            "codex-cli",
        ],
        "describe": (
            "Preferred LLM provider for accepted-coverage-to-case drafting. Auto follows "
            "gateway priority and fallback order."
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
    "michelle_workspace_root": {
        "type": _coerce_str,
        "describe": "External zstack-workspace root used for PRD imports and code search.",
    },
    "michelle_zdev_mcp_command": {
        "type": _coerce_str,
        "describe": "Command used to start zstack-dev-mcp, usually node.",
    },
    "michelle_zdev_mcp_args": {
        "type": _coerce_str,
        "describe": "Arguments for zstack-dev-mcp, usually the dist/index.js entrypoint.",
    },
    "michelle_zdev_mcp_cwd": {
        "type": _coerce_str,
        "describe": "Working directory for zstack-dev-mcp.",
    },
    "michelle_zdev_mcp_timeout_seconds": {
        "type": _coerce_int,
        "min": 5,
        "max": 300,
        "describe": "Timeout for zstack-dev-mcp tool calls.",
    },
    "michelle_dev_context_repos": {
        "type": _coerce_str,
        "describe": "Comma-separated workspace repos to search during failed-run diagnosis.",
    },
    "michelle_dev_context_max_files": {
        "type": _coerce_int,
        "min": 1,
        "max": 50,
        "describe": "Maximum candidate files returned by workspace code search.",
    },
    "michelle_dev_context_max_matches_per_file": {
        "type": _coerce_int,
        "min": 1,
        "max": 20,
        "describe": "Maximum code matches retained per candidate file.",
    },
    "michelle_server_logs_json": {
        "type": _coerce_str,
        "describe": "JSON whitelist of SSH server log sources for diagnosis evidence.",
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
    "test_design_provider": lambda: "auto",
    "test_design_preflight_timeout_seconds": lambda: 20,
    "case_drafting_provider": lambda: "auto",
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
    "michelle_workspace_root": lambda: settings.michelle_workspace_root,
    "michelle_zdev_mcp_command": lambda: settings.michelle_zdev_mcp_command,
    "michelle_zdev_mcp_args": lambda: settings.michelle_zdev_mcp_args,
    "michelle_zdev_mcp_cwd": lambda: settings.michelle_zdev_mcp_cwd,
    "michelle_zdev_mcp_timeout_seconds": lambda: settings.michelle_zdev_mcp_timeout_seconds,
    "michelle_dev_context_repos": lambda: settings.michelle_dev_context_repos,
    "michelle_dev_context_max_files": lambda: settings.michelle_dev_context_max_files,
    "michelle_dev_context_max_matches_per_file": lambda: (
        settings.michelle_dev_context_max_matches_per_file
    ),
    "michelle_server_logs_json": lambda: settings.michelle_server_logs_json,
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


async def get_test_design_provider(session: AsyncSession | None = None) -> str | None:
    """Preferred provider for PRD-to-coverage analysis. None means gateway auto."""
    if session is not None:
        value = str(await _read_raw(session, "test_design_provider"))
        return None if value == "auto" else value
    async with _db.async_session_maker() as s:
        value = str(await _read_raw(s, "test_design_provider"))
        return None if value == "auto" else value


async def get_test_design_preflight_timeout(session: AsyncSession | None = None) -> int:
    """Provider preflight timeout for PRD-to-coverage analysis."""
    if session is not None:
        return int(await _read_raw(session, "test_design_preflight_timeout_seconds"))
    async with _db.async_session_maker() as s:
        return int(await _read_raw(s, "test_design_preflight_timeout_seconds"))


async def get_case_drafting_provider(session: AsyncSession | None = None) -> str | None:
    """Preferred provider for accepted-coverage-to-case drafting. None means gateway auto."""
    if session is not None:
        value = str(await _read_raw(session, "case_drafting_provider"))
        return None if value == "auto" else value
    async with _db.async_session_maker() as s:
        value = str(await _read_raw(s, "case_drafting_provider"))
        return None if value == "auto" else value


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


async def get_dev_context_config(session: AsyncSession | None = None) -> dict[str, Any]:
    async def _build(s: AsyncSession) -> dict[str, Any]:
        return {
            "workspace_root": str(await _read_raw(s, "michelle_workspace_root")),
            "zdev_mcp_command": str(await _read_raw(s, "michelle_zdev_mcp_command")),
            "zdev_mcp_args": str(await _read_raw(s, "michelle_zdev_mcp_args")),
            "zdev_mcp_cwd": str(await _read_raw(s, "michelle_zdev_mcp_cwd")),
            "zdev_mcp_timeout_seconds": int(
                await _read_raw(s, "michelle_zdev_mcp_timeout_seconds")
            ),
            "code_repos": str(await _read_raw(s, "michelle_dev_context_repos")),
            "max_files": int(await _read_raw(s, "michelle_dev_context_max_files")),
            "max_matches_per_file": int(
                await _read_raw(s, "michelle_dev_context_max_matches_per_file")
            ),
            "server_logs_json": str(await _read_raw(s, "michelle_server_logs_json")),
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
