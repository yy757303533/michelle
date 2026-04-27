"""Unit tests for the generic OpenAICompatibleClient (Kimi/Qwen/DeepSeek/GLM/Gemini/relay)."""

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
from app.llm.openai_compatible import OpenAICompatibleClient


URL = "https://api.example.com/v1/chat/completions"


def _client(supports_image: bool = False) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        name="test",
        api_key="sk-test",
        base_url=URL,
        default_model="some-model",
        supports_image=supports_image,
    )


@pytest.mark.asyncio
@respx.mock
async def test_happy_path():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "abc",
                "model": "some-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1},
            },
        )
    )
    r = await _client().chat("hi", prompt_version="probe_v1")
    assert r.text == "ok"
    assert r.provider == "test"
    assert r.input_tokens == 4
    assert r.output_tokens == 1


@pytest.mark.asyncio
@respx.mock
async def test_401_auth_error():
    respx.post(URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(LLMAuthError):
        await _client().chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_402_quota_exceeded():
    respx.post(URL).mock(
        return_value=httpx.Response(
            402,
            json={"error": {"type": "quote_exceeded", "message": "subscription quota"}},
        )
    )
    with pytest.raises(QuotaExceededError):
        await _client().chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_429_rate_limited():
    respx.post(URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "too many requests"}})
    )
    with pytest.raises(RateLimitError):
        await _client().chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_quota_detected_in_2xx_body():
    """Some relays return HTTP 200 with an error envelope inside."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={"error": {"type": "insufficient_user_quota", "message": "quota gone"}},
        )
    )
    with pytest.raises(QuotaExceededError):
        await _client().chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_no_api_key_raises_auth_error():
    c = OpenAICompatibleClient(
        name="t", api_key="", base_url=URL, default_model="m"
    )
    with pytest.raises(LLMAuthError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_image_sent_when_supported():
    captured = {}

    def _capture(req: httpx.Request):
        captured["body"] = req.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "saw image"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5},
            },
        )

    respx.post(URL).mock(side_effect=_capture)
    r = await _client(supports_image=True).chat(
        "describe", prompt_version="probe_v1", image=b"\x89PNG\r\n\x1a\n"
    )
    assert r.text == "saw image"
    assert "image_url" in captured["body"]
    assert "data:image/png;base64," in captured["body"]


@pytest.mark.asyncio
@respx.mock
async def test_image_dropped_when_not_supported():
    captured = {}

    def _capture(req: httpx.Request):
        captured["body"] = req.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "text only"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    respx.post(URL).mock(side_effect=_capture)
    await _client(supports_image=False).chat(
        "hi", prompt_version="probe_v1", image=b"\x89PNG\r\n"
    )
    assert "image_url" not in captured["body"]


@pytest.mark.asyncio
@respx.mock
async def test_no_choices_raises_format_error():
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"choices": [], "usage": {}})
    )
    with pytest.raises(LLMResponseFormatError):
        await _client().chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
@respx.mock
async def test_reasoning_content_surfaced_to_metadata():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "answer",
                            "reasoning_content": "thinking...",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    r = await _client().chat("hi", prompt_version="probe_v1")
    assert r.text == "answer"
    assert r.metadata["reasoning_content"] == "thinking..."


@pytest.mark.asyncio
@respx.mock
async def test_temperature_and_json_mode_passed_through():
    captured = {}

    def _capture(req: httpx.Request):
        import json as _j

        captured["body"] = _j.loads(req.read())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    respx.post(URL).mock(side_effect=_capture)
    await _client().chat(
        "hi",
        prompt_version="probe_v1",
        temperature=0.0,
        json_mode=True,
    )
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["response_format"] == {"type": "json_object"}
