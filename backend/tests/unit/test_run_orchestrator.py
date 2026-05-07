"""Tests for the Run Orchestrator. claude_runner is mocked at the module level
so no real LLM/browser is touched."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.agent.claude_runner import RunOutcome
from app.agent.trace_parser import ParsedRun, RunSummary
from app.agent.trace_parser import StepEvent as ParsedStep
from app.models import Project, StepEvent, TestCase
from app.services import run_orchestrator
from app.services.run_orchestrator import (
    _format_assertions,
    _format_steps,
    _infer_status,
    create_run_row,
    execute_case,
    render_execute_prompt,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
async def session(monkeypatch) -> AsyncSession:
    """Per-test in-memory DB. Patches the orchestrator's `async_session_maker`
    so background calls reuse the same engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(run_orchestrator, "async_session_maker", maker)

    from app.agent.executor import ExecutorStatus

    async def fake_executor_status(_session):
        return ExecutorStatus(
            status="ready",
            configured_loop="claude_cli",
            resolved_loop="claude_cli",
            detail="test",
            generic_available=False,
            generic_providers=[],
            claude_cli_available=True,
            npx_available=True,
        )

    monkeypatch.setattr(run_orchestrator, "resolve_executor_status", fake_executor_status)
    async with maker() as s:
        yield s


@pytest.fixture
async def seeded(session: AsyncSession) -> tuple[Project, TestCase]:
    proj = Project(project_id="demo", name="Demo", base_url="http://example.com")
    case = TestCase(
        case_id="TC-DEMO-001",
        project_id="demo",
        name="login happy path",
        intent="user logs in with valid credentials",
        module="auth",
        tags=["happy"],
        priority="P0",
        preconditions=["test account exists"],
        steps=[
            {"intent": "open login page", "expected": "form is visible"},
            {"intent": "type username admin"},
            {"intent": "click submit"},
        ],
        assertions=[{"description": "URL changes to /home"}],
        source="ai-generated",
        prompt_version="case_gen_v1",
        model_version="claude-opus",
        review_status="approved",
    )
    session.add(proj)
    session.add(case)
    await session.commit()
    return proj, case


# ── Prompt rendering helpers ───────────────────────────────────────────────


def test_format_steps_includes_expected_lines(seeded):
    _, case = seeded.__wrapped__ if hasattr(seeded, "__wrapped__") else (None, None)


def test_format_steps_basic():
    case = TestCase(
        case_id="x",
        project_id="x",
        name="x",
        intent="x",
        steps=[
            {"intent": "open page", "expected": "title shown"},
            {"intent": "click submit"},
        ],
        assertions=[],
    )
    out = _format_steps(case)
    assert "1. open page" in out
    assert "期望: title shown" in out
    assert "2. click submit" in out


def test_format_assertions_default_when_empty():
    case = TestCase(case_id="x", project_id="x", name="x", intent="x", assertions=[])
    out = _format_assertions(case)
    assert "no explicit assertions" in out


@pytest.mark.asyncio
async def test_render_execute_prompt_substitutes_fields(seeded):
    proj, case = seeded
    prompt = render_execute_prompt(case, proj)
    assert "Demo" in prompt
    assert "http://example.com" in prompt
    assert "login happy path" in prompt
    assert "open login page" in prompt
    assert "URL changes to /home" in prompt


# ── Status mapping ─────────────────────────────────────────────────────────


def _parsed(
    *,
    success: bool = True,
    parsed_result: dict | None = None,
    final_text: str = "",
    steps: list[ParsedStep] | None = None,
    error: str | None = None,
) -> ParsedRun:
    return ParsedRun(
        steps=steps or [],
        summary=RunSummary(
            success=success,
            final_text=final_text,
            parsed_result=parsed_result,
            duration_ms=1000,
            num_turns=2,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=20,
            error=error,
        ),
    )


def test_infer_status_explicit_passed():
    p = _parsed(parsed_result={"case_status": "passed"})
    assert _infer_status(p) == ("passed", None)


def test_infer_status_explicit_failed_with_summary():
    p = _parsed(
        parsed_result={"case_status": "failed", "failure_summary": "url did not change"},
        success=False,
    )
    status, err = _infer_status(p)
    assert status == "failed"
    assert err == "url did not change"


def test_infer_status_from_final_text_regex():
    p = _parsed(final_text='all done. RESULT={"case_status":"failed"}', success=False)
    status, _ = _infer_status(p)
    assert status == "failed"


def test_infer_status_failed_step_implies_fail():
    s = ParsedStep(
        step_index=0,
        tool_name="browser_click",
        tool_full_name="mcp__playwright__browser_click",
        tool_args={"element": "x"},
        tool_use_id="t1",
        is_playwright=True,
        result_is_error=True,
    )
    p = _parsed(steps=[s], success=False)
    status, err = _infer_status(p)
    assert status == "failed"
    assert err and "tool" in err.lower()


