"""Smoke tests for /api/llm/* endpoints — uses TestClient + monkeypatched gateway."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

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


@pytest.mark.asyncio
async def test_llm_runner_status_reports_auto_generic(monkeypatch):
    import app.api.llm as llm_api
    from app.agent.executor import ExecutorStatus
    from app.main import app

    monkeypatch.setattr(llm_api, "build_claude_subprocess_env", lambda **_kw: {})

    async def fake_resolve(_session):
        return ExecutorStatus(
            status="ready",
            configured_loop="auto",
            resolved_loop="generic_openai",
            detail="auto selected generic loop via qwen",
            generic_available=True,
            generic_providers=["qwen"],
            claude_cli_available=True,
            npx_available=True,
        )

    monkeypatch.setattr(llm_api, "resolve_executor_status", fake_resolve)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/llm/runner_status")

    assert r.status_code == 200
    body = r.json()["data"]
    assert body["status"] == "ready"
    assert body["mode"] == "generic_openai"
    assert body["configured_loop"] == "auto"
    assert body["resolved_loop"] == "generic_openai"


@pytest.mark.asyncio
async def test_llm_runner_status_reports_missing_executor(monkeypatch):
    import app.api.llm as llm_api
    from app.agent.executor import ExecutorStatus
    from app.main import app

    monkeypatch.setattr(
        llm_api,
        "build_claude_subprocess_env",
        lambda **_kw: {"ANTHROPIC_BASE_URL": "http://127.0.0.1:4000"},
    )

    async def fake_resolve(_session):
        return ExecutorStatus(
            status="down",
            configured_loop="auto",
            resolved_loop=None,
            detail="no generic provider configured and claude CLI is unavailable",
            generic_available=False,
            generic_providers=[],
            claude_cli_available=False,
            npx_available=True,
        )

    monkeypatch.setattr(llm_api, "resolve_executor_status", fake_resolve)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/llm/runner_status")

    assert r.status_code == 200
    body = r.json()["data"]
    assert body["status"] == "down"
    assert body["mode"] == "unavailable"
