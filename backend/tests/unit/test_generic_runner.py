from __future__ import annotations

from app.agent.generic_runner import (
    _final_has_enough_evidence,
    _normalize_final_payload,
    _parse_action,
    _render_turn_prompt,
)
from app.agent.mcp_stdio import MCPTool
from app.agent.trace_parser import StepEvent


def test_parse_action_accepts_fenced_json() -> None:
    action = _parse_action('```json\n{"tool":"browser_snapshot","arguments":{}}\n```')
    assert action == {"tool": "browser_snapshot", "arguments": {}}


def test_parse_action_accepts_json_with_trailing_prose() -> None:
    action = _parse_action(
        '{"final":{"case_status":"passed","step_count":1,'
        '"assertion_results":[{"description":"x","passed":true,"evidence":"visible"}],'
        '"failure_summary":""}}\n\nDone.'
    )
    assert action["final"]["case_status"] == "passed"


def test_render_turn_prompt_includes_execution_guardrails() -> None:
    prompt = _render_turn_prompt(
        "open the login page",
        [
            MCPTool(
                name="browser_snapshot",
                description="snapshot",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        ["TOOL browser_snapshot({}) => OK\nLogin button visible"],
    )

    assert "Return ONLY one JSON object" in prompt
    assert "Do not return final before using at least one tool" in prompt
    assert "Never invent page state" in prompt
    assert "browser_snapshot" in prompt


def test_normalize_final_payload_requires_valid_status_and_step_count() -> None:
    final = _normalize_final_payload(
        {"case_status": "maybe", "step_count": 0, "assertion_results": []},
        step_count=3,
    )

    assert final["case_status"] == "failed"
    assert final["step_count"] == 3
    assert final["failure_summary"]


def test_final_evidence_requires_steps_and_assertion_evidence() -> None:
    step = StepEvent(
        step_index=0,
        tool_name="browser_snapshot",
        tool_full_name="mcp__playwright__browser_snapshot",
        tool_args={},
        tool_use_id="generic-0",
        is_playwright=True,
    )

    assert not _final_has_enough_evidence(
        {"assertion_results": [{"description": "x", "passed": True, "evidence": "visible"}]},
        [],
    )
    assert _final_has_enough_evidence(
        {
            "assertion_results": [
                {
                    "description": "login button visible",
                    "passed": True,
                    "evidence": "snapshot showed the Login button",
                }
            ]
        },
        [step],
    )
