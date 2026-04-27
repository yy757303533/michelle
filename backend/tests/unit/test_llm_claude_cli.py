"""Unit tests for ClaudeCLIClient — subprocess is fully mocked."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import LLMAuthError, LLMResponseFormatError, LLMTimeoutError, RateLimitError
from app.llm.claude_cli import ClaudeCLIClient


def _mk_proc(*, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()  # asyncio.subprocess.Process.kill is sync
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_claude_cli_happy_path():
    payload = {
        "result": "ok",
        "session_id": "sess_1",
        "num_turns": 1,
        "total_cost_usd": 0.001,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 5,
            "output_tokens": 1,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "modelUsage": {
            "claude-opus-4-7[1m]": {"inputTokens": 5, "outputTokens": 1, "costUSD": 0.001}
        },
    }
    proc = _mk_proc(stdout=json.dumps(payload).encode())
    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude")
        r = await c.chat("hi", prompt_version="probe_v1")
    assert r.text == "ok"
    assert r.provider == "claude-cli"
    assert r.input_tokens == 5
    assert r.output_tokens == 1
    assert r.model == "claude-opus-4-7[1m]"
    assert r.cost_usd == 0.001


@pytest.mark.asyncio
async def test_claude_cli_rate_limited_via_stderr():
    proc = _mk_proc(returncode=1, stderr=b"5h limit reached. Please wait.")
    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude")
        with pytest.raises(RateLimitError):
            await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_claude_cli_auth_error_on_not_logged_in():
    proc = _mk_proc(returncode=1, stderr=b"Not logged in. Run claude /login.")
    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude")
        with pytest.raises(LLMAuthError):
            await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_claude_cli_timeout_kills_proc():
    import asyncio

    async def slow_communicate(*a, **kw):
        await asyncio.sleep(10)
        return (b"", b"")

    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = slow_communicate
    proc.kill = MagicMock()  # sync
    proc.wait = AsyncMock()

    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude", default_timeout=1)
        with pytest.raises(LLMTimeoutError):
            await c.chat("hi", prompt_version="probe_v1", timeout_seconds=1)
    proc.kill.assert_called()


@pytest.mark.asyncio
async def test_claude_cli_unparseable_stdout():
    proc = _mk_proc(stdout=b"not json")
    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude")
        with pytest.raises(LLMResponseFormatError):
            await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_claude_cli_json_mode_strips_fences():
    payload = {
        "result": '```json\n{"a": 1}\n```',
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    proc = _mk_proc(stdout=json.dumps(payload).encode())
    with patch("app.llm.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        c = ClaudeCLIClient(binary="claude")
        r = await c.chat("hi", prompt_version="probe_v1", json_mode=True)
    assert r.text == '{"a": 1}'
