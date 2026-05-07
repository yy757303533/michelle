"""runtime_config + /api/settings/runtime tests.

Verifies the read/write boundary between the env-bootstrap defaults and
the DB-backed runtime overrides, plus the HTTP shape."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import app.db as db_mod
import app.runtime_config as rc
from app.models import RuntimeSetting


@pytest.fixture
async def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    yield maker


@pytest.fixture
async def session(memory_db) -> AsyncSession:
    async with memory_db() as s:
        yield s


@pytest.mark.asyncio
async def test_get_max_concurrent_runs_falls_back_to_env(memory_db, monkeypatch):
    """No row in DB → returns the env default."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "max_concurrent_runs", 7)
    assert await rc.get_max_concurrent_runs() == 7


@pytest.mark.asyncio
async def test_get_max_concurrent_runs_uses_db_when_present(session):
    session.add(RuntimeSetting(key="max_concurrent_runs", value="5"))
    await session.commit()
    assert await rc.get_max_concurrent_runs() == 5


@pytest.mark.asyncio
async def test_get_headless_default_true(memory_db):
    """No row → bootstrap default is True (Chromium hidden)."""
    assert await rc.get_headless() is True


@pytest.mark.asyncio
async def test_get_headless_db_override(session):
    session.add(RuntimeSetting(key="headless", value="false"))
    await session.commit()
    assert await rc.get_headless() is False


@pytest.mark.asyncio
async def test_get_executor_loop_default_auto(memory_db):
    assert await rc.get_executor_loop() == "auto"


@pytest.mark.asyncio
async def test_get_executor_loop_db_override(session):
    session.add(RuntimeSetting(key="executor_loop", value="generic_openai"))
    await session.commit()
    assert await rc.get_executor_loop() == "generic_openai"


@pytest.mark.asyncio
async def test_snapshot_includes_all_known_knobs(session):
    snap = await rc.snapshot(session)
    assert "max_concurrent_runs" in snap
    assert "headless" in snap
    assert "executor_loop" in snap
    assert snap["executor_loop"]["choices"] == ["auto", "generic_openai", "claude_cli"]
    for entry in snap.values():
        assert "value" in entry
        assert "default" in entry
        assert "describe" in entry


@pytest.mark.asyncio
async def test_update_many_persists_then_reads_back(session):
    await rc.update_many(
        session,
        {"max_concurrent_runs": 9, "headless": False, "executor_loop": "claude_cli"},
    )
    assert await rc.get_max_concurrent_runs(session) == 9
    assert await rc.get_headless(session) is False
    assert await rc.get_executor_loop(session) == "claude_cli"


@pytest.mark.asyncio
async def test_update_many_drops_unknown_keys(session):
    """Defense in depth: even if Pydantic somehow lets a stray key through,
    runtime_config silently drops anything not in the whitelist."""
    await rc.update_many(session, {"definitely_not_a_knob": 42})
    snap = await rc.snapshot(session)
    assert "definitely_not_a_knob" not in snap


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_settings_endpoint(app_client):
    r = await app_client.get("/api/settings/runtime")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "max_concurrent_runs" in data
    assert "headless" in data
    assert "executor_loop" in data


@pytest.mark.asyncio
async def test_put_settings_endpoint_validates(app_client):
    """422 on out-of-bounds (Pydantic Field constraint)."""
    r = await app_client.put("/api/settings/runtime", json={"max_concurrent_runs": 999})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_endpoint_empty_body(app_client):
    """422 when nothing to update — prevents accidental no-op writes."""
    r = await app_client.put("/api/settings/runtime", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_endpoint_round_trip(app_client):
    r = await app_client.put(
        "/api/settings/runtime",
        json={
            "max_concurrent_runs": 5,
            "headless": False,
            "executor_loop": "generic_openai",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["max_concurrent_runs"]["value"] == 5
    assert data["headless"]["value"] is False
    assert data["executor_loop"]["value"] == "generic_openai"
