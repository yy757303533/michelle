"""Michelle-owned generic browser agent loop.

This is the portable execution path for project/team use: an
OpenAI-compatible model produces JSON actions, Michelle calls
`@playwright/mcp` directly, persists every tool call, and stops when the model
returns the required RESULT payload.

The implementation intentionally uses a conservative JSON-action protocol
instead of provider-specific native tool calling. Native tool-calling can be
added later under the same runner without changing the UI contract.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.agent.claude_runner import RunOutcome, RunRequest
from app.agent.mcp_stdio import MCPClientError, MCPTool, build_playwright_stdio_client
from app.agent.trace_parser import (
    ParsedRun,
    RunSummary,
    StepEvent,
    _parse_tool_result_text,
    redact_bytes,
)
from app.config import settings
from app.llm import LLMError, get_gateway
from app.obs import EVENTS, get_logger
from app.runtime_config import get_case_execution_provider

_log = get_logger(__name__)
_DATA_URI_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")
_MAX_TOOL_RESULT_TEXT_CHARS = 12_000
_MIN_MODEL_TURN_SECONDS = 15.0
_REPEATABLE_OBSERVATION_TOOLS = {"browser_snapshot"}


class GenericRunnerError(RuntimeError):
    def __init__(self, message: str, *, partial: ParsedRun | None = None):
        super().__init__(message)
        self.partial = partial


async def run_generic_with_playwright(req: RunRequest) -> RunOutcome:
    """Run a case with Michelle's own JSON-action loop."""

    work = req.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    stdout_path = work / "generic-agent.stream.jsonl"
    stderr_path = work / "generic-agent.err.log"
    mcp_config_path = work / "mcp.json"
    # Keep the same artifact as Claude runner for operator forensics.
    from app.agent.mcp_config import build_playwright_mcp_config, write_config

    write_config(
        mcp_config_path,
        build_playwright_mcp_config(
            isolated=req.isolated,
            headless=req.headless,
            output_dir=str(work),
            extra_args=req.extra_mcp_args,
        ),
    )

    events: list[dict[str, Any]] = []
    steps: list[StepEvent] = []
    final_text = ""
    total_input = 0
    total_output = 0
    t0 = time.monotonic()
    runtime_event_index = 0

    try:
        async with build_playwright_stdio_client(
            cwd=work,
            headless=req.headless,
            isolated=req.isolated,
            extra_args=req.extra_mcp_args,
            output_dir=work,
        ) as mcp:
            tools = [tool for tool in await mcp.list_tools() if tool.name != "browser_install"]
            transcript = _initial_transcript(req.prompt, tools)
            runtime_event_index = await _bootstrap_configured_login(
                req=req,
                mcp=mcp,
                tools=tools,
                transcript=transcript,
                events=events,
                steps=steps,
                runtime_event_index=runtime_event_index,
            )
            max_turns = max(1, settings.generic_agent_max_turns)
            last_action_key: tuple[str, str] | None = None
            repeated_action_count = 0

            for turn in range(max_turns):
                remaining = _remaining_seconds(t0, req.timeout_seconds)
                if remaining <= 0:
                    raise _generic_error(
                        f"generic loop exceeded total timeout of {req.timeout_seconds}s",
                        steps=steps,
                        final_text=final_text,
                        elapsed_ms=int((time.monotonic() - t0) * 1000),
                        input_tokens=total_input,
                        output_tokens=total_output,
                    )
                if remaining < _MIN_MODEL_TURN_SECONDS:
                    raise _generic_error(
                        "generic loop insufficient time remaining before next model turn "
                        f"({remaining:.3f}s left of {req.timeout_seconds}s total timeout)",
                        steps=steps,
                        final_text=final_text,
                        elapsed_ms=int((time.monotonic() - t0) * 1000),
                        input_tokens=total_input,
                        output_tokens=total_output,
                    )
                runtime_event_index = await _emit_runtime_event(
                    req,
                    runtime_event_index,
                    "model_turn",
                    {"turn": turn},
                    "requesting next browser action",
                )
                prefer_provider = await get_case_execution_provider()
                model_prompt = _render_turn_prompt(req.prompt, tools, transcript)
                result = await get_gateway().chat(
                    model_prompt,
                    prompt_version="execute_generic_json_v1",
                    prefer=prefer_provider,
                    skip=["claude-cli"],
                    json_mode=True,
                    temperature=0,
                    timeout_seconds=max(0.1, min(remaining, 120)),
                )
                total_input += result.input_tokens
                total_output += result.output_tokens
                events.append(
                    {
                        "type": "model",
                        "turn": turn,
                        "provider": result.provider,
                        "model": result.model,
                        "text": _redact_text(result.text, req.secrets),
                    }
                )
                action = _parse_action(result.text)

                if "final" in action:
                    final_payload = _normalize_final_payload(action["final"], len(steps))
                    if not _final_has_enough_evidence(final_payload, steps):
                        transcript.append(
                            "Rejected premature final: before returning final, run at least "
                            "one Playwright tool and include assertion_results with concrete "
                            "evidence from observed page/tool output."
                        )
                        runtime_event_index = await _emit_runtime_event(
                            req,
                            runtime_event_index,
                            "rejected_final",
                            {"turn": turn},
                            "model returned final before enough Playwright evidence",
                        )
                        continue
                    final_text = _final_to_result_text(final_payload)
                    events.append({"type": "final", "text": final_text})
                    break

                tool_name = str(action.get("tool") or action.get("action") or "")
                arguments = action.get("arguments") or action.get("args") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if tool_name not in {t.name for t in tools}:
                    transcript.append(
                        f"Model requested invalid tool `{tool_name}`. "
                        "Choose one of the listed tools or return final."
                    )
                    runtime_event_index = await _emit_runtime_event(
                        req,
                        runtime_event_index,
                        "invalid_action",
                        {"turn": turn, "tool": tool_name},
                        f"invalid tool requested: {tool_name}",
                        is_error=True,
                    )
                    continue
                action_key = (
                    tool_name,
                    json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str),
                )
                if action_key == last_action_key:
                    repeated_action_count += 1
                else:
                    last_action_key = action_key
                    repeated_action_count = 1
                if repeated_action_count > 2 and tool_name not in _REPEATABLE_OBSERVATION_TOOLS:
                    transcript.append(
                        f"Rejected repeated action: `{tool_name}` with the same arguments "
                        "has already been attempted twice. Choose a different tool/action, "
                        "inspect the page, or return failed final with evidence."
                    )
                    runtime_event_index = await _emit_runtime_event(
                        req,
                        runtime_event_index,
                        "repeated_action",
                        {"turn": turn, "tool": tool_name, "arguments": arguments},
                        f"repeated action rejected: {tool_name}",
                        is_error=True,
                    )
                    continue

                runtime_event_index = await _emit_runtime_event(
                    req,
                    runtime_event_index,
                    "tool_start",
                    {
                        "turn": turn,
                        "tool": tool_name,
                        "arguments": _redact_value(arguments, req.secrets),
                    },
                    f"starting {tool_name}",
                )
                await _call_mcp_tool_recording(
                    req=req,
                    mcp=mcp,
                    tool_name=tool_name,
                    arguments=arguments,
                    events=events,
                    steps=steps,
                    transcript=transcript,
                    turn=turn,
                )
            else:
                raise _generic_error(
                    f"generic agent exceeded {max_turns} turns",
                    steps=steps,
                    final_text=final_text,
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                    input_tokens=total_input,
                    output_tokens=total_output,
                )

    except LLMError as exc:
        raise _generic_error(
            f"generic loop LLM error: {exc}",
            steps=steps,
            final_text=final_text,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            input_tokens=total_input,
            output_tokens=total_output,
        ) from exc
    except MCPClientError as exc:
        raise _generic_error(
            f"generic loop MCP error: {exc}",
            steps=steps,
            final_text=final_text,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            input_tokens=total_input,
            output_tokens=total_output,
        ) from exc
    finally:
        blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events).encode("utf-8")
        stdout_path.write_bytes(redact_bytes(blob, req.secrets))
        stderr_path.write_text("", encoding="utf-8")

    elapsed = int((time.monotonic() - t0) * 1000)
    summary = _build_summary(
        final_text=final_text,
        elapsed_ms=elapsed,
        input_tokens=total_input,
        output_tokens=total_output,
    )
    parsed = ParsedRun(steps=steps, summary=summary)

    _log.info(
        EVENTS.RUN_COMPLETED.name,
        runner="generic_openai",
        elapsed_ms=elapsed,
        steps=len(steps),
        success_hint=summary.success,
    )

    return RunOutcome(
        parsed=parsed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        mcp_config_path=mcp_config_path,
        exit_code=0,
        elapsed_ms=elapsed,
    )


