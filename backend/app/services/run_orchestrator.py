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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.claude_runner import (
    ClaudeRunnerError,
    RunRequest,
    run_claude_with_playwright,
)
from app.agent.executor import resolve_executor_status
from app.agent.generic_runner import GenericRunnerError, run_generic_with_playwright
from app.agent.trace_parser import ParsedRun
from app.agent.trace_parser import StepEvent as ParsedStep
from app.config import settings
from app.db import async_session_maker
from app.llm import render
from app.models import Project, Run, StepEvent, TestCase
from app.obs import EVENTS, bind_request_context, get_logger
from app.services._concurrency import ResizableLimiter
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
    return (
        "\n".join(lines) if lines else "(no explicit assertions; verify the steps above succeeded)"
    )


def _format_preconditions(case: TestCase) -> str:
    if not case.preconditions:
        return "(none)"
    return "\n".join(f"- {p}" for p in case.preconditions)


def _format_login_context(project: Project) -> str:
    """Surface the project's default credentials to the agent so cases that
    were generated without explicit login steps can still authenticate when
    a page redirects to a login form. Without this, the agent saw the
    login page, didn't know what creds to use, and bailed with
    "precondition not met"."""
    if not (project.default_username or project.default_password):
        return (
            "(no default credentials configured for this project — if a step "
            "requires authentication, the case must include explicit login "
            "steps)"
        )
    return (
        f"This project has default test credentials. If any step's target page "
        f"redirects to a login form, authenticate first using:\n"
        f"  - Username/Email: {project.default_username or '(not set)'}\n"
        f"  - Password: {project.default_password or '(not set)'}\n"
        f"Find the login page by clicking a 'Log in' / 'Sign in' link from the "
        f"home page or navigating to common paths like /login, /signin, /logins."
    )


def render_execute_prompt(case: TestCase, project: Project) -> str:
    return render(
        "execute",
        "v1",
        project_name=project.name or project.project_id,
        base_url=project.base_url or "(not configured)",
        case_name=case.name,
        case_intent=case.intent,
        auth_state=case.auth_state,
        login_context=_format_login_context(project),
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
    pw_steps_failed = any(s.is_playwright and s.result_is_error for s in parsed.steps)
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
        return f"{parsed.tool_name}: {a.get('element', '')}"
    if "text" in a:
        text_preview = str(a.get("text", ""))[:40]
        return f"{parsed.tool_name}: {text_preview!r}"
    if "time" in a:
        return f"{parsed.tool_name}: {a['time']}s"
    return parsed.tool_name


async def _next_step_offset(session: AsyncSession, run_id: str) -> int:
    """Return one past the highest step_index already persisted for this run.

    Retries (attempt > 1) need to push their step_index past existing rows so
    we don't collide on the (run_id, step_index) primary-key-shaped tuple and
    so the report viewer can show attempt boundaries via gaps."""
    from sqlalchemy import func
    from sqlmodel import select

    stmt = select(func.max(StepEvent.step_index)).where(StepEvent.run_id == run_id)
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        return 0
    return int(result) + 1


def _persist_step_events(
    session: AsyncSession,
    run_id: str,
    parsed_steps: list[ParsedStep],
    *,
    step_offset: int = 0,
) -> list[StepEvent]:
    rows: list[StepEvent] = []
    for s in parsed_steps:
        ev = StepEvent(
            run_id=run_id,
            step_index=step_offset + s.step_index,
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
    attempt: int = 1,
) -> Run:
    """Run a single case end-to-end.

    The caller is responsible for creating the Run row in pending state first;
    this function loads it, runs Claude, persists StepEvents, updates the Run.
    Each call uses its own AsyncSession (decoupled from request session) so it
    can be invoked from a background task.

    On retry (attempt > 1) the prior StepEvents stay in place and we continue
    appending — the report viewer shows attempt boundaries via step_index gaps.
    """
    bind_request_context(run_id=run_id, case_id=case_id, attempt=attempt)
    log = _log.bind(run_id=run_id, case_id=case_id, env=env, attempt=attempt)

    async with async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            log.error("orchestrator.run.not_found")
            raise RuntimeError(f"run {run_id} not found")

        case = await session.get(TestCase, case_id)
        if case is None:
            run.status = "aborted"
            run.error_message = f"case {case_id} not found"
            run.ended_at = datetime.now(UTC)
            if run.started_at:
                run.duration_ms = int((run.ended_at - run.started_at).total_seconds() * 1000)
            await session.commit()
            log.error("orchestrator.case.not_found")
            return run

        project = await session.get(Project, case.project_id)
        if project is None:
            project = Project(project_id=case.project_id, name=case.project_id)
            session.add(project)
            await session.commit()

        run.status = "running"
        run.started_at = datetime.now(UTC)
        await session.commit()
        log.info("orchestrator.run.started")

        prompt = render_execute_prompt(case, project)
        rd = run_dir_for(case.project_id, run_id)
        # Stash the prompt for forensics
        (rd / "prompt.txt").write_text(prompt, encoding="utf-8")

        # Treat configured target credentials as secrets — they get baked into
        # the prompt and would otherwise leak via stdout/stderr files,
        # StepEvent.tool_args (browser_type text=…), and final_text logs.
        # Project-level credentials override the env-level defaults so each
        # project can target its own environment without a restart.
        secrets = [
            s
            for s in (project.default_password, settings.default_target_password)
            if s and len(s) >= 3
        ]

        # Read live headless preference. Operator can toggle from the
        # dashboard to watch the agent drive Chromium during debugging.
        from app.runtime_config import get_headless

        headless = await get_headless(session)

        executor = await resolve_executor_status(session)
        if executor.status != "ready" or not executor.resolved_loop:
            run.status = "aborted"
            run.error_message = f"executor not ready: {executor.detail}"[:500]
            run.ended_at = datetime.now(UTC)
            run.duration_ms = (
                int((run.ended_at - (run.started_at or run.ended_at)).total_seconds() * 1000)
                if run.started_at
                else None
            )
            await session.commit()
            log.error(
                "orchestrator.executor.not_ready",
                configured_loop=executor.configured_loop,
                resolved_loop=executor.resolved_loop,
                detail=executor.detail,
            )
            return run

        run_req = RunRequest(
            prompt=prompt,
            work_dir=rd,
            timeout_seconds=timeout_seconds,
            headless=headless,
            isolated=True,
            secrets=secrets,
        )

        try:
            if executor.resolved_loop == "generic_openai":
                log.info("orchestrator.executor.selected", runner="generic_openai")
                outcome = await run_generic_with_playwright(run_req)
            else:
                log.info("orchestrator.executor.selected", runner="claude_cli")
                outcome = await run_claude_with_playwright(run_req)
        except (ClaudeRunnerError, GenericRunnerError) as exc:
            run.status = "aborted"
            run.error_message = str(exc)[:500]
            run.ended_at = datetime.now(UTC)
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
                json.dumps(_step_event_summary(s), ensure_ascii=False) for s in outcome.parsed.steps
            ),
            encoding="utf-8",
        )

        await _persist_results(session, run, outcome.parsed)

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
    parsed: ParsedRun,
) -> None:
    """Write StepEvents + update Run fields. Retries push step_index past
    existing rows so the (run_id, step_index) tuple stays unique and the
    report viewer can render attempt boundaries via the gap."""
    step_offset = await _next_step_offset(session, run.run_id)
    _persist_step_events(session, run.run_id, parsed.steps, step_offset=step_offset)

    status, err = _infer_status(parsed)
    run.status = status
    run.error_message = err
    run.ended_at = datetime.now(UTC)
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