def test_infer_status_aborted_when_no_playwright_steps():
    p = _parsed(success=False)
    status, _ = _infer_status(p)
    assert status == "aborted"


def test_infer_status_passes_with_explicit_success_hint():
    pw_step = ParsedStep(
        step_index=0,
        tool_name="browser_navigate",
        tool_full_name="mcp__playwright__browser_navigate",
        tool_args={},
        tool_use_id="t",
        is_playwright=True,
    )
    p = _parsed(steps=[pw_step], success=True)
    assert _infer_status(p) == ("passed", None)


# ── End-to-end with mocked claude_runner ───────────────────────────────────


def _mock_outcome(*, status_text: str = "passed", with_failure: bool = False) -> RunOutcome:
    """Build a fake RunOutcome that the orchestrator can persist."""
    steps = [
        ParsedStep(
            step_index=0,
            tool_name="browser_navigate",
            tool_full_name="mcp__playwright__browser_navigate",
            tool_args={"url": "http://example.com"},
            tool_use_id="t1",
            is_playwright=True,
            page_url="http://example.com",
            page_title="Example",
            console_errors=0,
            console_warnings=0,
        ),
        ParsedStep(
            step_index=1,
            tool_name="browser_type",
            tool_full_name="mcp__playwright__browser_type",
            tool_args={"element": "username", "text": "admin"},
            tool_use_id="t2",
            is_playwright=True,
            result_is_error=with_failure,
            result_text="oops" if with_failure else None,
        ),
    ]
    parsed = ParsedRun(
        steps=steps,
        summary=RunSummary(
            success=(not with_failure),
            final_text=f'RESULT={{"case_status":"{status_text}"}}',
            parsed_result={"case_status": status_text},
            duration_ms=2000,
            num_turns=4,
            cost_usd=0.01,
            input_tokens=100,
            output_tokens=50,
        ),
    )
    return RunOutcome(
        parsed=parsed,
        stdout_path=__import__("pathlib").Path("/tmp/dummy.jsonl"),
        stderr_path=__import__("pathlib").Path("/tmp/dummy.err"),
        mcp_config_path=__import__("pathlib").Path("/tmp/dummy.mcp.json"),
        exit_code=0,
        elapsed_ms=2200,
    )


@pytest.mark.asyncio
async def test_execute_case_passed_path(seeded, session):
    proj, case = seeded
    # Pre-create the Run row (the API does this for us in production)
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(return_value=_mock_outcome(status_text="passed")),
    ):
        out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "passed"
    assert out.input_tokens == 100
    assert out.output_tokens == 50

    # StepEvents persisted
    rows = (
        (
            await session.execute(
                select(StepEvent)
                .where(StepEvent.run_id == run.run_id)
                .order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert rows[0].tool_name == "browser_navigate"
    assert rows[0].status == "ok"
    assert rows[1].tool_name == "browser_type"


@pytest.mark.asyncio
async def test_execute_case_failed_path(seeded, session):
    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(return_value=_mock_outcome(status_text="failed", with_failure=True)),
    ):
        out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "failed"

    rows = (
        (await session.execute(select(StepEvent).where(StepEvent.run_id == run.run_id)))
        .scalars()
        .all()
    )
    failed = [r for r in rows if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].tool_name == "browser_type"


@pytest.mark.asyncio
async def test_execute_case_aborts_on_runner_exception(seeded, session):
    from app.agent.claude_runner import ClaudeRunnerError

    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(side_effect=ClaudeRunnerError("timeout")),
    ):
        out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "aborted"
    assert "timeout" in (out.error_message or "")


@pytest.mark.asyncio
async def test_execute_case_creates_artifacts(seeded, session, tmp_path, monkeypatch):
    """run_orchestrator should write trace.jsonl + report.html into the run dir."""
    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    # Redirect artifacts root so we don't litter the repo
    from app import storage

    monkeypatch.setattr(storage, "artifacts_root", lambda: tmp_path)

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(return_value=_mock_outcome(status_text="passed")),
    ):
        out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "passed"
    rd = tmp_path / case.project_id / run.run_id
    assert (rd / "prompt.txt").is_file()
    assert (rd / "trace.jsonl").is_file()
    assert (rd / "report.html").is_file()
    assert (rd / "result.json").is_file()
    trace_lines = (rd / "trace.jsonl").read_text().strip().split("\n")
    assert len(trace_lines) == 2
    payload = json.loads(trace_lines[0])
    assert payload["tool_name"] == "browser_navigate"
    assert payload["page_title"] == "Example"
