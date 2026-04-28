"""run_lifecycle.heal_stale_runs tests.

Covers the startup/shutdown self-heal that catches Run rows orphaned
by uvicorn --reload, SIGKILL, or any process exit that bypasses the
orchestrator's normal abort path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.models import Run
from app.services.run_lifecycle import heal_stale_runs


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


def _run(run_id: str, status: str, started_at: datetime | None = None) -> Run:
    return Run(
        run_id=run_id,
        trace_id=run_id,
        project_id="p",
        case_id="c",
        case_version=1,
        env="default",
        status=status,
        started_at=started_at,
    )


@pytest.mark.asyncio
async def test_heal_returns_zero_on_clean_db(memory_db):
    assert await heal_stale_runs(reason="anything") == 0


@pytest.mark.asyncio
async def test_heal_marks_running_and_pending_aborted(session):
    started = datetime.now(UTC) - timedelta(minutes=5)
    session.add_all(
        [
            _run("r-running", "running", started),
            _run("r-pending", "pending"),
            _run("r-passed", "passed"),  # should NOT be touched
            _run("r-failed", "failed"),  # should NOT be touched
        ]
    )
    await session.commit()

    healed = await heal_stale_runs(reason="test")
    assert healed == 2

    rows = (await session.execute(select(Run))).scalars().all()
    by_id = {r.run_id: r for r in rows}
    assert by_id["r-running"].status == "aborted"
    assert by_id["r-pending"].status == "aborted"
    assert by_id["r-passed"].status == "passed"
    assert by_id["r-failed"].status == "failed"


@pytest.mark.asyncio
async def test_heal_fills_duration_from_started_at(session):
    started = datetime.now(UTC) - timedelta(seconds=120)
    session.add(_run("r1", "running", started))
    await session.commit()

    await heal_stale_runs(reason="test")

    healed = (await session.execute(select(Run).where(Run.run_id == "r1"))).scalars().first()
    assert healed is not None
    assert healed.ended_at is not None
    assert healed.duration_ms is not None
    # ~120s ± a few ms of wall-clock drift in this test
    assert 110_000 < healed.duration_ms < 130_000


@pytest.mark.asyncio
async def test_heal_reason_prefixed_on_error_message(session):
    session.add(_run("r1", "running"))
    await session.commit()
    await heal_stale_runs(reason="uvicorn reload mid-run")

    healed = (await session.execute(select(Run).where(Run.run_id == "r1"))).scalars().first()
    assert healed is not None
    assert "auto-aborted" in (healed.error_message or "")
    assert "uvicorn reload mid-run" in (healed.error_message or "")
