"""Tests for the HTML report renderer.

These build synthetic Run / StepEvent objects (no DB roundtrip) and assert the
output structure. We also smoke-test rendering with a real screenshot file.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

from app.models.run import Run, StepEvent
from app.services.report_html import (
    FAIL,
    PASS,
    SKIP,
    ReportInput,
    ResultRow,
    render_report_html,
    render_report_json,
    run_to_report_input,
    write_report_files,
)


def _row(case_id: str, status: str, **kw) -> ResultRow:
    return ResultRow(case_id=case_id, title=f"title-{case_id}", status=status, **kw)


def _make_run(**overrides) -> Run:
    base = dict(
        run_id="run-1",
        trace_id="tr-1",
        project_id="demo",
        case_id="TC-1",
        case_version=1,
        env="dev",
        status="passed",
    )
    base.update(overrides)
    return Run(**base)


# ── render_report_html ──────────────────────────────────────────────────────


def test_render_html_contains_summary_cards():
    rep = ReportInput(
        project="demo",
        run_id="r1",
        rows=[_row("TC-1", PASS), _row("TC-2", FAIL, error="boom")],
    )
    h = render_report_html(rep)
    assert "<!DOCTYPE html>" in h
    assert ">1<" in h  # passed
    assert ">1<" in h  # failed
    assert "demo 测试报告" in h
    assert "TC-1" in h
    assert "TC-2" in h
    assert "boom" in h


def test_render_html_escapes_unsafe_input():
    rep = ReportInput(
        project="<demo>",
        run_id="r1",
        rows=[
            ResultRow(
                case_id="TC-X",
                title='evil "title" <script>alert(1)</script>',
                status=FAIL,
                error="<img onerror=hack>",
            )
        ],
    )
    h = render_report_html(rep)
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h
    assert "&lt;img onerror=hack&gt;" in h


def test_render_html_pass_pct_correct():
    rows = [_row(f"TC-{i}", PASS) for i in range(3)] + [_row("TC-99", FAIL)]
    rep = ReportInput(project="d", run_id="r", rows=rows)
    h = render_report_html(rep)
    # 3/4 = 75%
    assert "width:75%" in h


def test_render_html_skip_handled():
    rep = ReportInput(project="d", run_id="r", rows=[_row("TC-1", SKIP, error="not run")])
    h = render_report_html(rep)
    assert 'data-status="skip"' in h
    assert "skip-row" in h


def test_render_html_screenshot_embedded(tmp_path: Path):
    img = tmp_path / "shot.png"
    # 1×1 transparent PNG
    img.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )
    )
    rep = ReportInput(
        project="d",
        run_id="r",
        rows=[_row("TC-1", FAIL, error="x", screenshot_path=str(img))],
    )
    h = render_report_html(rep)
    assert "data:image/png;base64," in h


def test_render_html_missing_screenshot_silently_skipped():
    rep = ReportInput(
        project="d",
        run_id="r",
        rows=[_row("TC-1", FAIL, error="x", screenshot_path="/no/such/path.png")],
    )
    h = render_report_html(rep)
    assert "data:image/" not in h


# ── render_report_json ─────────────────────────────────────────────────────


def test_render_json_summary():
    import json

    rep = ReportInput(
        project="d",
        run_id="r",
        rows=[_row("TC-1", PASS), _row("TC-2", FAIL), _row("TC-3", SKIP)],
    )
    j = json.loads(render_report_json(rep))
    assert j["summary"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}
    assert len(j["results"]) == 3


# ── write_report_files ─────────────────────────────────────────────────────


def test_write_report_files_persists(tmp_path: Path):
    rep = ReportInput(project="d", run_id="r", rows=[_row("TC-1", PASS)])
    paths = write_report_files(rep, tmp_path / "run-r")
    assert paths["html"].is_file()
    assert paths["json"].is_file()
    assert "TC-1" in paths["html"].read_text(encoding="utf-8")


# ── run_to_report_input ────────────────────────────────────────────────────


def test_run_to_report_input_passed_run():
    run = _make_run(status="passed")
    rep = run_to_report_input(
        run=run,
        steps=[],
        case_name="my case",
    )
    assert len(rep.rows) == 1
    assert rep.rows[0].status == PASS
    assert rep.rows[0].title == "my case"
    assert rep.rows[0].error == ""


def test_run_to_report_input_failed_run_includes_step_errors():
    run = _make_run(status="failed", error_message="overall failure")
    steps = [
        StepEvent(
            run_id="run-1",
            step_index=0,
            event="agent.step.executed",
            intent="open page",
            status="ok",
        ),
        StepEvent(
            run_id="run-1",
            step_index=1,
            event="agent.step.executed",
            intent="click submit",
            status="failed",
            error_message="timeout waiting for navigation",
        ),
    ]
    rep = run_to_report_input(run=run, steps=steps, case_name="login flow")
    assert rep.rows[0].status == FAIL
    err = rep.rows[0].error
    assert "overall failure" in err
    assert "step #1" in err
    assert "click submit" in err
    assert "timeout" in err


def test_run_to_report_input_picks_screenshot_from_failed_step():
    run = _make_run(status="failed")
    steps = [
        StepEvent(
            run_id="run-1",
            step_index=0,
            event="agent.step.executed",
            status="ok",
            screenshot_after="/path/ok.png",
        ),
        StepEvent(
            run_id="run-1",
            step_index=1,
            event="agent.step.executed",
            status="failed",
            screenshot_before="/path/bad-before.png",
            screenshot_after="/path/bad-after.png",
        ),
    ]
    rep = run_to_report_input(run=run, steps=steps, case_name="x")
    assert rep.rows[0].screenshot_path == "/path/bad-after.png"


def test_run_to_report_input_falls_back_to_last_screenshot_when_passed():
    run = _make_run(status="passed")
    steps = [
        StepEvent(
            run_id="run-1",
            step_index=0,
            event="agent.step.executed",
            status="ok",
            screenshot_after="/path/early.png",
        ),
        StepEvent(
            run_id="run-1",
            step_index=1,
            event="agent.step.executed",
            status="ok",
            screenshot_after="/path/late.png",
        ),
    ]
    rep = run_to_report_input(run=run, steps=steps, case_name="x")
    assert rep.rows[0].screenshot_path == "/path/late.png"


def test_run_to_report_input_aborted_treated_as_fail():
    run = _make_run(status="aborted", error_message="claude crashed")
    rep = run_to_report_input(run=run, steps=[], case_name="x")
    assert rep.rows[0].status == FAIL


def test_run_to_report_input_uses_case_module():
    run = _make_run()
    rep = run_to_report_input(run=run, steps=[], case_name="x", case_module="login")
    assert rep.rows[0].module == "login"


def test_render_html_with_run_to_report_input_smoke():
    """End-to-end smoke: synthetic Run → adapter → HTML."""
    run = _make_run(
        status="failed",
        ended_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        error_message="fail x",
    )
    steps = [
        StepEvent(
            run_id="run-1",
            step_index=0,
            event="agent.step.executed",
            intent="navigate",
            status="ok",
        ),
        StepEvent(
            run_id="run-1",
            step_index=1,
            event="agent.step.executed",
            intent="click",
            status="failed",
            error_message="not found",
        ),
    ]
    rep = run_to_report_input(run=run, steps=steps, case_name="case A", case_module="auth")
    html = render_report_html(rep)
    assert "case A" in html
    assert "auth" in html
    assert "fail x" in html
    assert "click" in html
    assert "Run: run-1" in html