# ── Concurrency control ────────────────────────────────────────────────────

# Max simultaneous browser sessions. Each session = 1 Chromium + 1 claude CLI
# subprocess + ~250MB RAM. 2 is comfortable on a dev laptop. The .env value
# is just the bootstrap default — `runtime_settings.max_concurrent_runs`
# (Settings panel on the dashboard) overrides it at runtime via _resync_limiter.
MAX_CONCURRENT_RUNS = max(1, int(getattr(settings, "max_concurrent_runs", 2)))
_run_limiter: ResizableLimiter | None = None


async def _resolve_concurrency() -> int:
    try:
        from app.runtime_config import get_max_concurrent_runs

        return await get_max_concurrent_runs()
    except Exception:
        return MAX_CONCURRENT_RUNS


async def _limiter() -> ResizableLimiter:
    """Live-resizable concurrency limiter. Reads the current cap on every
    acquire so changes from the Settings panel take effect immediately
    for the next-launched run (and for waiters when the cap is raised),
    without recreating the limiter and stranding the in-flight count."""
    global _run_limiter
    capacity = await _resolve_concurrency()
    if _run_limiter is None:
        _run_limiter = ResizableLimiter(capacity)
    elif _run_limiter.capacity != capacity:
        await _run_limiter.set_capacity(capacity)
    return _run_limiter


def kick_off(case_id: str, run_id: str, env: str, *, timeout_seconds: int = 300) -> asyncio.Task:
    """Fire-and-forget background runner. Concurrency is gated by a semaphore
    sized to MAX_CONCURRENT_RUNS so a 50-case batch doesn't fork 50 Chromiums."""
    return asyncio.create_task(
        _safe_execute(case_id=case_id, run_id=run_id, env=env, timeout_seconds=timeout_seconds)
    )


# ── Run with retry + classification ────────────────────────────────────────

