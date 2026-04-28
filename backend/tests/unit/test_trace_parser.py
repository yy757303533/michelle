"""Unit tests for the stream-json trace parser.

These use small handcrafted fixtures, not real Claude runs, so they're fast
and deterministic.
"""

from __future__ import annotations

import json

from app.agent.trace_parser import parse_stream


def _line(obj: dict) -> str:
    return json.dumps(obj)


def test_parse_empty_stream():
    parsed = parse_stream([])
    assert parsed.steps == []
    assert parsed.summary.success is False
    assert parsed.summary.error == "no result frame"


def test_parse_minimal_run_with_one_tool_call():
    lines = [
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_1",
                            "name": "mcp__playwright__browser_navigate",
                            "input": {"url": "http://example.com"},
                        }
                    ]
                },
            }
        ),
        _line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "### Page\n"
                                        "- Page URL: http://example.com\n"
                                        "- Page Title: Example\n"
                                        "- Console: 0 errors, 1 warnings\n"
                                    ),
                                }
                            ],
                        }
                    ]
                },
            }
        ),
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": 'Done.\nRESULT={"login":"success","step_count":1}',
                        }
                    ]
                },
            }
        ),
        _line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 1234,
                "num_turns": 2,
                "total_cost_usd": 0.01,
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }
        ),
    ]

    parsed = parse_stream(lines)

    assert len(parsed.steps) == 1
    s = parsed.steps[0]
    assert s.tool_name == "browser_navigate"
    assert s.tool_full_name == "mcp__playwright__browser_navigate"
    assert s.is_playwright is True
    assert s.tool_args == {"url": "http://example.com"}
    assert s.page_url == "http://example.com"
    assert s.page_title == "Example"
    assert s.console_errors == 0
    assert s.console_warnings == 1

    assert parsed.summary.success is True
    assert parsed.summary.parsed_result == {"login": "success", "step_count": 1}
    assert parsed.summary.duration_ms == 1234
    assert parsed.summary.input_tokens == 5
    assert parsed.summary.output_tokens == 50


def test_failure_hint_when_login_failed():
    lines = [
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": 'RESULT={"login":"failed","evidence":"still on login page"}',
                        }
                    ]
                },
            }
        ),
        _line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 100,
                "num_turns": 1,
                "total_cost_usd": 0.0,
                "usage": {},
            }
        ),
    ]
    parsed = parse_stream(lines)
    assert parsed.summary.success is False
    assert parsed.summary.parsed_result == {"login": "failed", "evidence": "still on login page"}


def test_non_playwright_tool_is_marked_correctly():
    lines = [
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "x", "name": "ToolSearch", "input": {"q": "foo"}}
                    ]
                },
            }
        ),
    ]
    parsed = parse_stream(lines)
    assert len(parsed.steps) == 1
    assert parsed.steps[0].is_playwright is False
    assert parsed.steps[0].tool_name == "ToolSearch"
    assert parsed.playwright_steps == []
