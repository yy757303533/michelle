"""Tests for retry-on-transient + concurrency semaphore."""

from __future__ import annotations

import asyncio

import pytest

from app.services.run_orchestrator import _looks_transient

# ── _looks_transient ───────────────────────────────────────────────────────


def test_transient_includes_timeouts_and_dom_races():
    assert _looks_transient("claude CLI timed out after 180s") is True
    assert _looks_transient("element is not stable") is True
    assert _looks_transient("stale element reference") is True
    assert _looks_transient("navigation timeout") is True


def test_transient_rejects_unrelated_errors():
    assert _looks_transient("expected 'login successful' but got 'access denied'") is False
    assert _looks_transient("404 not found") is False
    assert _looks_transient("") is False
    assert _looks_transient(None) is False


# ── concurrency semaphore ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semaphore_caps_simultaneous_runs(monkeypatch):
    """Verify the concurrency limiter actually caps simultaneous _safe_execute calls."""
    from app.services import run_orchestrator as ro

    # Reset the singleton + clamp it small
    monkeypatch.setattr(ro, "_run_limiter", None)
    monkeypatch.setattr(ro, "MAX_CONCURRENT_RUNS", 2)

    in_flight = 0
    peak = 0

    async def fake_execute_case(**_kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1

        from app.models import Run

        return Run(
            run_id=_kw["run_id"],
            trace_id="t",
            project_id="p",
            case_id="c",
            case_version=1,
            env="x",
            status="passed",
        )

    monkeypatch.setattr(ro, "execute_case", fake_execute_case)

    async def noop_persist_abort(**_kw):
        pass

    async def noop_classify(**_kw):
        pass

    async def noop_mark(**_kw):
        pass

    monkeypatch.setattr(ro, "_persist_abort", noop_persist_abort)
    monkeypatch.setattr(ro, "_classify_and_persist", noop_classify)
    monkeypatch.setattr(ro, "_mark_status", noop_mark)

    tasks = [ro.kick_off(case_id="c", run_id=str(i), env="x", timeout_seconds=10) for i in range(5)]
    await asyncio.gather(*tasks)

    assert peak <= 2, f"semaphore breach: peak={peak} (limit=2)"
