"""Hook events — let the platform react automatically to state changes.

Day 1 implements the registration mechanism + 3 stub hooks.
Day 8-11 wires them up in services/.

These hooks are *internal* — they fire on Michelle business events
(case.approved, run.completed, diagnosis.confirmed). They are NOT the same as
Claude Code hooks (which are settings.json shell hooks that the harness fires).

External (Claude Code-side) hooks would live in `.claude/settings.local.json` if
needed, e.g. "every time a run finishes, ring a bell". Internal hooks decouple
business logic in the backend.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.obs import get_logger

_log = get_logger(__name__)

HookFn = Callable[[dict[str, Any]], Awaitable[None]]
_REGISTRY: dict[str, list[HookFn]] = {}


def register(event_name: str, fn: HookFn) -> None:
    """Register an async function to fire when an event is emitted."""
    _REGISTRY.setdefault(event_name, []).append(fn)
    _log.debug("hook.registered", hook_event=event_name, fn=fn.__name__)


async def emit(event_name: str, payload: dict[str, Any]) -> None:
    """Fire all registered handlers for `event_name`. Failures are logged, not raised."""
    handlers = _REGISTRY.get(event_name, [])
    if not handlers:
        return
    _log.debug("hook.emit", hook_event=event_name, n_handlers=len(handlers))
    results = await asyncio.gather(*(fn(payload) for fn in handlers), return_exceptions=True)
    for fn, res in zip(handlers, results, strict=True):
        if isinstance(res, Exception):
            _log.error("hook.handler_failed", hook_event=event_name, fn=fn.__name__, error=str(res))


# ── Default hook intents (bodies filled later) ──────────────────────────────


async def _on_case_approved_auto_run(payload: dict[str, Any]) -> None:
    """When a case is approved, optionally auto-trigger a run.

    Day 8: read settings flag, kick off /api/runs. Off by default.
    """
    _log.debug("hook.case_approved.auto_run.stub", case_id=payload.get("case_id"))


async def _on_run_failed_auto_diagnose(payload: dict[str, Any]) -> None:
    """When a run fails, automatically trigger AI diagnosis."""
    run_id = payload.get("run_id")
    if not run_id:
        return
    try:
        from app.db import async_session_maker
        from app.services.diagnoser import diagnose_run

        async with async_session_maker() as session:
            diag = await diagnose_run(run_id=run_id, session=session)
        _log.info(
            "hook.run_failed.auto_diagnosed",
            run_id=run_id,
            diag_id=diag.diag_id,
            category=diag.category,
            confidence=diag.confidence,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("hook.run_failed.auto_diagnose_failed", run_id=run_id, error=str(exc)[:200])


async def _on_diagnosis_confirmed_sediment(payload: dict[str, Any]) -> None:
    """When a human confirms a diagnosis, fold it into the pattern library.

    Day 11: call pattern_store.absorb. ON by default.
    """
    _log.debug("hook.diagnosis_confirmed.sediment.stub", diag_id=payload.get("diag_id"))


def install_default_hooks() -> None:
    """Wire defaults at startup."""
    register("case.approved", _on_case_approved_auto_run)
    register("run.failed", _on_run_failed_auto_diagnose)
    register("diagnosis.confirmed", _on_diagnosis_confirmed_sediment)
    _log.info("hooks.installed", count=3)
