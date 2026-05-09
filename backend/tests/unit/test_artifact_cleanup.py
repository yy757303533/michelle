from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import Run
from app.services.artifact_cleanup import cleanup_artifacts


@pytest.fixture
async def db(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from app import storage

    monkeypatch.setattr(storage, "artifacts_root", lambda: tmp_path)
    async with maker() as s:
        yield s, tmp_path
    await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_artifacts_dry_run_reports_old_terminal_runs(db):
    session, root = db
    old = datetime.now(UTC) - timedelta(days=40)
    session.add(
        Run(
            run_id="r-old",
            trace_id="t",
            project_id="demo",
            case_id="c",
            case_version=1,
            env="x",
            status="failed",
            created_at=old,
            ended_at=old,
            artifacts_dir=str(root / "demo" / "r-old"),
        )
    )
    rd = root / "demo" / "r-old"
    rd.mkdir(parents=True)
    (rd / "trace.jsonl").write_text("x" * 10, encoding="utf-8")
    await session.commit()

    result = await cleanup_artifacts(session=session, retention_days=30, dry_run=True)

    assert len(result.candidates) == 1
    assert result.candidates[0].run_id == "r-old"
    assert result.candidates[0].bytes == 10
    assert rd.exists()


@pytest.mark.asyncio
async def test_cleanup_artifacts_deletes_old_terminal_and_skips_running(db):
    session, root = db
    old = datetime.now(UTC) - timedelta(days=40)
    for run_id, status in [("r-old", "passed"), ("r-running", "running")]:
        session.add(
            Run(
                run_id=run_id,
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status=status,
                created_at=old,
                ended_at=old if status != "running" else None,
                artifacts_dir=str(root / "demo" / run_id),
            )
        )
        rd = root / "demo" / run_id
        rd.mkdir(parents=True)
        (rd / "trace.jsonl").write_text("x" * 10, encoding="utf-8")
    await session.commit()

    result = await cleanup_artifacts(session=session, retention_days=30, dry_run=False)

    assert result.deleted_runs == 1
    assert not (root / "demo" / "r-old").exists()
    assert (root / "demo" / "r-running").exists()
