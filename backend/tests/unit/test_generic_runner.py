from __future__ import annotations

import pytest

from app.agent.claude_runner import RunRequest
from app.agent.generic_runner import (
    GenericRunnerError,
    _final_has_enough_evidence,
    _normalize_final_payload,
    _parse_action,
    _render_turn_prompt,
    _sanitize_tool_result_text,
    run_generic_with_playwright,
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


def test_sanitize_tool_result_removes_screenshot_data_uri() -> None:
    raw = "before data:image/png;base64," + ("a" * 2000) + " after"

    text = _sanitize_tool_result_text(
        raw,
        tool_name="browser_take_screenshot",
        safe_args={"filename": "step-1.png"},
    )

    assert "data:image/png;base64" not in text
    assert "a" * 200 not in text
    assert "step-1.png" in text


@pytest.mark.asyncio
async def test_run_generic_bootstraps_configured_login_before_model(tmp_path, monkeypatch) -> None:
    from app.llm.base import LLMResult

    snapshot = """
- textbox "E-mail *" [ref=e28]
- textbox "Password *" [ref=e38]
- button "Log in" [ref=e50]
"""

    class FakeMCP:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def list_tools(self):
            return [
                MCPTool(name="browser_navigate", description="navigate", input_schema={}),
                MCPTool(name="browser_snapshot", description="snapshot", input_schema={}),
                MCPTool(name="browser_fill_form", description="fill", input_schema={}),
                MCPTool(name="browser_click", description="click", input_schema={}),
            ]

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "browser_snapshot":
                return {"content": [{"type": "text", "text": snapshot}]}
            return {"content": [{"type": "text", "text": "ok"}]}

    fake_mcp = FakeMCP()

    class FakeGateway:
        async def chat(self, prompt, *_args, **_kwargs):
            assert "AUTH BOOTSTRAP completed" in prompt
            return LLMResult(
                text=(
                    '{"final":{"case_status":"passed","step_count":4,'
                    '"assertion_results":[{"description":"login completed",'
                    '"passed":true,"evidence":"auth bootstrap reached the app"}],'
                    '"failure_summary":""}}'
                ),
                provider="fake",
                model="fake-model",
            )

    monkeypatch.setattr(
        "app.agent.generic_runner.build_playwright_stdio_client",
        lambda **_kw: fake_mcp,
    )
    monkeypatch.setattr("app.agent.generic_runner.get_gateway", lambda: FakeGateway())
    monkeypatch.setattr("app.agent.generic_runner.get_case_execution_provider", AsyncProvider())

    outcome = await run_generic_with_playwright(
        RunRequest(
            prompt="verify home page",
            work_dir=tmp_path,
            timeout_seconds=60,
            auth_state="logged-in",
            login_url="https://example.test/logins",
            default_username="admin@example.test",
            default_password="secret-pass",
            secrets=["secret-pass"],
        )
    )

    assert [name for name, _args in fake_mcp.calls] == [
        "browser_navigate",
        "browser_snapshot",
        "browser_fill_form",
        "browser_click",
    ]
    assert fake_mcp.calls[0][1] == {"url": "https://example.test/logins"}
    assert fake_mcp.calls[2][1]["fields"][0]["value"] == "admin@example.test"
    assert fake_mcp.calls[2][1]["fields"][1]["value"] == "secret-pass"
    assert fake_mcp.calls[3][1] == {"element": "Log in button", "ref": "e50"}
    assert [step.tool_name for step in outcome.parsed.steps[:4]] == [
        "browser_navigate",
        "browser_snapshot",
        "browser_fill_form",
        "browser_click",
    ]
    assert outcome.parsed.steps[2].tool_args["fields"][1]["value"] == "***"


@pytest.mark.asyncio
async def test_run_generic_enforces_total_timeout_before_turn(tmp_path, monkeypatch) -> None:
    class FakeMCP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def list_tools(self):
            return [
                MCPTool(
                    name="browser_snapshot",
                    description="snapshot",
                    input_schema={"type": "object"},
                )
            ]

    monkeypatch.setattr(
        "app.agent.generic_runner.build_playwright_stdio_client",
        lambda **_kw: FakeMCP(),
    )

    with pytest.raises(GenericRunnerError, match="exceeded total timeout"):
        await run_generic_with_playwright(
            RunRequest(
                prompt="open page",
                work_dir=tmp_path,
                timeout_seconds=0,
            )
        )


@pytest.mark.asyncio
async def test_run_generic_does_not_start_model_call_with_tiny_remaining_budget(
    tmp_path, monkeypatch
) -> None:
    class FakeMCP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def list_tools(self):
            return [
                MCPTool(
                    name="browser_snapshot",
                    description="snapshot",
                    input_schema={"type": "object"},
                )
            ]

    class GatewayShouldNotRun:
        async def chat(self, *_args, **_kwargs):
            raise AssertionError("model call should not start with tiny remaining budget")

    monkeypatch.setattr(
        "app.agent.generic_runner.build_playwright_stdio_client",
        lambda **_kw: FakeMCP(),
    )
    monkeypatch.setattr("app.agent.generic_runner.get_gateway", lambda: GatewayShouldNotRun())
    monkeypatch.setattr("app.agent.generic_runner.get_case_execution_provider", AsyncProvider())
    monkeypatch.setattr("app.agent.generic_runner._remaining_seconds", lambda *_args: 2.0)

    with pytest.raises(GenericRunnerError, match="insufficient time remaining"):
        await run_generic_with_playwright(
            RunRequest(
                prompt="open page",
                work_dir=tmp_path,
                timeout_seconds=60,
            )
        )


@pytest.mark.asyncio
async def test_run_generic_emits_runtime_events(tmp_path, monkeypatch) -> None:
    from app.llm.base import LLMResult

    class FakeMCP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def list_tools(self):
            return [
                MCPTool(
                    name="browser_snapshot",
                    description="snapshot",
                    input_schema={"type": "object"},
                )
            ]

    class FakeGateway:
        async def chat(self, *_args, **_kwargs):
            return LLMResult(
                text='{"tool":"missing_tool","arguments":{}}',
                provider="fake",
                model="fake-model",
            )

    events: list[StepEvent] = []

    async def on_event(step: StepEvent) -> None:
        events.append(step)

    monkeypatch.setattr(
        "app.agent.generic_runner.build_playwright_stdio_client",
        lambda **_kw: FakeMCP(),
    )
    monkeypatch.setattr("app.agent.generic_runner.get_gateway", lambda: FakeGateway())
    monkeypatch.setattr("app.agent.generic_runner.get_case_execution_provider", AsyncProvider())
    monkeypatch.setattr("app.agent.generic_runner.settings.generic_agent_max_turns", 1)

    with pytest.raises(GenericRunnerError, match="exceeded 1 turns"):
        await run_generic_with_playwright(
            RunRequest(
                prompt="open page",
                work_dir=tmp_path,
                timeout_seconds=60,
                on_runtime_event=on_event,
            )
        )

    assert [event.tool_name for event in events] == ["model_turn", "invalid_action"]


class AsyncProvider:
    async def __call__(self):
        return "fake"
