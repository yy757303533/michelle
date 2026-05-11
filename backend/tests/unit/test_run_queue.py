"""Run queue, trends, and cancel API tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import Project, Run, StepEvent, TestCase


@pytest.fixture
async def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.db as db_mod
    import app.services.run_orchestrator as ro

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    monkeypatch.setattr(ro, "async_session_maker", maker)
    monkeypatch.setattr(ro, "_RUN_TASKS", {})
    yield maker
    await engine.dispose()


@pytest.fixture
async def session(memory_db):
    async with memory_db() as s:
        yield s


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _run(run_id: str, status: str, project_id: str = "demo") -> Run:
    return Run(
        run_id=run_id,
        trace_id=f"trace-{run_id}",
        project_id=project_id,
        case_id=f"TC-{run_id}",
        case_version=1,
        env="test",
        status=status,
        duration_ms=1000 if status not in {"pending", "running"} else None,
    )


def _case(case_id: str, project_id: str = "demo") -> TestCase:
    return TestCase(
        case_id=case_id,
        project_id=project_id,
        name=case_id,
        intent=case_id,
        review_status="approved",
        steps=[{"intent": "open page"}],
        assertions=[{"description": "page opens"}],
    )


@pytest.mark.asyncio
async def test_list_runs_hides_runs_for_deleted_cases(session, app_client):
    session.add(Project(project_id="demo", name="Demo"))
    session.add(_case("TC-r1"))
    session.add(_run("r1", "passed"))
    session.add(_run("orphan", "aborted"))
    await session.commit()

    r = await app_client.get("/api/runs/?project_id=demo")

    assert r.status_code == 200
    body = r.json()
    assert [row["run_id"] for row in body["data"]] == ["r1"]
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_queue_lists_pending_and_running(session, app_client):
    session.add(Project(project_id="demo", name="Demo"))
    session.add(_case("TC-r1"))
    session.add(_case("TC-r2"))
    session.add(_run("r1", "pending"))
    session.add(_run("r2", "running"))
    session.add(_run("r3", "passed"))
    await session.commit()

    r = await app_client.get("/api/runs/queue?project_id=demo")

    assert r.status_code == 200
    body = r.json()
    assert [row["run_id"] for row in body["data"]] == ["r1", "r2"]
    assert body["data"][0]["queue_position"] == 1
    assert body["data"][0]["cancelable"] is True


@pytest.mark.asyncio
async def test_cancel_rolls_back_run_scope(session, app_client):
    session.add(Project(project_id="demo", name="Demo"))
    session.add(_run("r1", "pending"))
    session.add(StepEvent(run_id="r1", step_index=0, event="x"))
    await session.commit()

    r = await app_client.post("/api/runs/r1/cancel")

    assert r.status_code == 200
    assert r.json()["data"]["rolled_back"] is True
    row = await session.get(Run, "r1")
    assert row is None


@pytest.mark.asyncio
async def test_trends_returns_rollups(session, app_client):
    session.add(Project(project_id="demo", name="Demo"))
    session.add(_case("TC-r1"))
    session.add(_case("TC-r2"))
    session.add(_case("TC-r3"))
    session.add(_run("r1", "passed"))
    session.add(_run("r2", "failed"))
    session.add(_run("r3", "flaky"))
    await session.commit()

    r = await app_client.get("/api/runs/trends?project_id=demo")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 3
    assert data["by_status"]["passed"] == 1
    assert data["pass_rate"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_create_run_rejects_unapproved_case(session, app_client, monkeypatch):
    session.add(Project(project_id="demo", name="Demo"))
    session.add(
        TestCase(
            case_id="TC-PENDING",
            project_id="demo",
            name="pending case",
            intent="pending case",
            review_status="pending",
            steps=[{"intent": "open page"}],
            assertions=[{"description": "page opens"}],
        )
    )
    await session.commit()

    kicked: list[str] = []

    import app.api.runs as runs_api

    monkeypatch.setattr(runs_api, "kick_off", lambda **kw: kicked.append(kw["run_id"]))

    r = await app_client.post("/api/runs/", json={"case_ids": ["TC-PENDING"]})

    assert r.status_code == 409
    assert "approved" in r.text.lower()
    assert kicked == []
