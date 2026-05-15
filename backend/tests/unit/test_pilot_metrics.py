from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import CoverageItem, Diagnosis, Project, RegressionAsset, Run, TestCase


@pytest.fixture
async def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.db as db_mod

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    yield maker
    await engine.dispose()


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_pilot_metrics_reports_review_execution_asset_and_diagnosis_rates(
    memory_db, app_client
):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add_all(
            [
                CoverageItem(
                    coverage_id="cov-accepted",
                    project_id="demo",
                    prd_id="prd",
                    requirement_id="req",
                    chapter_index=0,
                    title="accepted",
                    scenario="accepted",
                    review_status="accepted",
                ),
                CoverageItem(
                    coverage_id="cov-rejected",
                    project_id="demo",
                    prd_id="prd",
                    requirement_id="req",
                    chapter_index=0,
                    title="rejected",
                    scenario="rejected",
                    review_status="rejected",
                ),
            ]
        )
        session.add_all(
            [
                TestCase(
                    case_id="TC-PASS",
                    project_id="demo",
                    name="Pass",
                    intent="pass",
                    review_status="approved",
                ),
                TestCase(
                    case_id="TC-FAIL",
                    project_id="demo",
                    name="Fail",
                    intent="fail",
                    review_status="rejected",
                ),
            ]
        )
        session.add_all(
            [
                Run(
                    run_id="run-agentic-pass",
                    trace_id="trace-1",
                    project_id="demo",
                    case_id="TC-PASS",
                    execution_mode="agentic",
                    status="passed",
                    duration_ms=10_000,
                ),
                Run(
                    run_id="run-agentic-fail",
                    trace_id="trace-2",
                    project_id="demo",
                    case_id="TC-FAIL",
                    execution_mode="agentic",
                    status="failed",
                    duration_ms=20_000,
                ),
                Run(
                    run_id="run-replay-pass",
                    trace_id="trace-3",
                    project_id="demo",
                    case_id="TC-PASS",
                    asset_id="asset-approved",
                    execution_mode="replay",
                    status="passed",
                    duration_ms=1_000,
                ),
            ]
        )
        session.add(
            RegressionAsset(
                asset_id="asset-approved",
                project_id="demo",
                case_id="TC-PASS",
                source_run_id="run-agentic-pass",
                status="approved",
            )
        )
        session.add(
            Diagnosis(
                diag_id="diag-confirmed",
                run_id="run-agentic-fail",
                case_id="TC-FAIL",
                diagnoser_prompt_version="diagnose_v1",
                diagnoser_model="fake",
                category="real_bug",
                human_feedback="confirmed",
                feedback_target="coverage",
                feedback_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = await app_client.get("/api/pilot/metrics?project_id=demo")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["coverage"]["acceptance_rate"] == 0.5
    assert data["cases"]["approval_rate"] == 0.5
    assert data["execution"]["first_agentic_pass_rate"] == 0.5
    assert data["assets"]["asset_extraction_rate"] == 1.0
    assert data["assets"]["asset_approval_rate"] == 1.0
    assert data["replay"]["replay_pass_rate"] == 1.0
    assert data["replay"]["speedup_ratio"] == 15.0
    assert data["diagnosis"]["confirmation_rate"] == 1.0
    assert data["diagnosis"]["feedback_routing_distribution"] == {"coverage": 1}
