"""Smoke test: the app boots and /healthz returns 200."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "providers" in body
    assert "claude-cli" in body["providers"]
    assert "codex-cli" in body["providers"]


@pytest.mark.asyncio
async def test_healthz_request_id_header():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert "x-request-id" in {k.lower() for k in r.headers}


@pytest.mark.asyncio
async def test_admin_token_blocks_admin_api_without_header(monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "admin_token", "secret")
    monkeypatch.setattr(settings, "app_env", "dev")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/settings/runtime")
    assert r.status_code == 401


def test_database_summary_redacts_password(monkeypatch):
    import app.db as db_mod
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+asyncpg://michelle:secret@db.internal:5432/michelle",
    )

    summary = db_mod.database_summary()

    assert "secret" not in summary["url"]