async def _bootstrap_configured_login(
    *,
    req: RunRequest,
    mcp: Any,
    tools: list[MCPTool],
    transcript: list[str],
    events: list[dict[str, Any]],
    steps: list[StepEvent],
    runtime_event_index: int,
) -> int:
    """Use project credentials deterministically before the LLM loop starts."""
    if not _should_bootstrap_login(req):
        return runtime_event_index

    available = {tool.name for tool in tools}
    required = {"browser_navigate", "browser_snapshot", "browser_fill_form", "browser_click"}
    missing = sorted(required - available)
    if missing:
        raise _generic_error(
            "configured login cannot run because Playwright tools are missing: "
            + ", ".join(missing),
            steps=steps,
            final_text="",
            elapsed_ms=0,
            input_tokens=0,
            output_tokens=0,
        )

    runtime_event_index = await _emit_runtime_event(
        req,
        runtime_event_index,
        "auth_bootstrap",
        {"login_url": req.login_url},
        "starting configured login",
    )
    await _call_mcp_tool_recording(
        req=req,
        mcp=mcp,
        tool_name="browser_navigate",
        arguments={"url": req.login_url},
        events=events,
        steps=steps,
        transcript=transcript,
        turn="auth",
    )
    snapshot_step = await _call_mcp_tool_recording(
        req=req,
        mcp=mcp,
        tool_name="browser_snapshot",
        arguments={},
        events=events,
        steps=steps,
        transcript=transcript,
        turn="auth",
    )
    refs = _extract_login_refs(snapshot_step.result_text or "")
    if not refs:
        raise _generic_error(
            "configured login page did not expose email/password fields and a login button",
            steps=steps,
            final_text="",
            elapsed_ms=0,
            input_tokens=0,
            output_tokens=0,
        )

    email_ref, password_ref, button_label, button_ref = refs
    await _call_mcp_tool_recording(
        req=req,
        mcp=mcp,
        tool_name="browser_fill_form",
        arguments={
            "fields": [
                {
                    "name": "E-mail",
                    "type": "textbox",
                    "ref": email_ref,
                    "value": req.default_username,
                },
                {
                    "name": "Password",
                    "type": "textbox",
                    "ref": password_ref,
                    "value": req.default_password,
                },
            ]
        },
        events=events,
        steps=steps,
        transcript=transcript,
        turn="auth",
    )
    click_step = await _call_mcp_tool_recording(
        req=req,
        mcp=mcp,
        tool_name="browser_click",
        arguments={"element": f"{button_label} button", "ref": button_ref},
        events=events,
        steps=steps,
        transcript=transcript,
        turn="auth",
    )
    if click_step.result_is_error:
        raise _generic_error(
            "configured login submit failed",
            steps=steps,
            final_text="",
            elapsed_ms=0,
            input_tokens=0,
            output_tokens=0,
        )

    transcript.append(
        "AUTH BOOTSTRAP completed using the configured project login URL and "
        "credentials. Treat old explicit login/navigation-to-login steps as "
        "already satisfied unless later page evidence shows the session is not authenticated."
    )
    runtime_event_index = await _emit_runtime_event(
        req,
        runtime_event_index,
        "auth_bootstrap",
        {"login_url": req.login_url},
        "configured login completed",
    )
    return runtime_event_index


