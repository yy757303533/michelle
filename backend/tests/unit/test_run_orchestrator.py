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
from app.models import Diagnosis, Project, Run, StepEvent, TestCase
from app.services import run_orchestrator
from app.services.run_orchestrator import (
    _format_assertions,
    _format_steps,
    _infer_status,
    create_run_row,
    execute_case,
    render_execute_prompt,
    rollback_run_scope,
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
    await engine.dispose()


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


def test_render_execute_prompt_includes_project_login_url():
    proj = Project(
        project_id="demo",
        name="Demo",
        base_url="http://example.com",
        login_url="http://example.com/auth/login",
        default_username="admin@example.com",
        default_password="secret",
    )
    case = TestCase(
        case_id="TC-AUTH-URL",
        project_id="demo",
        name="protected flow",
        intent="exercise a protected page",
        auth_state="logged-in",
        steps=[{"intent": "open protected page"}],
        assertions=[{"description": "protected content is visible"}],
    )

    prompt = render_execute_prompt(case, proj)

    assert "Login URL: http://example.com/auth/login" in prompt
    assert "Use this login URL directly" in prompt


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
    assert len(rows) == 5
    assert [r.tool_name for r in rows[:3]] == ["case_step", "case_step", "case_step"]
    assert [r.intent for r in rows[:3]] == [
        "open login page",
        "type username admin",
        "click submit",
    ]
    assert rows[0].phase == "case_step"
    assert rows[0].tool_args == {"expected": "form is visible"}
    assert rows[3].tool_name == "browser_navigate"
    assert rows[3].phase == "prepare"
    assert rows[3].status == "ok"
    assert rows[4].tool_name == "browser_type"
    assert rows[4].phase == "action"


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
async def test_execute_case_persists_final_assertions(seeded, session):
    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()
    outcome = _mock_outcome(status_text="failed", with_failure=False)
    outcome.parsed.summary.success = False
    outcome.parsed.summary.parsed_result = {
        "case_status": "failed",
        "failure_summary": "home URL was not reached",
        "assertion_results": [
            {
                "description": "URL changes to /home",
                "passed": False,
                "evidence": "Page URL remained http://example.com/login",
            }
        ],
    }

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(return_value=outcome),
    ):
        out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "failed"
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
    assertion = rows[-1]
    assert assertion.phase == "assertion"
    assert assertion.event == "agent.assertion.evaluated"
    assert assertion.status == "failed"
    assert "Page URL remained" in (assertion.error_message or "")


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
async def test_execute_case_persists_partial_steps_from_generic_exception(
    seeded, session, monkeypatch
):
    from app.agent.executor import ExecutorStatus
    from app.agent.generic_runner import GenericRunnerError

    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()
    partial = _parsed(
        success=False,
        steps=[
            ParsedStep(
                step_index=0,
                tool_name="browser_navigate",
                tool_full_name="mcp__playwright__browser_navigate",
                tool_args={"url": "http://example.com"},
                tool_use_id="generic-0",
                is_playwright=True,
                result_text="ok",
            )
        ],
        error="MCP response too large",
    )

    async def fake_executor_status(_session):
        return ExecutorStatus(
            status="ready",
            configured_loop="generic_openai",
            resolved_loop="generic_openai",
            detail="test",
            generic_available=True,
            generic_providers=["codex-cli"],
            claude_cli_available=False,
            npx_available=True,
        )

    monkeypatch.setattr(run_orchestrator, "resolve_executor_status", fake_executor_status)
    monkeypatch.setattr(
        run_orchestrator,
        "run_generic_with_playwright",
        AsyncMock(side_effect=GenericRunnerError("MCP response too large", partial=partial)),
    )

    out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "aborted"
    assert "MCP response too large" in (out.error_message or "")
    rows = (
        (await session.execute(select(StepEvent).where(StepEvent.run_id == run.run_id)))
        .scalars()
        .all()
    )
    execution_rows = [r for r in rows if r.tool_name != "case_step"]
    assert len(execution_rows) == 1
    assert execution_rows[0].tool_name == "browser_navigate"


@pytest.mark.asyncio
async def test_execute_case_passes_project_login_config_to_generic_runner(
    seeded, session, monkeypatch
):
    from app.agent.executor import ExecutorStatus

    proj, case = seeded
    proj.login_url = "https://example.test/logins"
    proj.default_username = "admin@example.test"
    proj.default_password = "secret-pass"
    case.auth_state = "logged-in"
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    async def fake_executor_status(_session):
        return ExecutorStatus(
            status="ready",
            configured_loop="generic_openai",
            resolved_loop="generic_openai",
            detail="test",
            generic_available=True,
            generic_providers=["codex-cli"],
            claude_cli_available=False,
            npx_available=True,
        )

    async def fake_generic(req):
        assert req.auth_state == "logged-in"
        assert req.login_url == "https://example.test/logins"
        assert req.default_username == "admin@example.test"
        assert req.default_password == "secret-pass"
        assert "secret-pass" in (req.secrets or [])
        return _mock_outcome(status_text="passed")

    monkeypatch.setattr(run_orchestrator, "resolve_executor_status", fake_executor_status)
    monkeypatch.setattr(run_orchestrator, "run_generic_with_playwright", fake_generic)

    out = await execute_case(case_id=case.case_id, run_id=run.run_id)

    assert out.status == "passed"


