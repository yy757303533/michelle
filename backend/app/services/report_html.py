"""HTML report renderer — adapted from webtest-mcp's `save_test_results`.

This file lives in Michelle (not vendor/) because we own the input shape:
  Michelle's Run + StepEvent rows  →  ResultRow[]  →  HTML

The actual HTML/CSS/JS template is lifted (with attribution) from
`vendor/webtest-mcp/src/webtest_mcp/server.py`. Decoupling it from the MCP
tool wrapper lets us call it directly from FastAPI services.
"""

from __future__ import annotations

import base64
import html as _html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.run import Run, StepEvent

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class ResultRow:
    case_id: str
    title: str
    module: str = ""
    status: str = PASS  # pass | fail | skip
    error: str = ""
    screenshot_path: str | None = None
    """Absolute path to a screenshot file (will be embedded as base64)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "module": self.module,
            "status": self.status,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
        }


@dataclass
class ReportInput:
    project: str
    """Project key — appears in the report title."""
    run_id: str
    """Stable identifier rendered in the meta line."""
    excel_path: str | None = None
    """Optional source-of-truth file label (legacy webtest-mcp field; we reuse
    it to point at the PRD or 'AI generated'."""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    rows: list[ResultRow] = field(default_factory=list)


# ── Public API ─────────────────────────────────────────────────────────────


def render_report_html(report: ReportInput) -> str:
    """Return a self-contained HTML string. Screenshots embedded as base64."""
    screenshot_map = _build_screenshot_map(report.rows)

    passed = sum(1 for r in report.rows if r.status == PASS)
    failed = sum(1 for r in report.rows if r.status == FAIL)
    skipped = sum(1 for r in report.rows if r.status == SKIP)
    total = len(report.rows)
    pass_pct = round(passed / total * 100) if total else 0

    rows_html = "".join(_row_html(r, screenshot_map) for r in report.rows)
    return _template(
        project=report.project,
        run_id=report.run_id,
        excel_path=report.excel_path or "-",
        timestamp=report.timestamp,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        pass_pct=pass_pct,
        rows_html=rows_html,
    )


def render_report_json(report: ReportInput) -> str:
    """Side-car JSON, easy to diff."""
    counts = {
        "total": len(report.rows),
        "passed": sum(1 for r in report.rows if r.status == PASS),
        "failed": sum(1 for r in report.rows if r.status == FAIL),
        "skipped": sum(1 for r in report.rows if r.status == SKIP),
    }
    payload = {
        "project": report.project,
        "run_id": report.run_id,
        "excel_path": report.excel_path,
        "timestamp": report.timestamp,
        "summary": counts,
        "results": [r.to_dict() for r in report.rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_report_files(report: ReportInput, run_dir: Path) -> dict[str, Path]:
    """Persist report.html + result.json under `run_dir`. Returns paths."""
    run_dir.mkdir(parents=True, exist_ok=True)
    html_path = run_dir / "report.html"
    json_path = run_dir / "result.json"
    html_path.write_text(render_report_html(report), encoding="utf-8")
    json_path.write_text(render_report_json(report), encoding="utf-8")
    return {"html": html_path, "json": json_path}


# ── Adapter: Michelle Run + StepEvents → ReportInput ──


def run_to_report_input(
    *,
    run: Run,
    steps: list[StepEvent],
    case_name: str,
    case_intent: str = "",
    case_module: str = "",
) -> ReportInput:
    """Translate one Run into a single-row report.

    For now (Day 5), one Run = one Case. Day 9 may expand to batch runs where
    one report contains many cases — at which point we'll have a `runs_to_report`
    that aggregates.
    """
    if run.status == "passed":
        status = PASS
    elif run.status in {"failed", "aborted"}:
        status = FAIL
    elif run.status == "flaky":
        status = FAIL
    else:
        status = SKIP

    error_lines: list[str] = []
    if run.error_message:
        error_lines.append(run.error_message)
    failed_steps = [s for s in steps if s.status == "failed"]
    for s in failed_steps[:3]:
        bits = []
        if s.intent:
            bits.append(f"step #{s.step_index}: {s.intent}")
        if s.error_message:
            bits.append(s.error_message[:200])
        if bits:
            error_lines.append(" — ".join(bits))

    screenshot_path: str | None = None
    for s in failed_steps:
        if s.screenshot_after:
            screenshot_path = s.screenshot_after
            break
        if s.screenshot_before:
            screenshot_path = s.screenshot_before
            break
    if screenshot_path is None and steps:
        # fall back to the very last screenshot we have
        for s in reversed(steps):
            if s.screenshot_after:
                screenshot_path = s.screenshot_after
                break

    row = ResultRow(
        case_id=run.case_id,
        title=case_name,
        module=case_module,
        status=status,
        error="\n".join(error_lines).strip(),
        screenshot_path=screenshot_path,
    )

    return ReportInput(
        project=run.project_id,
        run_id=run.run_id,
        excel_path=case_intent or "AI-generated",
        timestamp=(run.ended_at or run.started_at or datetime.now(UTC)).isoformat(),
        rows=[row],
    )


# ── Internal helpers ─────────────────────────────────────────────────────


def _build_screenshot_map(rows: list[ResultRow]) -> dict[str, str]:
    """Return {case_id: data:image/<mime>;base64,...} for embedding inline."""
    out: dict[str, str] = {}
    for r in rows:
        if not r.screenshot_path:
            continue
        p = Path(r.screenshot_path)
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        ext = p.suffix.lstrip(".").lower() or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        out[r.case_id] = f"data:image/{mime};base64,{base64.b64encode(data).decode()}"
    return out


def _row_html(r: ResultRow, screenshots: dict[str, str]) -> str:
    if r.status == PASS:
        badge, row_cls = '<span class="badge pass">PASS</span>', ""
    elif r.status == FAIL:
        badge, row_cls = '<span class="badge fail">FAIL</span>', ' class="fail-row"'
    else:
        badge, row_cls = '<span class="badge skip">SKIP</span>', ' class="skip-row"'

    cid = _html.escape(r.case_id or "")
    mod = _html.escape(r.module or "")
    title = _html.escape(r.title or "")
    err = _html.escape(r.error or "")
    ss_tag = ""
    if uri := screenshots.get(r.case_id):
        ss_tag = (
            f'<div class="ss-thumb" onclick="openImg(this)">'
            f'<img src="{uri}" alt="screenshot"></div>'
        )

    return (
        f'<tr{row_cls} data-status="{r.status}">'
        f'<td class="cid">{cid}</td>'
        f'<td class="mod">{mod}</td>'
        f"<td>{title}</td>"
        f"<td>{badge}</td>"
        f'<td class="err">{err}{ss_tag}</td>'
        f"</tr>\n"
    )


def _template(
    *,
    project: str,
    run_id: str,
    excel_path: str,
    timestamp: str,
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    pass_pct: int,
    rows_html: str,
) -> str:
    """Self-contained HTML template. Adapted from webtest-mcp save_test_results.

    Visual style: Apple-ish white cards on grey, status-coloured badges,
    a search box, and a click-to-zoom screenshot lightbox. Renders standalone
    in any browser.
    """
    project_h = _html.escape(project)
    timestamp_h = _html.escape(timestamp)
    excel_path_h = _html.escape(excel_path)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{project_h} 测试报告</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f7;color:#1d1d1f;padding:24px}}
h1{{font-size:22px;font-weight:600;margin-bottom:4px}}
.meta{{color:#6e6e73;font-size:13px;margin-bottom:20px;word-break:break-all}}
.cards{{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
.card{{background:#fff;border-radius:10px;padding:16px 20px;min-width:100px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card .num{{font-size:28px;font-weight:700;line-height:1}}
.card .lbl{{font-size:12px;color:#6e6e73;margin-top:4px}}
.card.pass .num{{color:#28a745}}.card.fail .num{{color:#dc3545}}
.card.skip .num{{color:#fd7e14}}.card.total .num{{color:#0066cc}}
.progress{{background:#e9ecef;border-radius:99px;height:8px;margin-bottom:20px;overflow:hidden}}
.progress-bar{{height:100%;border-radius:99px;background:#28a745}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
th{{background:#f5f5f7;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;padding:10px 12px;text-align:left;border-bottom:1px solid #e5e5ea}}
td{{padding:9px 12px;font-size:13px;border-bottom:1px solid #f0f0f0;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
.fail-row td{{background:#fff8f8}}.skip-row td{{background:#fffbf5}}
.cid{{font-family:monospace;font-size:12px;color:#6e6e73;white-space:nowrap}}
.mod{{font-size:12px;color:#6e6e73;white-space:nowrap}}
.err{{color:#dc3545;font-size:12px;max-width:300px;word-break:break-word}}
.badge{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600;letter-spacing:.3px;white-space:nowrap}}
.badge.pass{{background:#d4edda;color:#155724}}.badge.fail{{background:#f8d7da;color:#721c24}}.badge.skip{{background:#fff3cd;color:#856404}}
.filter-bar{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.filter-btn{{padding:5px 14px;border:1px solid #d2d2d7;border-radius:99px;font-size:13px;cursor:pointer;background:#fff;transition:all .15s}}
.filter-btn.active{{background:#0066cc;color:#fff;border-color:#0066cc}}
.search{{padding:6px 14px;border:1px solid #d2d2d7;border-radius:99px;font-size:13px;outline:none;flex:1;min-width:160px}}
.ss-thumb{{margin-top:6px;cursor:zoom-in}}
.ss-thumb img{{max-width:120px;max-height:80px;border-radius:4px;border:1px solid #e5e5ea;display:block}}
.lightbox{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;align-items:center;justify-content:center}}
.lightbox.open{{display:flex}}
.lightbox img{{max-width:90vw;max-height:90vh;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.4)}}
.foot{{margin-top:16px;font-size:11px;color:#a1a1a6;text-align:right}}
</style>
</head>
<body>
<h1>{project_h} 测试报告</h1>
<div class="meta">{timestamp_h} &nbsp;|&nbsp; {excel_path_h} &nbsp;|&nbsp; Run: {_html.escape(run_id)}</div>

<div class="cards">
  <div class="card total"><div class="num">{total}</div><div class="lbl">总计</div></div>
  <div class="card pass"><div class="num">{passed}</div><div class="lbl">通过</div></div>
  <div class="card fail"><div class="num">{failed}</div><div class="lbl">失败</div></div>
  <div class="card skip"><div class="num">{skipped}</div><div class="lbl">跳过</div></div>
</div>

<div class="progress"><div class="progress-bar" style="width:{pass_pct}%"></div></div>

<div class="filter-bar">
  <button class="filter-btn active" onclick="setFilter('all',this)">全部 {total}</button>
  <button class="filter-btn" onclick="setFilter('pass',this)">通过 {passed}</button>
  <button class="filter-btn" onclick="setFilter('fail',this)">失败 {failed}</button>
  <button class="filter-btn" onclick="setFilter('skip',this)">跳过 {skipped}</button>
  <input class="search" id="search" type="text" placeholder="搜索用例ID / 模块 / 标题..." oninput="doSearch()">
</div>

<table>
<thead><tr><th>用例ID</th><th>模块</th><th>标题</th><th>结果</th><th>备注 / 截图</th></tr></thead>
<tbody id="tbody">
{rows_html}
</tbody>
</table>

<div class="lightbox" id="lb" onclick="this.classList.remove('open')">
  <img id="lb-img" src="" alt="screenshot">
</div>

<div class="foot">Generated by Michelle · adapted from webtest-mcp</div>

<script>
var tbody = document.getElementById('tbody');
var currentFilter = 'all';
function setFilter(type, btn) {{
  currentFilter = type;
  document.querySelectorAll('.filter-btn').forEach(function(b){{ b.classList.remove('active'); }});
  btn.classList.add('active');
  applyFilters();
}}
function doSearch() {{ applyFilters(); }}
function applyFilters() {{
  var q = document.getElementById('search').value.toLowerCase();
  Array.from(tbody.rows).forEach(function(r) {{
    var statusMatch = currentFilter === 'all' || r.dataset.status === currentFilter;
    var textMatch = !q || r.textContent.toLowerCase().includes(q);
    r.style.display = (statusMatch && textMatch) ? '' : 'none';
  }});
}}
function openImg(el) {{
  var img = el.querySelector('img');
  if (!img) return;
  document.getElementById('lb-img').src = img.src;
  document.getElementById('lb').classList.add('open');
}}
</script>
</body>
</html>"""
