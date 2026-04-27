"""Smoke tests for /api/llm/* endpoints — uses TestClient + monkeypatched gateway."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.llm.base import LLMResult
from app.llm.gateway import GatewayClient, LLMGateway
from tests.unit.test_llm_gateway import FakeClient


@pytest.fixture(autouse=True)
def _override_gateway(monkeypatch):
    primary = FakeClient("primary")
    backup = FakeClient("backup")
    gw = LLMGateway(
        clients=[
            GatewayClient(name="primary", client=primary, priority=10, available=True),
            GatewayClient(name="backup", client=backup, priority=20, available=True),
        ]
    )
    import app.llm.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "_gateway", gw)
    yield


@pytest.mark.asyncio
async def test_llm_health_endpoint_lists_providers():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/llm/health")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "primary" in body["data"]
    assert body["data"]["primary"]["available"] is True
    assert body["available_providers"] == ["primary", "backup"]


@pytest.mark.asyncio
async def test_llm_probe_endpoint_returns_result():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/llm/probe", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["ok"] is True
    assert body["data"]["provider"] == "primary"
    assert body["data"]["text"] == "ok"