def _should_bootstrap_login(req: RunRequest) -> bool:
    return (
        (req.auth_state or "").strip().lower() == "logged-in"
        and bool((req.login_url or "").strip())
        and bool((req.default_username or "").strip())
        and bool(req.default_password)
    )


_REF_RE = re.compile(
    r'\b(?P<role>textbox|button)\s+"(?P<label>[^"]+)"[^\n]*\[ref=(?P<ref>[^\]]+)\]'
)
_EMAIL_LABEL_RE = re.compile(r"e-?mail|email|user\s*name|username|account", re.IGNORECASE)
_PASSWORD_LABEL_RE = re.compile(r"password|passcode|密码", re.IGNORECASE)
_LOGIN_LABEL_RE = re.compile(r"log\s*in|login|sign\s*in|登录|se connecter|connexion", re.IGNORECASE)


def _extract_login_refs(text: str) -> tuple[str, str, str, str] | None:
    email_ref = ""
    password_ref = ""
    login_ref = ""
    login_label = "Log in"
    for match in _REF_RE.finditer(text):
        role = match.group("role")
        label = match.group("label")
        ref = match.group("ref")
        if role == "textbox" and not email_ref and _EMAIL_LABEL_RE.search(label):
            email_ref = ref
        elif role == "textbox" and not password_ref and _PASSWORD_LABEL_RE.search(label):
            password_ref = ref
        elif role == "button" and not login_ref and _LOGIN_LABEL_RE.search(label):
            login_ref = ref
            login_label = label.strip() or login_label
    if email_ref and password_ref and login_ref:
        return email_ref, password_ref, login_label, login_ref
    return None


