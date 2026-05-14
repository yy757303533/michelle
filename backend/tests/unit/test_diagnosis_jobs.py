from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import app.db as db_mod
from app.models import Diagnosis, Run, TestCase


@pytest.fixture
async def app_client(monkeypatch):
    from app.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    async with maker() as session:
        session.add(
            TestCase(
                case_id="CASE-JOB",
                project_id="demo",
                name="job case",
                intent="fail",
                module="demo",
                steps=[],
                review_status="approved",
            )
        )
        session.add(
            Run(
                run_id="RUN-JOB",
                trace_id="trace",
                project_id="demo",
                case_id="CASE-JOB",
                status="failed",
                error_message="failed",
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnosis_job_runs_in_background(app_client, monkeypatch) -> None:
    import app.services.diagnosis_jobs as jobs

    client, _maker = app_client

    async def fake_diagnose_run(**kwargs):
        session = kwargs["session"]
        diag = Diagnosis(
            diag_id="DIAG-JOB",
            run_id="RUN-JOB",
            case_id="CASE-JOB",
            diagnoser_prompt_version="test",
            diagnoser_model="test",
            category="real_bug",
            confidence=0.8,
        )
        session.add(diag)
        await session.commit()
        return diag

    monkeypatch.setattr(jobs, "diagnose_run", fake_diagnose_run)

    created = await client.post(
        "/api/diagnosis/by-run/RUN-JOB/jobs",
        json={"overwrite_existing": True, "include_dev_context": True},
    )

    assert created.status_code == 200
    job_id = created.json()["data"]["job_id"]
    fetched = await client.get(f"/api/diagnosis/jobs/{job_id}")

    assert fetched.status_code == 200
    data = fetched.json()["data"]
    assert data["status"] == "done"
    assert data["diag_id"] == "DIAG-JOB"


@pytest.mark.asyncio
async def test_diagnosis_job_reuses_existing_diagnosis_without_background_work(
    app_client, monkeypatch
) -> None:
    import app.api.diagnosis as diagnosis_api

    client, maker = app_client
    async with maker() as session:
        session.add(
            Diagnosis(
                diag_id="DIAG-EXISTING",
                run_id="RUN-JOB",
                case_id="CASE-JOB",
                diagnoser_prompt_version="test",
                diagnoser_model="test",
                category="real_bug",
                confidence=0.7,
            )
        )
        await session.commit()

    async def fail_if_scheduled(**_kwargs):
        raise AssertionError("diagnosis background task should not be scheduled")

    monkeypatch.setattr(diagnosis_api, "run_diagnosis_job", fail_if_scheduled)

    created = await client.post(
        "/api/diagnosis/by-run/RUN-JOB/jobs",
        json={"overwrite_existing": False, "include_dev_context": True},
    )

    assert created.status_code == 200
    data = created.json()["data"]
    assert data["status"] == "done"
    assert data["diag_id"] == "DIAG-EXISTING"
    assert data["error"] == "reused existing diagnosis"
