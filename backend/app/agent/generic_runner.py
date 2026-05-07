"""Michelle-owned generic browser agent loop.

This is the portable execution path for project/team use: an
OpenAI-compatible model produces JSON actions, Michelle calls
`@playwright/mcp` directly, persists every tool call, and stops when the model
returns the required RESULT payload.

The first implementation intentionally uses a conservative JSON-action
protocol instead of provider-specific native tool calling. That keeps Qwen /
Kimi / GLM / DeepSeek / relay gateways on one code path. Native tool-calling
can be added later under the same runner without changing the UI contract.
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

_log = get_logger(__name__)


class GenericRunnerError(RuntimeError):
    pass


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


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
            extra_args=req.extra_mcp_args,
        ),
    )

    events: list[dict[str, Any]] = []
    steps: list[StepEvent] = []
    final_text = ""
    total_input = 0
    total_output = 0
    t0 = time.monotonic()

    try:
        async with build_playwright_stdio_client(
            cwd=work,
            headless=req.headless,
            isolated=req.isolated,
            extra_args=req.extra_mcp_args,
        ) as mcp:
            tools = await mcp.list_tools()
            transcript = _initial_transcript(req.prompt, tools)
            max_turns = max(1, settings.generic_agent_max_turns)
            last_action_key: tuple[str, str] | None = None
            repeated_action_count = 0

            for turn in range(max_turns):
                model_prompt = _render_turn_prompt(req.prompt, tools, transcript)
                result = await get_gateway().chat(
                    model_prompt,
                    prompt_version="execute_generic_json_v1",
                    skip=["claude-cli", "codex-cli", "minimax"],
                    json_mode=True,
                    temperature=0,
                    timeout_seconds=min(req.timeout_seconds, 120),
                )
                total_input += result.input_tokens
                total_output += result.output_tokens
                action = _parse_action(result.text)
                events.append(
                    {
                        "type": "model",
                        "turn": turn,
                        "provider": result.provider,
                        "model": result.model,
                        "text": _redact_text(result.text, req.secrets),
                    }
                )

                if "final" in action:
                    final_payload = _normalize_final_payload(action["final"], len(steps))
                    if not _final_has_enough_evidence(final_payload, steps):
                        transcript.append(
                            "Rejected premature final: before returning final, run at least "
                            "one Playwright tool and include assertion_results with concrete "
                            "evidence from observed page/tool output."
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
                if repeated_action_count > 2:
                    transcript.append(
                        f"Rejected repeated action: `{tool_name}` with the same arguments "
                        "has already been attempted twice. Choose a different tool/action, "
                        "inspect the page, or return failed final with evidence."
                    )
                    continue

                idx = len(steps)
                tool_use_id = f"generic-{idx}"
                safe_args = _redact_value(arguments, req.secrets)
                step = StepEvent(
                    step_index=idx,
                    tool_name=tool_name,
                    tool_full_name=f"mcp__playwright__{tool_name}",
                    tool_args=safe_args,
                    tool_use_id=tool_use_id,
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
                step.result_text = result_text
                step.result_is_error = is_error
                extracted = _parse_tool_result_text(result_text)
                step.page_url = extracted.get("page_url")
                step.page_title = extracted.get("page_title")
                step.console_errors = extracted.get("console_errors")
                step.console_warnings = extracted.get("console_warnings")
                step.screenshot_path = extracted.get("screenshot_path")

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
            else:
                raise GenericRunnerError(f"generic agent exceeded {max_turns} turns")

    except LLMError as exc:
        raise GenericRunnerError(f"generic loop LLM error: {exc}") from exc
    except MCPClientError as exc:
        raise GenericRunnerError(f"generic loop MCP error: {exc}") from exc
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


def _initial_transcript(_prompt: str, _tools: list[MCPTool]) -> list[str]:
    return [
        "Start by taking a browser_snapshot unless you need to navigate first. "
        "Use short, deliberate actions. Do not repeat the same action with the "
        "same arguments more than twice. Do not return final until at least one "
        "tool observation supports the assertion result."
    ]


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
        m = _JSON_RE.search(s)
        if not m:
            raise GenericRunnerError(f"model did not return JSON action: {text[:300]}") from exc
        data = json.loads(m.group(0))
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