# Patterns that MAY recover on a retry (e.g. one Chromium hiccup, transient
# network blip). Anything else fails fast.
_TRANSIENT_HINTS = (
    "timeout",
    "stale element",
    "detached from dom",
    "wait_for timeout",
    "navigation timeout",
    "claude cli timed out",
    "race condition",
    "element is not visible",
    "element is not stable",
    "click was intercepted",
)


def _looks_transient(blob: str | None) -> bool:
    if not blob:
        return False
    s = blob.lower()
    return any(h in s for h in _TRANSIENT_HINTS)


async def _safe_execute(*, case_id: str, run_id: str, env: str, timeout_seconds: int) -> None:
    """Run the case under a semaphore, with one retry on transient failure.

    Retry policy: if execute_case finishes with status=failed/aborted AND the
    error string looks transient, run it again (preserves the same Run row,
    appends step events from the second attempt with step_index continuing).
    The second attempt's terminal status is what sticks.
    """
    limiter = await _limiter()
    async with limiter:
        attempt = 1
        try:
            run = await execute_case(
                case_id=case_id,
                run_id=run_id,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            await _persist_abort(run_id=run_id, error=str(exc))
            _log.exception("orchestrator.background.failed", run_id=run_id, attempt=attempt)
            return

        if run.status not in {"failed", "aborted"}:
            return

        if not _looks_transient(run.error_message):
            await _classify_and_persist(run_id=run_id)
            return

        _log.info(
            "orchestrator.run.retry",
            run_id=run_id,
            attempt=attempt + 1,
            reason="transient error in attempt 1",
            error=(run.error_message or "")[:200],
        )
        try:
            run = await execute_case(
                case_id=case_id,
                run_id=run_id,
                env=env,
                timeout_seconds=timeout_seconds,
                attempt=attempt + 1,
            )
        except Exception as exc:  # noqa: BLE001
            await _persist_abort(run_id=run_id, error=f"retry crashed: {exc}")
            return

        # If retry passed but first attempt failed → mark flaky and re-render
        # the report so report.html status matches the DB Run.status. Without
        # this, the HTML still says "passed" even though Run.status="flaky".
        if run.status == "passed":
            await _mark_status(run_id=run_id, status="flaky", note="passed on retry")
            await _rerender_report(run_id=run_id)
        else:
            await _classify_and_persist(run_id=run_id)
            await _rerender_report(run_id=run_id)


async def _persist_abort(*, run_id: str, error: str) -> None:
    try:
        async with async_session_maker() as session:
            run = await session.get(Run, run_id)
            if run is not None and run.status in {"pending", "running"}:
                run.status = "aborted"
                run.error_message = error[:500]
                run.ended_at = datetime.now(UTC)
                await session.commit()
    except Exception:  # noqa: BLE001
        _log.exception("orchestrator.persist_abort.failed", run_id=run_id)


async def _mark_status(*, run_id: str, status: str, note: str = "") -> None:
    async with async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        run.status = status
        if note:
            existing = run.error_message or ""
            run.error_message = (existing + ("\n" if existing else "") + note)[:500]
        await session.commit()


async def _rerender_report(*, run_id: str) -> None:
    """Regenerate report.html after a post-execute_case status mutation
    (retry → flaky, heuristic classification appended to error_message).
    Without this the report freezes at the value execute_case wrote."""
    async with async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        case = await session.get(TestCase, run.case_id)
        if case is None:
            return
        steps = await _load_step_events(session, run_id)
        rd = run_dir_for(run.project_id, run_id)
        rep = run_to_report_input(
            run=run,
            steps=steps,
            case_name=case.name,
            case_intent=case.intent,
            case_module=case.module,
        )
        paths = write_report_files(rep, rd)
        run.report_html_path = str(paths["html"])
        await session.commit()


async def _emit_failure_hook(*, run_id: str) -> None:
    """Fire run.failed hook handlers (auto-diagnose lives there)."""
    from app.agent import hooks

    try:
        await hooks.emit("run.failed", {"run_id": run_id})
    except Exception:  # noqa: BLE001
        _log.exception("orchestrator.failure_hook_emit_failed", run_id=run_id)


async def _classify_and_persist(*, run_id: str) -> None:
    """Fire the run.failed hook (auto-diagnose lives there).

    Previously this also attached a `[heuristic:<category>]` tag to
    error_message based on string-matching keywords. That heuristic
    routinely mis-classified — e.g. "Precondition not met: user is not
    logged in" was tagged `real_bug` even though the case itself was
    poorly designed. The diagnoser produces a proper category, we don't
    need a noisy second source of truth."""
    await _emit_failure_hook(run_id=run_id)


# Surface the default timeout from settings so callers can override per env
DEFAULT_RUN_TIMEOUT = max(60, settings.claude_timeout_seconds + 60)
