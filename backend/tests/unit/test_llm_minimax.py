"""Unit tests for MiniMaxClient — httpx mocked via respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.llm.base import (
    LLMAuthError,
    LLMResponseFormatError,
    QuotaExceededError,
    RateLimitError,
)
from app.llm.minimax import MiniMaxClient

URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"


@pytest.mark.asyncio
@respx.mock
async def test_minimax_happy_path():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "abc",
                "model": "MiniMax-Text-01",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )
    )
    c = MiniMaxClient(api_key="testkey")
    r = await c.chat("hi", prompt_version="probe_v1")
    assert r.text == "ok"
    assert r.provider == "minimax"
    assert r.input_tokens == 5
    assert r.output_tokens == 1


@pytest.mark.asyncio
@respx.mock
async def test_minimax_invalid_api_key():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": None,
                "base_resp": {"status_code": 2049, "status_msg": "invalid api key"},
            },
        )
    )
    c = MiniMaxClient(api_key="badkey")
    with pytest.raises(LLMAuthError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_minimax_quota_exceeded():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": None,
                "base_resp": {
                    "status_code": 1024,
                    "status_msg": "subscription quota exceeded",
                },
            },
        )
    )
    c = MiniMaxClient(api_key="testkey")
    with pytest.raises(QuotaExceededError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_minimax_rate_limited():
    respx.post(URL).mock(
        return_value=httpx.Response(
            429,
            json={
                "choices": None,
                "base_resp": {"status_code": 0, "status_msg": "rate limit exceeded"},
            },
        )
    )
    c = MiniMaxClient(api_key="testkey")
    with pytest.raises(RateLimitError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_minimax_no_key_raises_auth_error():
    c = MiniMaxClient(api_key="")
    with pytest.raises(LLMAuthError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_minimax_passes_image_as_base64():
    captured = {}

    def _capture(request: httpx.Request):
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "image seen"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5},
                "base_resp": {"status_code": 0},
            },
        )

    respx.post(URL).mock(side_effect=_capture)

    c = MiniMaxClient(api_key="testkey")
    r = await c.chat("see this", prompt_version="probe_v1", image=b"\x89PNG\r\n\x1a\n")

    assert r.text == "image seen"
    assert "image_url" in captured["body"]
    assert "data:image/png;base64," in captured["body"]


@pytest.mark.asyncio
@respx.mock
async def test_minimax_unparseable_response():
    respx.post(URL).mock(return_value=httpx.Response(200, content=b"<html>oops</html>"))
    c = MiniMaxClient(api_key="testkey")
    with pytest.raises(LLMResponseFormatError):
        await c.chat("hi", prompt_version="probe_v1")
