from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.llm.base import LLMResult
from app.models import CoverageItem, Project, RequirementItem, TestCase


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
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_prd_analyze_creates_requirement_and_coverage_items(app_client, memory_db):
    upload = await app_client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "Login PRD",
            "markdown": "# Login PRD\n\n## Login\nUsers can log in with valid credentials.",
        },
    )
    assert upload.status_code == 200
    prd_id = upload.json()["data"]["prd_id"]

    analyze = await app_client.post(
        f"/api/prd/{prd_id}/analyze",
        json={"chapter_indices": [0]},
    )

    assert analyze.status_code == 200
    data = analyze.json()["data"]
    assert data["requirements_created"] == 1
    assert data["coverage_created"] == 1

    async with memory_db() as session:
        requirements = (await session.execute(select(RequirementItem))).scalars().all()
        coverage = (await session.execute(select(CoverageItem))).scalars().all()

    assert requirements[0].prd_id == prd_id
    assert "valid credentials" in requirements[0].text
    assert coverage[0].requirement_id == requirements[0].requirement_id
    assert coverage[0].review_status == "proposed"


@pytest.mark.asyncio
async def test_prd_analyze_can_output_chinese_coverage_with_fallback(app_client, memory_db):
    upload = await app_client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "Investment PRD",
            "markdown": (
                "# Investment PRD\n\n"
                "## Investment Flow\n"
                "Investors can review a project and submit an investment order."
            ),
        },
    )
    prd_id = upload.json()["data"]["prd_id"]

    analyze = await app_client.post(
        f"/api/prd/{prd_id}/analyze",
        json={"chapter_indices": [0], "output_language": "zh"},
    )

    assert analyze.status_code == 200
    async with memory_db() as session:
        requirement = (await session.execute(select(RequirementItem))).scalars().one()
        coverage = (await session.execute(select(CoverageItem))).scalars().one()

    assert requirement.text.startswith("投资流程：")
    assert coverage.title.startswith("投资流程：")
    assert "请验证" in coverage.scenario
    assert coverage.rationale == "基于 PRD 证据生成，供评审确认。"


@pytest.mark.asyncio
async def test_prd_analyze_uses_requested_design_provider(app_client, memory_db, monkeypatch):
    class FakeGateway:
        def __init__(self):
            self.calls: list[dict] = []

        async def chat(self, prompt: str, *, prompt_version: str, **kwargs):
            self.calls.append({"prompt": prompt, "prompt_version": prompt_version, **kwargs})
            return LLMResult(
                text=(
                    '{"requirements":[{"text":"Users must log in with MFA.",'
                    '"type":"behavior","evidence":"MFA login","confidence":0.9,'
                    '"coverage":[{"title":"MFA login","scenario":"Complete MFA login.",'
                    '"risk_type":"validation","coverage_type":"happy","priority":"P0",'
                    '"rationale":"critical auth path"}]}]}'
                ),
                provider="codex-cli",
                model="fake",
            )

    fake = FakeGateway()
    monkeypatch.setattr("app.services.test_design_planner.get_gateway", lambda: fake)

    upload = await app_client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "Login PRD",
            "markdown": "# Login PRD\n\n## Login\nMFA login is required.",
        },
    )
    prd_id = upload.json()["data"]["prd_id"]

    analyze = await app_client.post(
        f"/api/prd/{prd_id}/analyze",
        json={"chapter_indices": [0], "prefer_provider": "codex-cli", "output_language": "zh"},
    )

    assert analyze.status_code == 200
    assert fake.calls[0]["prefer"] == "codex-cli"
    assert fake.calls[0]["prompt_version"] == "test_design_v1"
    assert "Output language: Chinese" in fake.calls[0]["prompt"]
    async with memory_db() as session:
        coverage = (await session.execute(select(CoverageItem))).scalars().one()
    assert coverage.title == "MFA login"
    assert coverage.priority == "P0"


@pytest.mark.asyncio
async def test_prd_direct_case_generation_route_is_removed(app_client):
    upload = await app_client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "Login PRD",
            "markdown": "# Login PRD\n\n## Login\nUsers can log in.",
        },
    )
    assert upload.status_code == 200
    prd_id = upload.json()["data"]["prd_id"]

    response = await app_client.post(
        f"/api/prd/{prd_id}/generate",
        json={"chapter_indices": [0]},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_coverage_review_and_draft_case_requires_acceptance(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_test",
            project_id="demo",
            prd_id="prd_test",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_test",
            project_id="demo",
            prd_id="prd_test",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login",
            scenario="User logs in with valid credentials.",
        )
        session.add(req)
        session.add(cov)
        await session.commit()

    blocked = await app_client.post("/api/coverage/cov_test/draft-case", json={})
    assert blocked.status_code == 409

    reviewed = await app_client.post("/api/coverage/cov_test/review", json={"action": "accept"})
    assert reviewed.status_code == 200
    assert reviewed.json()["data"]["review_status"] == "accepted"

    drafted = await app_client.post("/api/coverage/cov_test/draft-case", json={})
    assert drafted.status_code == 201
    case_id = drafted.json()["data"]["case_id"]

    async with memory_db() as session:
        case = await session.get(TestCase, case_id)
        coverage = await session.get(CoverageItem, "cov_test")

    assert case is not None
    assert case.coverage_id == "cov_test"
    assert case.review_status == "pending"
    assert coverage is not None
    assert coverage.linked_case_id == case_id