async def _call_mcp_tool_recording(
    *,
    req: RunRequest,
    mcp: Any,
    tool_name: str,
    arguments: dict[str, Any],
    events: list[dict[str, Any]],
    steps: list[StepEvent],
    transcript: list[str],
    turn: int | str,
) -> StepEvent:
    idx = len(steps)
    safe_args = _redact_value(arguments, req.secrets)
    step = StepEvent(
        step_index=idx,
        tool_name=tool_name,
        tool_full_name=f"mcp__playwright__{tool_name}",
        tool_args=safe_args,
        tool_use_id=f"generic-{idx}",
        is_playwright=True,
    )
    steps.append(step)

    started = time.monotonic()
    try:
        tool_result = await mcp.call_tool(tool_name, arguments)
        result_text = _flatten_mcp_content(tool_result.get("content"))
        is_error = bool(tool_result.get("isError") or tool_result.get("is_error"))
    except MCPClientError as exc:
        result_text = str(exc)
        is_error = True
    elapsed_ms = int((time.monotonic() - started) * 1000)

    result_text = _redact_text(result_text, req.secrets)
    result_text = _sanitize_tool_result_text(
        result_text,
        tool_name=tool_name,
        safe_args=safe_args,
    )
    step.result_text = result_text
    step.result_is_error = is_error
    extracted = _parse_tool_result_text(result_text)
    step.page_url = extracted.get("page_url")
    step.page_title = extracted.get("page_title")
    step.console_errors = extracted.get("console_errors")
    step.console_warnings = extracted.get("console_warnings")
    step.screenshot_path = extracted.get("screenshot_path")
    if not step.screenshot_path and tool_name == "browser_take_screenshot":
        filename = safe_args.get("filename")
        if isinstance(filename, str) and filename.strip():
            step.screenshot_path = filename.strip()

    events.append(
        {
            "type": "tool",
            "turn": turn,
            "tool": tool_name,
            "arguments": safe_args,
            "is_error": is_error,
            "elapsed_ms": elapsed_ms,
            "result_text": result_text[:4000],
        }
    )
    transcript.append(
        f"TOOL {tool_name}({json.dumps(safe_args, ensure_ascii=False)}) "
        f"=> {'ERROR' if is_error else 'OK'}\n{result_text[:6000]}"
    )
    return step


