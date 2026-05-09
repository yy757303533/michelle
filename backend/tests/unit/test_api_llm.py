"""Smoke tests for /api/llm/* endpoints — uses TestClient + monkeypatched gateway."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel

import app.db as db_mod
from app.llm.gateway import GatewayClient, LLMGateway
from app.models import LLMCall
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
async def test_llm_probe_with_prefer_only_tests_requested_provider():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/llm/probe", json={"prefer": "backup"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["ok"] is True
    assert body["data"]["provider"] == "backup"


@pytest.mark.asyncio
async def test_llm_probe_with_unavailable_prefer_does_not_fallback():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/llm/probe", json={"prefer": "missing"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["ok"] is False
    assert body["data"]["provider"] == "missing"
    assert body["data"]["error_type"] == "ProviderUnavailable"


@pytest.mark.asyncio
async def test_llm_probe_all_checks_each_available_provider():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/llm/probe_all", json={})
    assert r.status_code == 200
    body = r.json()["data"]
    assert [row["provider"] for row in body] == ["primary", "backup"]
    assert all(row["ok"] for row in body)


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
            detail="auto selected generic loop via codex-cli",
            generic_available=True,
            generic_providers=["codex-cli"],
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


@pytest.mark.asyncio
async def test_clear_llm_metrics_deletes_history():
    from app.main import app

    async with db_mod.engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with db_mod.async_session_maker() as session:
        session.add(
            LLMCall(
                provider="codex-cli",
                model="m",
                prompt_version="p",
                ok=False,
                error_type="QuotaExceededError",
                error_message="insufficient balance",
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        before = await ac.get("/api/llm/metrics")
        r = await ac.delete("/api/llm/metrics")
        after = await ac.get("/api/llm/metrics")

    assert before.json()["data"]["totals"]["calls"] == 1
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == 1
    assert after.json()["data"]["totals"]["calls"] == 0
