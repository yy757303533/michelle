from __future__ import annotations

from datetime import UTC, datetime

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


def _case(case_id: str, coverage_id: str) -> TestCase:
    return TestCase(
        case_id=case_id,
        project_id="demo",
        coverage_id=coverage_id,
        name="Valid login",
        intent="User logs in with valid credentials.",
        module="validation",
        tags=["happy"],
        priority="P1",
        preconditions=[],
        steps=[{"intent": "open"}],
        assertions=[{"description": "ok"}],
        quality={},
        source="ai-generated",
        prompt_version="coverage_draft_v1",
        model_version="fake",
        generated_from=f"coverage:{coverage_id}",
        review_status="pending",
    )


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
        json={"chapter_indices": [0], "prefer_provider": "fallback"},
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
        json={
            "chapter_indices": [0],
            "prefer_provider": "fallback",
            "output_language": "zh",
        },
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
async def test_prd_analyze_skips_metadata_only_chapters(app_client, memory_db):
    upload = await app_client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "Investment PRD",
            "markdown": (
                "# Investment PRD\n\n"
                "## Table of Contents\n"
                "1. [Project Overview](#project-overview)\n"
                "2. [Investment Flow](#investment-flow)\n\n"
                "## External Reference Links\n"
                "| Link Type | URL |\n|---|---|\n| Figma Design | https://example.test |\n\n"
                "## Project Responsible Persons\n"
                "| Responsible |\n|---|\n| Viktor |\n| Michelle |\n\n"
                "## Investment Flow\n"
                "Investors can review a project and submit an investment order."
            ),
        },
    )
    prd_id = upload.json()["data"]["prd_id"]

    analyze = await app_client.post(
        f"/api/prd/{prd_id}/analyze",
        json={
            "chapter_indices": [0, 1, 2, 3],
            "prefer_provider": "fallback",
            "output_language": "zh",
        },
    )

    assert analyze.status_code == 200
    assert analyze.json()["data"]["requirements_created"] == 1
    assert analyze.json()["data"]["coverage_created"] == 1
    async with memory_db() as session:
        coverage = (await session.execute(select(CoverageItem))).scalars().one()

    assert coverage.chapter_index == 3
    assert coverage.title.startswith("投资流程：")


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
async def test_prd_analyze_auto_provider_uses_gateway_for_translation(
    app_client, memory_db, monkeypatch
):
    class FakeGateway:
        def __init__(self):
            self.calls: list[dict] = []

        async def chat(self, prompt: str, *, prompt_version: str, **kwargs):
            self.calls.append({"prompt": prompt, "prompt_version": prompt_version, **kwargs})
            return LLMResult(
                text=(
                    '{"requirements":[{"text":"投资者必须能够提交投资订单。",'
                    '"type":"behavior","evidence":"Investors can submit an investment order",'
                    '"confidence":0.9,'
                    '"coverage":[{"title":"提交投资订单","scenario":"验证投资者可以提交投资订单。",'
                    '"risk_type":"business","coverage_type":"happy","priority":"P1",'
                    '"rationale":"覆盖核心投资流程"}]}]}'
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
    assert fake.calls
    assert fake.calls[0]["prefer"] is None
    assert fake.calls[0]["prompt_version"] == "test_design_v1"
    assert "Output language: Chinese" in fake.calls[0]["prompt"]
    async with memory_db() as session:
        requirement = (await session.execute(select(RequirementItem))).scalars().one()
        coverage = (await session.execute(select(CoverageItem))).scalars().one()

    assert requirement.text == "投资者必须能够提交投资订单。"
    assert coverage.title == "提交投资订单"
    assert coverage.scenario == "验证投资者可以提交投资订单。"


@pytest.mark.asyncio
async def test_prd_analyze_falls_back_when_llm_returns_invalid_json(
    app_client, memory_db, monkeypatch
):
    class FakeGateway:
        async def chat(self, prompt: str, *, prompt_version: str, **kwargs):
            return LLMResult(text="", provider="codex-cli", model="fake")

    monkeypatch.setattr("app.services.test_design_planner.get_gateway", lambda: FakeGateway())

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
    assert analyze.json()["data"]["coverage_created"] == 1
    async with memory_db() as session:
        coverage = (await session.execute(select(CoverageItem))).scalars().one()
    assert coverage.title.startswith("投资流程：")


@pytest.mark.asyncio
async def test_prd_analyze_replace_unreviewed_preserves_reviewed_and_drafted(app_client, memory_db):
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
    async with memory_db() as session:
        req = RequirementItem(
            requirement_id="req_existing",
            project_id="demo",
            prd_id=prd_id,
            chapter_index=0,
            text="existing",
        )
        session.add(req)
        session.add(
            CoverageItem(
                coverage_id="cov_replace_proposed",
                project_id="demo",
                prd_id=prd_id,
                requirement_id=req.requirement_id,
                chapter_index=0,
                title="Replace proposed",
                scenario="replace",
                review_status="proposed",
            )
        )
        session.add(
            CoverageItem(
                coverage_id="cov_keep_accepted",
                project_id="demo",
                prd_id=prd_id,
                requirement_id=req.requirement_id,
                chapter_index=0,
                title="Keep accepted",
                scenario="keep",
                review_status="accepted",
            )
        )
        session.add(
            CoverageItem(
                coverage_id="cov_keep_drafted",
                project_id="demo",
                prd_id=prd_id,
                requirement_id=req.requirement_id,
                chapter_index=0,
                title="Keep drafted",
                scenario="keep",
                review_status="proposed",
                linked_case_id="TC-DRAFTED",
            )
        )
        session.add(_case("TC-DRAFTED", "cov_keep_drafted"))
        await session.commit()

    analyze = await app_client.post(
        f"/api/prd/{prd_id}/analyze",
        json={
            "chapter_indices": [0],
            "prefer_provider": "fallback",
            "output_language": "zh",
            "replace_unreviewed": True,
        },
    )

    assert analyze.status_code == 200
    async with memory_db() as session:
        proposed = await session.get(CoverageItem, "cov_replace_proposed")
        accepted = await session.get(CoverageItem, "cov_keep_accepted")
        drafted = await session.get(CoverageItem, "cov_keep_drafted")
        active = (
            (await session.execute(select(CoverageItem).where(CoverageItem.deleted_at.is_(None))))
            .scalars()
            .all()
        )

    assert proposed is not None
    assert proposed.deleted_at is not None
    assert accepted is not None
    assert accepted.deleted_at is None
    assert drafted is not None
    assert drafted.deleted_at is None
    assert any(row.title.startswith("投资流程：") for row in active)


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


@pytest.mark.asyncio
async def test_coverage_review_persists_reviewer_note(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_review_note",
            project_id="demo",
            prd_id="prd_review_note",
            chapter_index=0,
            text="Users can keep their language selection while navigating.",
        )
        cov = CoverageItem(
            coverage_id="cov_review_note",
            project_id="demo",
            prd_id="prd_review_note",
            requirement_id=req.requirement_id,
            chapter_index=0,
            title="Language selection persists across navigation",
            scenario="Switch to French, navigate away, and verify the next page remains French.",
        )
        session.add(req)
        session.add(cov)
        await session.commit()

    reviewed = await app_client.post(
        "/api/coverage/cov_review_note/review",
        json={
            "action": "reject",
            "note": "Scenario should follow PRD language, not hard-code French.",
        },
    )

    assert reviewed.status_code == 200
    data = reviewed.json()["data"]
    assert data["review_status"] == "rejected"
    assert data["review_note"] == "Scenario should follow PRD language, not hard-code French."
    async with memory_db() as session:
        coverage = await session.get(CoverageItem, "cov_review_note")
    assert coverage is not None
    assert coverage.review_note == "Scenario should follow PRD language, not hard-code French."


@pytest.mark.asyncio
async def test_update_coverage_item_edits_reviewable_fields_and_resets_verdict(
    app_client, memory_db
):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_edit_coverage",
            project_id="demo",
            prd_id="prd_edit_coverage",
            chapter_index=9,
            text="Users can keep their language selection while navigating.",
        )
        cov = CoverageItem(
            coverage_id="cov_edit_coverage",
            project_id="demo",
            prd_id="prd_edit_coverage",
            requirement_id=req.requirement_id,
            chapter_index=9,
            risk_type="validation",
            coverage_type="edge",
            title="Cross-page language selection persists",
            scenario="Switch to French, navigate away, and verify the next page remains French.",
            priority="P1",
            review_status="accepted",
            review_note="looks good",
        )
        session.add(req)
        session.add(cov)
        await session.commit()

    edited = await app_client.patch(
        "/api/coverage/cov_edit_coverage",
        json={
            "title": "跨页面导航后语言选择保持",
            "scenario": "切换为中文后导航到其他页面，验证新页面仍显示中文。",
            "risk_type": "validation",
            "coverage_type": "happy",
            "priority": "P0",
            "rationale": "Reviewer corrected the language expectation.",
        },
    )

    assert edited.status_code == 200
    data = edited.json()["data"]
    assert data["title"] == "跨页面导航后语言选择保持"
    assert data["scenario"] == "切换为中文后导航到其他页面，验证新页面仍显示中文。"
    assert data["coverage_type"] == "happy"
    assert data["priority"] == "P0"
    assert data["rationale"] == "Reviewer corrected the language expectation."
    assert data["review_status"] == "proposed"
    assert data["review_note"] == ""


@pytest.mark.asyncio
async def test_update_coverage_item_rejects_active_linked_case(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_edit_linked",
            project_id="demo",
            prd_id="prd_edit_linked",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_edit_linked",
            project_id="demo",
            prd_id="prd_edit_linked",
            requirement_id=req.requirement_id,
            chapter_index=0,
            title="Valid login",
            scenario="User logs in with valid credentials.",
            linked_case_id="case_edit_linked",
        )
        session.add(req)
        session.add(cov)
        session.add(_case("case_edit_linked", "cov_edit_linked"))
        await session.commit()

    edited = await app_client.patch(
        "/api/coverage/cov_edit_linked",
        json={"title": "Updated title"},
    )

    assert edited.status_code == 409
    assert "linked case" in edited.json()["detail"]


@pytest.mark.asyncio
async def test_delete_coverage_soft_deletes_unlinked_item(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_delete",
            project_id="demo",
            prd_id="prd_delete",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_delete",
            project_id="demo",
            prd_id="prd_delete",
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

    response = await app_client.delete("/api/coverage/cov_delete")

    assert response.status_code == 204
    async with memory_db() as session:
        coverage = await session.get(CoverageItem, "cov_delete")
    assert coverage is not None
    assert coverage.deleted_at is not None

    listed = await app_client.get("/api/coverage/?project_id=demo")
    assert listed.status_code == 200
    assert listed.json()["data"] == []

    deleted = await app_client.get("/api/coverage/?project_id=demo&deleted=deleted")
    assert deleted.status_code == 200
    assert [row["coverage_id"] for row in deleted.json()["data"]] == ["cov_delete"]


@pytest.mark.asyncio
async def test_restore_coverage_reactivates_soft_deleted_item(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_restore",
            project_id="demo",
            prd_id="prd_restore",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_restore",
            project_id="demo",
            prd_id="prd_restore",
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

    deleted = await app_client.delete("/api/coverage/cov_restore")
    assert deleted.status_code == 204

    restored = await app_client.post("/api/coverage/cov_restore/restore")

    assert restored.status_code == 200
    assert restored.json()["data"]["deleted_at"] is None
    listed = await app_client.get("/api/coverage/?project_id=demo")
    assert [row["coverage_id"] for row in listed.json()["data"]] == ["cov_restore"]


@pytest.mark.asyncio
async def test_delete_coverage_rejects_linked_item(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_linked",
            project_id="demo",
            prd_id="prd_linked",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_linked",
            project_id="demo",
            prd_id="prd_linked",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login",
            scenario="User logs in with valid credentials.",
            linked_case_id="case_linked",
        )
        session.add(req)
        session.add(cov)
        session.add(_case("case_linked", "cov_linked"))
        await session.commit()

    response = await app_client.delete("/api/coverage/cov_linked")

    assert response.status_code == 409
    assert "linked case" in response.json()["detail"]
    async with memory_db() as session:
        coverage = await session.get(CoverageItem, "cov_linked")
    assert coverage is not None


@pytest.mark.asyncio
async def test_delete_coverage_allows_stale_linked_case_id(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_stale_link",
            project_id="demo",
            prd_id="prd_stale_link",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_stale_link",
            project_id="demo",
            prd_id="prd_stale_link",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login",
            scenario="User logs in with valid credentials.",
            linked_case_id="TC-MISSING",
        )
        session.add(req)
        session.add(cov)
        await session.commit()

    response = await app_client.delete("/api/coverage/cov_stale_link")

    assert response.status_code == 204
    async with memory_db() as session:
        coverage = await session.get(CoverageItem, "cov_stale_link")
    assert coverage is not None
    assert coverage.deleted_at is not None
    assert coverage.linked_case_id is None


@pytest.mark.asyncio
async def test_list_coverage_hides_stale_linked_case_id(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_list_stale_link",
            project_id="demo",
            prd_id="prd_list_stale_link",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_list_stale_link",
            project_id="demo",
            prd_id="prd_list_stale_link",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login",
            scenario="User logs in with valid credentials.",
            linked_case_id="TC-MISSING",
        )
        session.add(req)
        session.add(cov)
        await session.commit()

    response = await app_client.get("/api/coverage/?project_id=demo")

    assert response.status_code == 200
    assert response.json()["data"][0]["linked_case_id"] is None


@pytest.mark.asyncio
async def test_delete_coverage_allows_soft_deleted_linked_case(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_deleted_link",
            project_id="demo",
            prd_id="prd_deleted_link",
            chapter_index=0,
            text="Users can log in.",
        )
        cov = CoverageItem(
            coverage_id="cov_deleted_link",
            project_id="demo",
            prd_id="prd_deleted_link",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login",
            scenario="User logs in with valid credentials.",
            linked_case_id="TC-DELETED",
        )
        case = TestCase(
            case_id="TC-DELETED",
            project_id="demo",
            coverage_id="cov_deleted_link",
            name="Valid login",
            intent="User logs in with valid credentials.",
            module="validation",
            tags=["happy"],
            priority="P1",
            preconditions=[],
            steps=[{"intent": "open"}],
            assertions=[{"description": "ok"}],
            quality={},
            source="ai-generated",
            prompt_version="coverage_draft_v1",
            model_version="fake",
            generated_from="coverage:cov_deleted_link",
            review_status="pending",
            deleted_at=datetime.now(UTC),
        )
        session.add(req)
        session.add(cov)
        session.add(case)
        await session.commit()

    response = await app_client.delete("/api/coverage/cov_deleted_link")

    assert response.status_code == 204
    async with memory_db() as session:
        coverage = await session.get(CoverageItem, "cov_deleted_link")
    assert coverage is not None
    assert coverage.deleted_at is not None
    assert coverage.linked_case_id is None


@pytest.mark.asyncio
async def test_bulk_delete_coverage_soft_deletes_unlinked_and_skips_linked(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        req = RequirementItem(
            requirement_id="req_bulk",
            project_id="demo",
            prd_id="prd_bulk",
            chapter_index=0,
            text="Users can log in.",
        )
        session.add(req)
        session.add(
            CoverageItem(
                coverage_id="cov_bulk_delete",
                project_id="demo",
                prd_id="prd_bulk",
                requirement_id=req.requirement_id,
                chapter_index=0,
                risk_type="validation",
                coverage_type="happy",
                title="Delete me",
                scenario="User logs in with valid credentials.",
            )
        )
        session.add(
            CoverageItem(
                coverage_id="cov_bulk_linked",
                project_id="demo",
                prd_id="prd_bulk",
                requirement_id=req.requirement_id,
                chapter_index=1,
                risk_type="validation",
                coverage_type="happy",
                title="Keep me",
                scenario="User logs in with valid credentials.",
                linked_case_id="TC-LINKED",
            )
        )
        session.add(_case("TC-LINKED", "cov_bulk_linked"))
        await session.commit()

    response = await app_client.post(
        "/api/coverage/bulk-delete",
        json={"coverage_ids": ["cov_bulk_delete", "cov_bulk_linked", "cov_missing"]},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["deleted"] == ["cov_bulk_delete"]
    assert data["skipped_linked"] == ["cov_bulk_linked"]
    assert data["missing"] == ["cov_missing"]
    async with memory_db() as session:
        deleted = await session.get(CoverageItem, "cov_bulk_delete")
        linked = await session.get(CoverageItem, "cov_bulk_linked")
    assert deleted is not None
    assert deleted.deleted_at is not None
    assert linked is not None
    assert linked.deleted_at is None
