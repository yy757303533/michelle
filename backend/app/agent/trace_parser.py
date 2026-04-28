"""Parse `claude -p --output-format stream-json --verbose` JSONL output.

Each line in the stream is one of:
  - {"type": "system", ...}                — session metadata
  - {"type": "assistant", "message": {...}} — model output (may carry tool_use blocks)
  - {"type": "user", "message": {...}}      — back from tool calls (tool_result blocks)
  - {"type": "result", ...}                 — final summary with usage + cost
  - {"type": "rate_limit_event", ...}       — informational

We turn this into a list of `StepEvent`-shaped dicts that line up 1-to-1 with
tool invocations, plus a `RunSummary` with status / cost / duration / etc.

Tool name convention: `@playwright/mcp` tools come in as
`mcp__playwright__browser_<verb>`. We strip the prefix when surfacing to UI.

The model's final message is expected to contain a marker line like
  RESULT={"login":"success", ...}
so the orchestrator can read structured intent without re-asking.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLAYWRIGHT_TOOL_PREFIX = "mcp__playwright__"
RESULT_LINE_RE = re.compile(r"^\s*RESULT\s*=\s*(\{.*\})\s*$", re.MULTILINE)

# Page URL / Title / Snapshot extractors — `@playwright/mcp` text format
_PAGE_URL_RE = re.compile(r"^- Page URL:\s*(.+)$", re.MULTILINE)
_PAGE_TITLE_RE = re.compile(r"^- Page Title:\s*(.+)$", re.MULTILINE)
_CONSOLE_SUMMARY_RE = re.compile(r"^- Console:\s*(\d+)\s+errors,\s*(\d+)\s+warnings", re.MULTILINE)
_SCREENSHOT_FILE_RE = re.compile(
    r"\[Screenshot of viewport\]\((.+?)\)|filename['\"]:\s*['\"]([^'\"]+)['\"]"
)


@dataclass
class StepEvent:
    step_index: int
    tool_name: str  # short, e.g. "browser_navigate"
    tool_full_name: str  # raw, e.g. "mcp__playwright__browser_navigate"
    tool_args: dict[str, Any]
    tool_use_id: str
    is_playwright: bool

    # Filled from the matching tool_result (if found)
    result_text: str | None = None
    result_is_error: bool | None = None
    page_url: str | None = None
    page_title: str | None = None
    console_errors: int | None = None
    console_warnings: int | None = None
    screenshot_path: str | None = None

    def short_summary(self) -> str:
        """Human-readable one-liner for UI / log."""
        parts = [self.tool_name]
        if self.tool_args:
            head = next(iter(self.tool_args.items()), None)
            if head:
                k, v = head
                vs = str(v)
                parts.append(f"{k}={vs[:50]}")
        if self.page_url:
            parts.append(f"→ {self.page_url}")
        return " ".join(parts)


@dataclass
class RunSummary:
    success: bool  # parsed `RESULT={"login":"success"}`-style hint, or false if absent
    final_text: str
    parsed_result: dict[str, Any] | None
    duration_ms: int | None
    num_turns: int | None
    cost_usd: float | None
    model_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    error: str | None = None
    raw_result: dict[str, Any] | None = None


@dataclass
class ParsedRun:
    steps: list[StepEvent]
    summary: RunSummary

    @property
    def playwright_steps(self) -> list[StepEvent]:
        return [s for s in self.steps if s.is_playwright]


# ────────────────────────────────────────────────────────────────────────────


_REDACTION = "***"


def _redact_text(text: str, secrets: list[str] | None) -> str:
    if not secrets or not text:
        return text
    out = text
    for s in secrets:
        if s and len(s) >= 3:
            out = out.replace(s, _REDACTION)
    return out


def _redact_value(v: Any, secrets: list[str] | None) -> Any:
    if not secrets:
        return v
    if isinstance(v, str):
        return _redact_text(v, secrets)
    if isinstance(v, dict):
        return {k: _redact_value(vv, secrets) for k, vv in v.items()}
    if isinstance(v, list):
        return [_redact_value(item, secrets) for item in v]
    return v


def redact_bytes(data: bytes, secrets: list[str] | None) -> bytes:
    """Replace each secret string in raw stream bytes. Used to scrub stdout/stderr
    before they hit disk so artifacts don't preserve plaintext credentials."""
    if not secrets or not data:
        return data
    text = data.decode("utf-8", errors="replace")
    text = _redact_text(text, secrets)
    return text.encode("utf-8")


def _parse_tool_result_text(text: str) -> dict[str, Any]:
    """Pull structured fields out of a `@playwright/mcp` tool_result text body."""
    fields: dict[str, Any] = {}
    if m := _PAGE_URL_RE.search(text):
        fields["page_url"] = m.group(1).strip()
    if m := _PAGE_TITLE_RE.search(text):
        fields["page_title"] = m.group(1).strip()
    if m := _CONSOLE_SUMMARY_RE.search(text):
        fields["console_errors"] = int(m.group(1))
        fields["console_warnings"] = int(m.group(2))
    if m := _SCREENSHOT_FILE_RE.search(text):
        fields["screenshot_path"] = m.group(1) or m.group(2)
    return fields


