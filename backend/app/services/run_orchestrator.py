"""Run a TestCase end-to-end and persist Run + StepEvent rows.

Flow:
  1. Build prompt from execute_v1 + case fields
  2. Spawn claude_runner.run_claude_with_playwright in artifacts/<project>/<run_id>/
  3. Parse the stream-json trace into our StepEvent rows
  4. Update Run.status from RESULT={...} hint or step-level evidence
  5. Render report.html
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.claude_runner import (
    ClaudeRunnerError,
    RunOutcome,
    RunRequest,
    run_claude_with_playwright,
)
from app.agent.trace_parser import ParsedRun, StepEvent as ParsedStep
from app.config import settings
from app.db import async_session_maker
from app.llm import prompt_id, render
from app.models import Project, Run, StepEvent, TestCase
from app.obs import EVENTS, bind_request_context, get_logger
from app.services.report_html import run_to_report_input, write_report_files
from app.storage import run_dir as run_dir_for

_log = get_logger(__name__)


# ── Prompt rendering helper ────────────────────────────────────────────────


def _format_steps(case: TestCase) -> str:
    lines: list[str] = []
    for i, step in enumerate(case.steps, start=1):
        intent = step.get("intent", "") if isinstance(step, dict) else str(step)
        expected = step.get("expected", "") if isinstance(step, dict) else ""
        if expected:
            lines.append(f"{i}. {intent}\n     期望: {expected}")
        else:
            lines.append(f"{i}. {intent}")
    return "\n".join(lines) if lines else "(no steps)"


def _format_assertions(case: TestCase) -> str:
    lines: list[str] = []
    for i, a in enumerate(case.assertions, start=1):
        desc = a.get("description", "") if isinstance(a, dict) else str(a)
        lines.append(f"{i}. {desc}")
    return "\n".join(lines) if lines else "(no explicit assertions; verify the steps above succeeded)"


def _format_preconditions(case: TestCase) -> str:
    if not case.preconditions:
        return "(none)"
    return "\n".join(f"- {p}" for p in case.preconditions)


def render_execute_prompt(case: TestCase, project: Project) -> str:
    return render(
        "execute",
        "v1",
        project_name=project.name or project.project_id,
        base_url=project.base_url or "(not configured)",
        case_name=case.name,
        case_intent=case.intent,
        preconditions=_format_preconditions(case),
        numbered_steps=_format_steps(case),
        numbered_assertions=_format_assertions(case),
    )


# ── Status mapping ─────────────────────────────────────────────────────────


_RESULT_STATUS_RE = re.compile(r'"case_status"\s*:\s*"([^"]+)"', re.IGNORECASE)


def _infer_status(parsed: ParsedRun) -> tuple[str, str | None]:
    """Pick a Run.status from the parsed trace.

    Priority:
      1. RESULT={"case_status":"passed|failed"} explicit hint
      2. summary.parsed_result["case_status"]
      3. Any failed steps  → failed
      4. summary.success   → passed if explicit success hint
      5. Default           → passed

    Returns (status, error_message_or_None).
    """
    pr = parsed.summary.parsed_result or {}

    case_status = str(pr.get("case_status", "")).lower()
    if not case_status and parsed.summary.final_text:
        m = _RESULT_STATUS_RE.search(parsed.summary.final_text)
        if m:
            case_status = m.group(1).lower()

    failure_summary = pr.get("failure_summary") or ""

    if case_status in {"passed", "pass", "success", "succeeded", "ok"}:
        return "passed", None
    if case_status in {"failed", "fail"}:
        return "failed", failure_summary or "model reported case_status=failed"

    # No explicit hint → look at steps
    pw_steps_failed = any(
        s.is_playwright and s.result_is_error for s in parsed.steps
    )
    if pw_steps_failed:
        return "failed", "one or more @playwright/mcp tool calls failed"

    if parsed.summary.success:
        return "passed", None

    if parsed.summary.error:
        return "failed", parsed.summary.error

    # Conservative: if we can't tell, mark passed only if there were ≥1 playwright steps
    if any(s.is_playwright for s in parsed.steps):
        return "passed", None
    return "aborted", "no playwright tool calls observed"


# ── Trace → StepEvent persistence ──────────────────────────────────────────


def _step_intent(parsed: ParsedStep) -> str | None:
    """Best-effort human label for a tool call."""
    if not parsed.is_playwright:
        return parsed.tool_name
    a = parsed.tool_args or {}
    if "url" in a:
        return f"{parsed.tool_name}: {a['url']}"
    if "element" in a:
        return f"{parsed.tool_name}: {a.get('element','')}"
    if "text" in a:
        text_preview = str(a.get("text", ""))[:40]
        return f"{parsed.tool_name}: {text_preview!r}"
    if "time" in a:
        return f"{parsed.tool_name}: {a['time']}s"
    return parsed.tool_name


def _persist_step_events(
    session: AsyncSession,
    run_id: str,
    parsed_steps: list[ParsedStep],
) -> list[StepEvent]:
    rows: list[StepEvent] = []
    for s in parsed_steps:
        ev = StepEvent(
            run_id=run_id,
            step_index=s.step_index,
            event=EVENTS.AGENT_STEP_EXECUTED.name,
            intent=_step_intent(s),
            tool_name=s.tool_name,
            tool_args=s.tool_args,
            tool_result={
                "result_text": (s.result_text or "")[:8000],
                "is_error": s.result_is_error,
                "page_url": s.page_url,
                "page_title": s.page_title,
                "console_errors": s.console_errors,
                "console_warnings": s.console_warnings,
            },
            screenshot_after=s.screenshot_path,
            status="failed" if s.result_is_error else "ok",
            error_message=(s.result_text or "")[:500] if s.result_is_error else None,
        )
        session.add(ev)
        rows.append(ev)
    return rows


# ── Public entrypoint ──────────────────────────────────────────────────────


async def execute_case(
    *,
    case_id: str,
    run_id: str,
    env: str = "default",
    timeout_seconds: int = 300,
) -> Run:
    """Run a single case end-to-end.

    The caller is responsible for creating the Run row in pending state first;
    this function loads it, runs Claude, persists StepEvents, updates the Run.
    Each call uses its own AsyncSession (decoupled from request session) so it
    can be invoked from a background task.
    """
    bind_request_context(run_id=run_id, case_id=case_id)
    log = _log.bind(run_id=run_id, case_id=case_id, env=env)

    async with async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            log.error("orchestrator.run.not_found")
            raise RuntimeError(f"run {run_id} not found")

        case = await session.get(TestCase, case_id)
        if case is None:
            run.status = "aborted"
            run.error_message = f"case {case_id} not found"
            await session.commit()
            log.error("orchestrator.case.not_found")
            return run

        project = await session.get(Project, case.project_id)
        if project is None:
            project = Project(project_id=case.project_id, name=case.project_id)
            session.add(project)
            await session.commit()

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("orchestrator.run.started")

        prompt = render_execute_prompt(case, project)
        rd = run_dir_for(case.project_id, run_id)
        # Stash the prompt for forensics
        (rd / "prompt.txt").write_text(prompt, encoding="utf-8")

        try:
            outcome: RunOutcome = await run_claude_with_playwright(
                RunRequest(
                    prompt=prompt,
                    work_dir=rd,
                    timeout_seconds=timeout_seconds,
                    headless=True,
                    isolated=True,
                )
            )
        except ClaudeRunnerError as exc:
            run.status = "aborted"
            run.error_message = str(exc)[:500]
            run.ended_at = datetime.now(timezone.utc)
            run.duration_ms = (
                int((run.ended_at - (run.started_at or run.ended_at)).total_seconds() * 1000)
                if run.started_at
                else None
            )
            await session.commit()
            log.error("orchestrator.run.aborted", error=str(exc)[:200])
            return run

        # Trace dump as JSONL alongside artifacts
        (rd / "trace.jsonl").write_text(
            "\n".join(
                json.dumps(_step_event_summary(s), ensure_ascii=False)
                for s in outcome.parsed.steps
            ),
            encoding="utf-8",
        )

        await _persist_results(session, run, case, outcome.parsed, prompt_id("execute", "v1"))

        # Render the HTML report immediately
        steps_in_db = await _load_step_events(session, run_id)
        rep = run_to_report_input(
            run=run,
            steps=steps_in_db,
            case_name=case.name,
            case_intent=case.intent,
            case_module=case.module,
        )
        paths = write_report_files(rep, rd)
        run.report_html_path = str(paths["html"])
        run.artifacts_dir = str(rd)
        run.trace_jsonl_path = str(rd / "trace.jsonl")

        await session.commit()
        log.info(
            EVENTS.RUN_COMPLETED.name,
            status=run.status,
            duration_ms=run.duration_ms,
            steps=len(outcome.parsed.steps),
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
        )
        return run


async def _persist_results(
    session: AsyncSession,
    run: Run,
    case: TestCase,
    parsed: ParsedRun,
    prompt_v: str,
) -> None:
    """Write StepEvents + update Run fields."""
    _persist_step_events(session, run.run_id, parsed.steps)

    status, err = _infer_status(parsed)
    run.status = status
    run.error_message = err
    run.ended_at = datetime.now(timezone.utc)
    run.duration_ms = (
        int((run.ended_at - (run.started_at or run.ended_at)).total_seconds() * 1000)
        if run.started_at
        else parsed.summary.duration_ms
    )
    run.input_tokens = parsed.summary.input_tokens
    run.output_tokens = parsed.summary.output_tokens
    # leave run.case_version as set by caller; we use its existing value


async def _load_step_events(session: AsyncSession, run_id: str) -> list[StepEvent]:
    from sqlmodel import select

    stmt = select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


def _step_event_summary(s: ParsedStep) -> dict[str, Any]:
    return {
        "step_index": s.step_index,
        "tool_name": s.tool_name,
        "tool_args": s.tool_args,
        "is_playwright": s.is_playwright,
        "page_url": s.page_url,
        "page_title": s.page_title,
        "console_errors": s.console_errors,
        "console_warnings": s.console_warnings,
        "screenshot_path": s.screenshot_path,
        "is_error": s.result_is_error,
    }


# ── Helper for the API to start a run ──────────────────────────────────────


async def create_run_row(
    *,
    case_id: str,
    env: str,
    session: AsyncSession,
    trace_id: str | None = None,
) -> Run:
    """Insert a Run row in pending state. Returns the row (uncommitted).

    The caller commits + then schedules execute_case in the background.
    """
    from uuid import uuid4

    case = await session.get(TestCase, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")

    run = Run(
        run_id=str(uuid4()),
        trace_id=trace_id or uuid4().hex,
        project_id=case.project_id,
        case_id=case.case_id,
        case_version=case.version,
        env=env or "default",
        status="pending",
    )
    session.add(run)
    return run


def kick_off(case_id: str, run_id: str, env: str, *, timeout_seconds: int = 300) -> asyncio.Task:
    """Fire-and-forget background runner. Returns the task so callers may await in tests."""
    return asyncio.create_task(
        _safe_execute(case_id=case_id, run_id=run_id, env=env, timeout_seconds=timeout_seconds)
    )


async def _safe_execute(*, case_id: str, run_id: str, env: str, timeout_seconds: int) -> None:
    try:
        await execute_case(
            case_id=case_id, run_id=run_id, env=env, timeout_seconds=timeout_seconds
        )
    except Exception as exc:  # noqa: BLE001
        # Persist abort
        try:
            async with async_session_maker() as session:
                run = await session.get(Run, run_id)
                if run is not None and run.status in {"pending", "running"}:
                    run.status = "aborted"
                    run.error_message = str(exc)[:500]
                    run.ended_at = datetime.now(timezone.utc)
                    await session.commit()
        finally:
            _log.exception("orchestrator.background.failed", run_id=run_id)


# Surface the default timeout from settings so callers can override per env
DEFAULT_RUN_TIMEOUT = max(60, settings.claude_timeout_seconds + 60)