@pytest.mark.asyncio
async def test_execute_case_persists_generic_runtime_events(seeded, session, monkeypatch):
    from app.agent.executor import ExecutorStatus
    from app.agent.generic_runner import GenericRunnerError

    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    async def fake_executor_status(_session):
        return ExecutorStatus(
            status="ready",
            configured_loop="generic_openai",
            resolved_loop="generic_openai",
            detail="test",
            generic_available=True,
            generic_providers=["codex-cli"],
            claude_cli_available=False,
            npx_available=True,
        )

    async def fake_generic(req):
        assert req.on_runtime_event is not None
        await req.on_runtime_event(
            ParsedStep(
                step_index=0,
                tool_name="model_turn",
                tool_full_name="michelle.model_turn",
                tool_args={"turn": 0},
                tool_use_id="runtime-0",
                is_playwright=False,
                result_text="requesting next action",
            )
        )
        raise GenericRunnerError("generic loop exceeded total timeout")

    monkeypatch.setattr(run_orchestrator, "resolve_executor_status", fake_executor_status)
    monkeypatch.setattr(run_orchestrator, "run_generic_with_playwright", fake_generic)

    out = await execute_case(case_id=case.case_id, run_id=run.run_id, timeout_seconds=1)

    assert out.status == "aborted"
    rows = (
        (await session.execute(select(StepEvent).where(StepEvent.run_id == run.run_id)))
        .scalars()
        .all()
    )
    runtime_rows = [r for r in rows if r.event == "agent.runtime.event"]
    assert len(runtime_rows) == 1
    assert runtime_rows[0].tool_name == "model_turn"


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


@pytest.mark.asyncio
async def test_execute_case_redacts_password_from_prompt_artifact(
    seeded, session, tmp_path, monkeypatch
):
    proj, case = seeded
    proj.default_username = "admin"
    proj.default_password = "super-secret-password"
    await session.commit()
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    from app import storage

    monkeypatch.setattr(storage, "artifacts_root", lambda: tmp_path)

    with patch(
        "app.services.run_orchestrator.run_claude_with_playwright",
        AsyncMock(return_value=_mock_outcome(status_text="passed")),
    ):
        await execute_case(case_id=case.case_id, run_id=run.run_id)

    prompt_text = (tmp_path / case.project_id / run.run_id / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "super-secret-password" not in prompt_text
    assert "***" in prompt_text


@pytest.mark.asyncio
async def test_rollback_run_scope_deletes_side_effects(seeded, session, tmp_path, monkeypatch):
    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()
    session.add(StepEvent(run_id=run.run_id, step_index=0, event="agent.step.executed"))
    session.add(
        Diagnosis(
            diag_id="diag-1",
            run_id=run.run_id,
            case_id=case.case_id,
            diagnoser_prompt_version="v",
            diagnoser_model="m",
            category="unknown",
        )
    )
    await session.commit()

    rd = tmp_path / case.project_id / run.run_id
    rd.mkdir(parents=True)
    (rd / "report.html").write_text("x")
    monkeypatch.setattr(run_orchestrator, "run_dir_for", lambda _project, _run: rd)

    deleted = await rollback_run_scope(session, run_id=run.run_id, delete_run=True)

    assert deleted == 3
    assert await session.get(Run, run.run_id) is None
    assert (
        await session.execute(select(StepEvent).where(StepEvent.run_id == run.run_id))
    ).scalars().all() == []
    assert not rd.exists()


@pytest.mark.asyncio
async def test_retry_rolls_back_first_attempt_side_effects(seeded, session, monkeypatch):
    _, case = seeded
    run = await create_run_row(case_id=case.case_id, env="default", session=session)
    await session.commit()

    calls = 0

    async def fake_execute_case(**kw):
        nonlocal calls
        calls += 1
        row = await session.get(Run, kw["run_id"])
        assert row is not None
        if calls == 1:
            session.add(
                StepEvent(
                    run_id=kw["run_id"],
                    step_index=0,
                    event="agent.step.executed",
                    error_message="timeout",
                    status="failed",
                )
            )
            row.status = "failed"
            row.error_message = "navigation timeout"
        else:
            previous = (
                (await session.execute(select(StepEvent).where(StepEvent.run_id == kw["run_id"])))
                .scalars()
                .all()
            )
            assert previous == []
            session.add(StepEvent(run_id=kw["run_id"], step_index=0, event="agent.step.executed"))
            row.status = "passed"
            row.error_message = None
        await session.commit()
        return row

    async def noop(**_kw):
        return None

    monkeypatch.setattr(run_orchestrator, "execute_case", fake_execute_case)
    monkeypatch.setattr(run_orchestrator, "_notify_run_completed_email", noop)
    monkeypatch.setattr(run_orchestrator, "_rerender_report", noop)
    monkeypatch.setattr(run_orchestrator, "_run_limiter", None)
    monkeypatch.setattr(run_orchestrator, "MAX_CONCURRENT_RUNS", 1)

    await run_orchestrator._safe_execute(
        case_id=case.case_id,
        run_id=run.run_id,
        env="default",
        timeout_seconds=10,
    )

    assert calls == 2
    refreshed = await session.get(Run, run.run_id)
    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.status == "flaky"
    rows = (
        (await session.execute(select(StepEvent).where(StepEvent.run_id == run.run_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == "ok"
