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
    await engine.dispose()


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
async def test_get_test_design_provider_default_auto(memory_db):
    assert await rc.get_test_design_provider() is None


@pytest.mark.asyncio
async def test_get_test_design_provider_db_override(session):
    session.add(RuntimeSetting(key="test_design_provider", value="codex-cli"))
    await session.commit()
    assert await rc.get_test_design_provider(session) == "codex-cli"


@pytest.mark.asyncio
async def test_get_test_design_preflight_timeout_default_and_override(memory_db, session):
    assert await rc.get_test_design_preflight_timeout() == 20
    session.add(RuntimeSetting(key="test_design_preflight_timeout_seconds", value="45"))
    await session.commit()
    assert await rc.get_test_design_preflight_timeout(session) == 45


@pytest.mark.asyncio
async def test_get_case_drafting_provider_default_and_override(memory_db, session):
    assert await rc.get_case_drafting_provider() is None
    session.add(RuntimeSetting(key="case_drafting_provider", value="claude-cli"))
    await session.commit()
    assert await rc.get_case_drafting_provider(session) == "claude-cli"


@pytest.mark.asyncio
async def test_get_case_execution_provider_db_override(session):
    session.add(RuntimeSetting(key="case_execution_provider", value="claude-cli"))
    await session.commit()
    assert await rc.get_case_execution_provider(session) == "claude-cli"


@pytest.mark.asyncio
async def test_get_diagnosis_provider_db_override(session):
    session.add(RuntimeSetting(key="diagnosis_provider", value="codex-cli"))
    await session.commit()
    assert await rc.get_diagnosis_provider(session) == "codex-cli"


@pytest.mark.asyncio
async def test_snapshot_includes_all_known_knobs(session):
    snap = await rc.snapshot(session)
    assert "max_concurrent_runs" in snap
    assert "headless" in snap
    assert "executor_loop" in snap
    assert "test_design_provider" in snap
    assert "test_design_preflight_timeout_seconds" in snap
    assert "case_drafting_provider" in snap
    assert "case_generation_provider" not in snap
    assert "case_generation_preflight_timeout_seconds" not in snap
    assert "case_execution_provider" in snap
    assert "diagnosis_provider" in snap
    assert "email_enabled" in snap
    assert "artifact_retention_days" in snap
    assert snap["artifact_retention_days"]["default"] == 30
    assert "smtp_password" in snap
    assert snap["executor_loop"]["choices"] == ["auto", "generic_openai", "claude_cli"]
    assert "codex-cli" in snap["test_design_provider"]["choices"]
    assert "claude-cli" in snap["case_drafting_provider"]["choices"]
    assert snap["test_design_preflight_timeout_seconds"]["value"] == 20
    assert snap["test_design_preflight_timeout_seconds"]["min"] == 5
    assert snap["test_design_preflight_timeout_seconds"]["max"] == 300
    assert snap["case_execution_provider"]["choices"] == [
        "auto",
        "claude-cli",
        "codex-cli",
    ]
    assert snap["diagnosis_provider"]["choices"] == ["auto", "claude-cli", "codex-cli"]
    assert snap["smtp_password"]["value"] == ""
    assert snap["smtp_password"]["is_set"] is False
    for entry in snap.values():
        assert "value" in entry
        assert "default" in entry
        assert "describe" in entry


@pytest.mark.asyncio
async def test_update_many_persists_then_reads_back(session):
    await rc.update_many(
        session,
        {
            "max_concurrent_runs": 9,
            "headless": False,
            "executor_loop": "claude_cli",
            "test_design_provider": "codex-cli",
            "test_design_preflight_timeout_seconds": 45,
            "case_drafting_provider": "claude-cli",
            "case_execution_provider": "claude-cli",
            "diagnosis_provider": "codex-cli",
        },
    )
    assert await rc.get_max_concurrent_runs(session) == 9
    assert await rc.get_headless(session) is False
    assert await rc.get_executor_loop(session) == "claude_cli"
    assert await rc.get_test_design_provider(session) == "codex-cli"
    assert await rc.get_test_design_preflight_timeout(session) == 45
    assert await rc.get_case_drafting_provider(session) == "claude-cli"
    assert await rc.get_case_execution_provider(session) == "claude-cli"
    assert await rc.get_diagnosis_provider(session) == "codex-cli"


@pytest.mark.asyncio
async def test_email_config_and_secret_snapshot(session):
    await rc.update_many(
        session,
        {
            "email_enabled": True,
            "artifact_retention_days": 14,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_password": "secret",
            "smtp_from": "michelle@example.com",
            "smtp_to": "ops@example.com",
            "smtp_use_ssl": True,
            "smtp_use_tls": False,
        },
    )
    cfg = await rc.get_email_config(session)
    assert cfg["enabled"] is True
    assert cfg["host"] == "smtp.example.com"
    assert cfg["password"] == "secret"
    snap = await rc.snapshot(session)
    assert snap["smtp_password"]["value"] == ""
    assert snap["smtp_password"]["is_set"] is True


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
    assert "test_design_provider" in data
    assert "test_design_preflight_timeout_seconds" in data
    assert "case_drafting_provider" in data
    assert "case_generation_provider" not in data
    assert "case_execution_provider" in data
    assert "diagnosis_provider" in data
    assert "email_enabled" in data
    assert "smtp_password" in data


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
            "test_design_provider": "codex-cli",
            "test_design_preflight_timeout_seconds": 60,
            "case_drafting_provider": "claude-cli",
            "case_execution_provider": "claude-cli",
            "diagnosis_provider": "codex-cli",
            "email_enabled": True,
            "artifact_retention_days": 14,
            "smtp_host": "smtp.example.com",
            "smtp_password": "secret",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["max_concurrent_runs"]["value"] == 5
    assert data["headless"]["value"] is False
    assert data["executor_loop"]["value"] == "generic_openai"
    assert data["test_design_provider"]["value"] == "codex-cli"
    assert data["test_design_preflight_timeout_seconds"]["value"] == 60
    assert data["case_drafting_provider"]["value"] == "claude-cli"
    assert data["case_execution_provider"]["value"] == "claude-cli"
    assert data["diagnosis_provider"]["value"] == "codex-cli"
    assert data["email_enabled"]["value"] is True
    assert data["artifact_retention_days"]["value"] == 14
    assert data["smtp_host"]["value"] == "smtp.example.com"
    assert data["smtp_password"]["value"] == ""
    assert data["smtp_password"]["is_set"] is True