def _initial_transcript(_prompt: str, _tools: list[MCPTool]) -> list[str]:
    return [
        "Start by taking a browser_snapshot unless you need to navigate first. "
        "Use short, deliberate actions. Do not repeat the same action with the "
        "same arguments more than twice. Do not return final until at least one "
        "tool observation supports the assertion result."
    ]


def _remaining_seconds(started: float, timeout_seconds: int) -> float:
    return float(timeout_seconds) - (time.monotonic() - started)


def _generic_error(
    message: str,
    *,
    steps: list[StepEvent],
    final_text: str,
    elapsed_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> GenericRunnerError:
    return GenericRunnerError(
        message,
        partial=ParsedRun(
            steps=steps,
            summary=_build_summary(
                final_text=final_text,
                elapsed_ms=elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        ),
    )


async def _emit_runtime_event(
    req: RunRequest,
    index: int,
    tool_name: str,
    tool_args: dict[str, Any],
    result_text: str,
    *,
    is_error: bool = False,
) -> int:
    if req.on_runtime_event is None:
        return index
    await req.on_runtime_event(
        StepEvent(
            step_index=index,
            tool_name=tool_name,
            tool_full_name=f"michelle.{tool_name}",
            tool_args=tool_args,
            tool_use_id=f"runtime-{index}",
            is_playwright=False,
            result_text=result_text,
            result_is_error=is_error,
        )
    )
    return index + 1


def _render_turn_prompt(test_prompt: str, tools: list[MCPTool], transcript: list[str]) -> str:
    tool_lines = []
    for t in tools:
        schema = json.dumps(t.input_schema, ensure_ascii=False)[:1200]
        tool_lines.append(f"- {t.name}: {t.description}\n  input_schema={schema}")

    history = "\n\n".join(transcript[-12:])
    return f"""You are Michelle's browser test execution loop.

You must execute the test case by choosing exactly one Playwright MCP tool per
turn, or return a final RESULT when done.

Return ONLY one JSON object, no markdown:

Tool action:
{{"tool":"browser_snapshot","arguments":{{}},"reason":"why this action is next"}}

Final:
{{"final":{{"case_status":"passed|failed","step_count":N,"assertion_results":[{{"description":"...","passed":true|false,"evidence":"..."}}],"failure_summary":"<empty if passed; one sentence if failed>"}}}}

Rules:
- Never invent page state. Evidence must come from Recent observations.
- Do not return final before using at least one tool.
- Mark passed only when every explicit assertion is supported by observed evidence.
- If a required element is missing or an action fails twice, inspect once, then try a different route or return failed with evidence.
- Do not repeat the same tool with identical arguments more than twice.
- Prefer browser_snapshot to understand the current page, browser_navigate to open URLs, and locator/ref based actions when the snapshot provides refs.
- Keep `reason` short. It is for trace readability, not hidden planning.

Available tools:
{chr(10).join(tool_lines)}

Test case:
{test_prompt}

Recent observations:
{history}
"""


def _parse_action(text: str) -> dict[str, Any]:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        for m in re.finditer(r"\{", s):
            try:
                data, _end = decoder.raw_decode(s[m.start() :])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise GenericRunnerError(f"model did not return JSON action: {text[:300]}") from exc
    if not isinstance(data, dict):
        raise GenericRunnerError("model JSON action must be an object")
    return data


def _final_to_result_text(final: Any) -> str:
    final = _normalize_final_payload(final, step_count=0)
    return "RESULT=" + json.dumps(final, ensure_ascii=False)


def _normalize_final_payload(final: Any, step_count: int) -> dict[str, Any]:
    if not isinstance(final, dict):
        final = {
            "case_status": "failed",
            "step_count": step_count,
            "assertion_results": [],
            "failure_summary": "model returned malformed final payload",
        }
    status = str(final.get("case_status") or "").lower()
    if status not in {"passed", "failed"}:
        final["case_status"] = "failed"
        final["failure_summary"] = (
            final.get("failure_summary") or "model returned invalid case_status"
        )
    if not isinstance(final.get("step_count"), int) or final["step_count"] < step_count:
        final["step_count"] = step_count
    if not isinstance(final.get("assertion_results"), list):
        final["assertion_results"] = []
    if final.get("case_status") == "passed":
        final["failure_summary"] = ""
    elif not str(final.get("failure_summary") or "").strip():
        final["failure_summary"] = "test failed; model did not provide a failure summary"
    return final


def _final_has_enough_evidence(final: dict[str, Any], steps: list[StepEvent]) -> bool:
    if not steps:
        return False
    assertions = final.get("assertion_results")
    if not isinstance(assertions, list) or not assertions:
        return False
    for assertion in assertions:
        if not isinstance(assertion, dict):
            return False
        evidence = str(assertion.get("evidence") or "").strip()
        if len(evidence) < 8:
            return False
    return True


def _build_summary(
    *,
    final_text: str,
    elapsed_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> RunSummary:
    parsed: dict[str, Any] | None = None
    if final_text.startswith("RESULT="):
        try:
            parsed = json.loads(final_text[len("RESULT=") :])
        except json.JSONDecodeError:
            parsed = None
    success = str((parsed or {}).get("case_status", "")).lower() in {"passed", "pass", "ok"}
    return RunSummary(
        success=success,
        final_text=final_text,
        parsed_result=parsed,
        duration_ms=elapsed_ms,
        num_turns=None,
        cost_usd=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=None if final_text else "generic loop produced no final result",
        raw_result={"runner": "generic_openai", "duration_ms": elapsed_ms},
    )


def _flatten_mcp_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(str(item.get("text", "")))
                elif item.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(json.dumps(item, ensure_ascii=False)[:1000])
            else:
                out.append(str(item))
        return "\n".join(x for x in out if x)
    return "" if content is None else str(content)


def _sanitize_tool_result_text(
    text: str,
    *,
    tool_name: str,
    safe_args: dict[str, Any],
) -> str:
    """Keep large browser artifacts out of transcripts and JSONL logs.

    MCP tools may return screenshots as data URIs or very large page/network
    dumps. The run directory is the artifact store; the model transcript only
    needs concise evidence and file names.
    """
    if not text:
        return text

    filename = safe_args.get("filename")
    screenshot_hint = (
        f"[screenshot saved to {filename}]"
        if tool_name == "browser_take_screenshot" and isinstance(filename, str) and filename
        else "[image data omitted]"
    )
    text = _DATA_URI_RE.sub(screenshot_hint, text)

    if tool_name == "browser_take_screenshot" and len(text) > 2000:
        return f"{screenshot_hint}\n{_truncate_text(text, 2000)}"
    return _truncate_text(text, _MAX_TOOL_RESULT_TEXT_CHARS)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"


def _redact_text(text: str, secrets: list[str] | None) -> str:
    if not secrets or not text:
        return text
    out = text
    for s in secrets:
        if s and len(s) >= 3:
            out = out.replace(s, "***")
    return out


def _redact_value(v: Any, secrets: list[str] | None) -> Any:
    if isinstance(v, str):
        return _redact_text(v, secrets)
    if isinstance(v, dict):
        return {k: _redact_value(vv, secrets) for k, vv in v.items()}
    if isinstance(v, list):
        return [_redact_value(item, secrets) for item in v]
    return v
