"""Gateway tests — verify fallback ordering and error semantics."""

from __future__ import annotations

import pytest

from app.llm.base import (
    BaseChatClient,
    LLMAuthError,
    LLMError,
    LLMResult,
    QuotaExceededError,
    RateLimitError,
)
from app.llm.gateway import GatewayClient, LLMGateway


class FakeClient(BaseChatClient):
    def __init__(self, name: str, *, raises: Exception | None = None, text: str = "ok"):
        self.name = name
        self._raises = raises
        self._text = text
        self.call_count = 0

    async def chat(self, prompt: str, *, prompt_version: str, **kwargs) -> LLMResult:
        self.call_count += 1
        if self._raises:
            raise self._raises
        return LLMResult(
            text=self._text,
            provider=self.name,
            model=f"{self.name}-model",
            input_tokens=1,
            output_tokens=1,
        )


def _gw(*clients: FakeClient) -> tuple[LLMGateway, list[FakeClient]]:
    gws = [
        GatewayClient(name=c.name, client=c, priority=10 + i, available=True)
        for i, c in enumerate(clients)
    ]
    return LLMGateway(clients=gws), list(clients)


@pytest.mark.asyncio
async def test_gateway_uses_first_provider_when_healthy():
    primary = FakeClient("primary")
    backup = FakeClient("backup")
    gw, _ = _gw(primary, backup)

    r = await gw.chat("hi", prompt_version="probe_v1")
    assert r.provider == "primary"
    assert primary.call_count == 1
    assert backup.call_count == 0


@pytest.mark.asyncio
async def test_gateway_falls_through_on_rate_limit():
    primary = FakeClient(
        "primary", raises=RateLimitError("throttled", provider="primary")
    )
    backup = FakeClient("backup")
    gw, _ = _gw(primary, backup)

    r = await gw.chat("hi", prompt_version="probe_v1")
    assert r.provider == "backup"
    assert primary.call_count == 1
    assert backup.call_count == 1


@pytest.mark.asyncio
async def test_gateway_falls_through_quota_then_rate_limit_then_succeeds():
    a = FakeClient("a", raises=QuotaExceededError("quota", provider="a"))
    b = FakeClient("b", raises=RateLimitError("throttled", provider="b"))
    c = FakeClient("c", text="recovered")
    gw, _ = _gw(a, b, c)

    r = await gw.chat("hi", prompt_version="probe_v1")
    assert r.provider == "c"
    assert r.text == "recovered"
    assert a.call_count == b.call_count == c.call_count == 1


@pytest.mark.asyncio
async def test_gateway_auth_error_does_not_fall_through():
    primary = FakeClient(
        "primary", raises=LLMAuthError("not logged in", provider="primary")
    )
    backup = FakeClient("backup")
    gw, _ = _gw(primary, backup)

    with pytest.raises(LLMAuthError):
        await gw.chat("hi", prompt_version="probe_v1")
    assert primary.call_count == 1
    assert backup.call_count == 0  # we did NOT fall through


@pytest.mark.asyncio
async def test_gateway_raises_when_all_fall_through():
    a = FakeClient("a", raises=RateLimitError("a", provider="a"))
    b = FakeClient("b", raises=RateLimitError("b", provider="b"))
    gw, _ = _gw(a, b)

    with pytest.raises(RateLimitError):
        await gw.chat("hi", prompt_version="probe_v1")
    assert a.call_count == b.call_count == 1


@pytest.mark.asyncio
async def test_gateway_prefer_overrides_priority():
    primary = FakeClient("primary")
    backup = FakeClient("backup")
    gw, _ = _gw(primary, backup)

    r = await gw.chat("hi", prompt_version="probe_v1", prefer="backup")
    assert r.provider == "backup"


@pytest.mark.asyncio
async def test_gateway_skip_excludes_provider():
    primary = FakeClient("primary")
    backup = FakeClient("backup")
    gw, _ = _gw(primary, backup)

    r = await gw.chat("hi", prompt_version="probe_v1", skip=["primary"])
    assert r.provider == "backup"
    assert primary.call_count == 0


@pytest.mark.asyncio
async def test_gateway_no_providers_available():
    gws = [
        GatewayClient(name="x", client=FakeClient("x"), priority=10, available=False),
    ]
    gw = LLMGateway(clients=gws)
    with pytest.raises(LLMError):
        await gw.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_gateway_health_reports_availability():
    primary = FakeClient("primary")
    gws = [GatewayClient(name="primary", client=primary, priority=10, available=True)]
    gw = LLMGateway(clients=gws)
    h = await gw.health()
    assert h["primary"]["available"] is True
    assert h["primary"]["priority"] == 10