def _flatten_tool_result_content(content: Any) -> str:
    """tool_result content can be a string, a list of dicts, or mixed. Concat to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(item.get("text", ""))
                elif item.get("type") == "image":
                    out.append("[image]")
            elif isinstance(item, str):
                out.append(item)
        return "\n".join(out)
    return str(content)


def parse_stream(lines: list[str], *, secrets: list[str] | None = None) -> ParsedRun:
    """Parse a list of JSONL lines into a ParsedRun.

    `secrets`: literal strings to scrub from tool_args / result_text / final_text
    so that StepEvent rows persisted to DB / served to the frontend never carry
    plaintext credentials. Caller (Run Orchestrator) supplies the list."""
    tool_uses: list[StepEvent] = []
    tool_use_by_id: dict[str, StepEvent] = {}
    final_assistant_text = ""
    raw_result: dict[str, Any] | None = None

    step_idx = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")

        if t == "assistant":
            for c in obj.get("message", {}).get("content", []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    name = c.get("name", "")
                    is_pw = name.startswith(PLAYWRIGHT_TOOL_PREFIX)
                    short = name.removeprefix(PLAYWRIGHT_TOOL_PREFIX) if is_pw else name
                    raw_id = c.get("id") or f"missing-{step_idx}"
                    step = StepEvent(
                        step_index=step_idx,
                        tool_name=short,
                        tool_full_name=name,
                        tool_args=_redact_value(c.get("input", {}) or {}, secrets),
                        tool_use_id=raw_id,
                        is_playwright=is_pw,
                    )
                    tool_uses.append(step)
                    tool_use_by_id[raw_id] = step
                    step_idx += 1
                elif c.get("type") == "text":
                    text = c.get("text", "") or ""
                    if text.strip():
                        final_assistant_text = text  # last non-empty wins

        elif t == "user":
            for c in obj.get("message", {}).get("content", []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_result":
                    tid = c.get("tool_use_id", "")
                    step = tool_use_by_id.get(tid)
                    if step is None:
                        continue
                    body = _flatten_tool_result_content(c.get("content", []))
                    body = _redact_text(body, secrets)
                    step.result_text = body
                    step.result_is_error = c.get("is_error")
                    extracted = _parse_tool_result_text(body)
                    step.page_url = extracted.get("page_url")
                    step.page_title = extracted.get("page_title")
                    step.console_errors = extracted.get("console_errors")
                    step.console_warnings = extracted.get("console_warnings")
                    step.screenshot_path = extracted.get("screenshot_path")

        elif t == "result":
            raw_result = obj
            # Some Claude CLI builds emit the final answer here even when no
            # assistant `text` block was streamed. Use it as a fallback.
            if not final_assistant_text:
                rtext = obj.get("result")
                if isinstance(rtext, str) and rtext.strip():
                    final_assistant_text = rtext

    final_assistant_text = _redact_text(final_assistant_text, secrets)
    summary = _build_summary(final_assistant_text, raw_result)
    return ParsedRun(steps=tool_uses, summary=summary)


def _build_summary(final_text: str, raw_result: dict[str, Any] | None) -> RunSummary:
    parsed: dict[str, Any] | None = None
    if m := RESULT_LINE_RE.search(final_text):
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError:
            parsed = None

    success_hint = False
    if parsed:
        v = str(parsed.get("login", parsed.get("status", parsed.get("result", "")))).lower()
        success_hint = v in ("success", "succeeded", "passed", "ok", "true")

    if raw_result is None:
        return RunSummary(
            success=success_hint,
            final_text=final_text,
            parsed_result=parsed,
            duration_ms=None,
            num_turns=None,
            cost_usd=None,
            error="no result frame",
        )

    usage = raw_result.get("usage", {}) or {}
    model_usage = raw_result.get("modelUsage", {}) or {}

    is_error = bool(raw_result.get("is_error"))
    err_text = raw_result.get("error") or (raw_result.get("subtype") if is_error else None)

    overall_success = success_hint and not is_error

    return RunSummary(
        success=overall_success,
        final_text=final_text,
        parsed_result=parsed,
        duration_ms=raw_result.get("duration_ms"),
        num_turns=raw_result.get("num_turns"),
        cost_usd=raw_result.get("total_cost_usd"),
        model_usage=model_usage,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        error=err_text,
        raw_result=raw_result,
    )


def parse_stream_file(path: str | Path, *, secrets: list[str] | None = None) -> ParsedRun:
    p = Path(path)
    return parse_stream(p.read_text(encoding="utf-8").splitlines(), secrets=secrets)
