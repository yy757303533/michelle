"""Day 9: tests for failure heuristic + retry-on-transient + concurrency semaphore."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.trace_parser import ParsedRun, RunSummary
from app.agent.trace_parser import StepEvent as ParsedStep
from app.services.run_orchestrator import (
    _looks_transient,
    heuristic_classify,
)


# ── heuristic_classify ─────────────────────────────────────────────────────


def _parsed(steps: list[ParsedStep] | None = None) -> ParsedRun:
    return ParsedRun(
        steps=steps or [],
        summary=RunSummary(
            success=False, final_text="", parsed_result=None,
            duration_ms=0, num_turns=0, cost_usd=None,
        ),
    )


def _err_step(text: str) -> ParsedStep:
    return ParsedStep(
        step_index=0, tool_name="x", tool_full_name="x", tool_args={},
        tool_use_id="t", is_playwright=True, result_is_error=True, result_text=text,
    )


def test_classify_env_issue_from_run_error():
    assert heuristic_classify(_parsed(), "ECONNREFUSED 172.25.17.105:5000") == "env_issue"


def test_classify_env_issue_from_step_error():
    s = _err_step("net::ERR_CONNECTION_REFUSED at navigate")
    assert heuristic_classify(_parsed([s]), None) == "env_issue"


def test_classify_flaky_from_dom_race():
    s = _err_step("element is not stable, click was intercepted by overlay")
    assert heuristic_classify(_parsed([s]), None) == "flaky"


def test_classify_real_bug_when_no_known_pattern():
    s = _err_step("expected 'login successful' but got 'access denied'")
    assert heuristic_classify(_parsed([s]), None) == "real_bug"


def test_classify_returns_none_when_no_evidence():
    assert heuristic_classify(_parsed(), None) is None


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
    """Verify _run_semaphore actually limits concurrent _safe_execute calls."""
    from app.services import run_orchestrator as ro

    # Reset the singleton + clamp it small
    monkeypatch.setattr(ro, "_run_semaphore", None)
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
            run_id=_kw["run_id"], trace_id="t", project_id="p", case_id="c",
            case_version=1, env="x", status="passed",
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

    tasks = [
        ro.kick_off(case_id="c", run_id=str(i), env="x", timeout_seconds=10)
        for i in range(5)
    ]
    await asyncio.gather(*tasks)

    assert peak <= 2, f"semaphore breach: peak={peak} (limit=2)"
