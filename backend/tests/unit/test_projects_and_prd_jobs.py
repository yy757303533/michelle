"""Project CRUD, PRD delete/cascading, and retired PRD generation tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.models import (
    PRD,
    CoverageItem,
    DesignGenerationJob,
    Diagnosis,
    Project,
    ProjectMember,
    RegressionAsset,
    RequirementItem,
    Run,
    StepEvent,
    TestCase,
)


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


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_project_without_id_auto_mints(app_client):
    r = await app_client.post(
        "/api/projects/",
        json={"name": "Demo", "base_url": "http://localhost:5000/"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["project_id"].startswith("p_")


@pytest.mark.asyncio
async def test_post_with_known_id_updates_existing(app_client, session):
    session.add(Project(project_id="demo", name="Old", base_url="http://old/"))
    await session.commit()

    r = await app_client.post(
        "/api/projects/",
        json={
            "project_id": "demo",
            "name": "New",
            "base_url": "http://new/",
            "login_url": "http://new/login",
            "default_username": "admin",
            "default_password": "p",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "New"
    assert data["base_url"] == "http://new/"
    assert data["default_password"] == ""
    assert data["default_password_is_set"] is True


@pytest.mark.asyncio
async def test_delete_project_removes_owned_design_spine_data(app_client, session):
    session.add(Project(project_id="tmp", name="Temporary"))
    session.add(ProjectMember(project_id="tmp", user_id="u1", role="admin"))
    session.add(
        PRD(
            prd_id="prd-tmp",
            project_id="tmp",
            name="Spec",
            raw_markdown="# Spec",
            content_hash="hash",
        )
    )
    session.add(
        DesignGenerationJob(
            job_id="job-tmp",
            prd_id="prd-tmp",
            project_id="tmp",
            total_chapters=1,
        )
    )
    session.add(
        RequirementItem(
            requirement_id="req-tmp",
            project_id="tmp",
            prd_id="prd-tmp",
            chapter_index=0,
            text="requirement",
        )
    )
    session.add(
        CoverageItem(
            coverage_id="cov-tmp",
            project_id="tmp",
            prd_id="prd-tmp",
            requirement_id="req-tmp",
            chapter_index=0,
            title="coverage",
            scenario="scenario",
        )
    )
    session.add(
        TestCase(
            case_id="TC-TMP",
            project_id="tmp",
            coverage_id="cov-tmp",
            name="case",
            intent="intent",
            module="module",
        )
    )
    session.add(
        Run(
            run_id="run-tmp",
            trace_id="trace-tmp",
            project_id="tmp",
            case_id="TC-TMP",
            asset_id="asset-tmp",
            status="failed",
        )
    )
    session.add(StepEvent(run_id="run-tmp", step_index=0, event="agent.step.executed"))
    session.add(
        RegressionAsset(
            asset_id="asset-tmp",
            project_id="tmp",
            case_id="TC-TMP",
            source_run_id="run-tmp",
        )
    )
    session.add(
        Diagnosis(
            diag_id="diag-tmp",
            run_id="run-tmp",
            case_id="TC-TMP",
            asset_id="asset-tmp",
            diagnoser_prompt_version="v1",
            diagnoser_model="test",
            category="unknown",
        )
    )
    await session.commit()

    r = await app_client.delete("/api/projects/tmp")

    assert r.status_code == 204
    for model in (
        Project,
        ProjectMember,
        PRD,
        DesignGenerationJob,
        RequirementItem,
        CoverageItem,
        TestCase,
        Run,
        StepEvent,
        RegressionAsset,
        Diagnosis,
    ):
        assert (await session.execute(select(model))).scalars().all() == []


@pytest.mark.asyncio
async def test_prd_get_returns_raw_markdown_and_chapter_body(app_client, session):
    session.add(Project(project_id="demo", name="demo"))
    session.add(
        PRD(
            prd_id="p1",
            project_id="demo",
            name="Spec",
            raw_markdown="# Spec\n\n## Registration\n\nEmail verification required.",
            content_hash="hash",
            chapters=[
                {
                    "position": 0,
                    "level": 2,
                    "title": "Registration",
                    "normalized_title": "registration",
                    "hash": "abc123",
                    "body": "Email verification required.",
                }
            ],
        )
    )
    await session.commit()

    r = await app_client.get("/api/prd/p1")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["raw_markdown"] == "# Spec\n\n## Registration\n\nEmail verification required."
    assert data["chapters"][0]["body"] == "Email verification required."


@pytest.mark.asyncio
async def test_get_design_job_endpoint_returns_status(app_client, session):
    session.add(
        DesignGenerationJob(
            job_id="gen_x",
            prd_id="p",
            project_id="demo",
            status="running",
            total_chapters=5,
            completed_chapters=2,
            requirements_created=2,
            coverage_created=2,
            results=[
                {"chapter_index": 0, "coverage_created": 1, "skipped": False},
                {"chapter_index": 1, "coverage_created": 1, "skipped": False},
            ],
            request_payload={},
        )
    )
    await session.commit()

    r = await app_client.get("/api/prd/jobs/gen_x")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "running"
    assert data["completed_chapters"] == 2
    assert data["coverage_created"] == 2
    assert len(data["results"]) == 2
