from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CoverageItem, Diagnosis, RegressionAsset, RequirementItem, Run, TestCase


async def test_requirement_coverage_asset_models_persist() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine) as session:
        req = RequirementItem(
            requirement_id="req_abc123",
            project_id="demo",
            prd_id="prd_1",
            chapter_index=0,
            chapter_hash="hash1",
            text="Users can log in with valid credentials.",
            type="behavior",
            evidence="Login section",
            confidence=0.91,
        )
        cov = CoverageItem(
            coverage_id="cov_abc123",
            project_id="demo",
            prd_id="prd_1",
            requirement_id=req.requirement_id,
            chapter_index=0,
            risk_type="validation",
            coverage_type="happy",
            title="Valid login succeeds",
            scenario="User submits valid username and password.",
            rationale="Core authentication path.",
            priority="P0",
            review_status="accepted",
        )
        case = TestCase(
            case_id="TC-20260512-001",
            project_id="demo",
            coverage_id=cov.coverage_id,
            name="Valid login",
            intent="Verify valid login succeeds",
            steps=[{"intent": "Open login page"}],
            assertions=[{"description": "User reaches dashboard"}],
        )
        run = Run(
            run_id="run_1",
            trace_id="trace_1",
            project_id="demo",
            case_id=case.case_id,
            case_version=1,
            execution_mode="agentic",
            status="passed",
        )
        asset = RegressionAsset(
            asset_id="asset_abc123",
            project_id="demo",
            case_id=case.case_id,
            case_version=1,
            source_run_id=run.run_id,
            status="approved",
            action_plan=[{"action": "navigate", "url": "/login"}],
            locator_candidates=[{"step": 1, "locators": ["getByRole('button')"]}],
            assertions=[{"description": "User reaches dashboard"}],
        )
        diag = Diagnosis(
            diag_id="diag_abc123",
            run_id=run.run_id,
            case_id=case.case_id,
            asset_id=asset.asset_id,
            diagnoser_prompt_version="diagnose_v1",
            diagnoser_model="fake",
            category="selector_drift",
            feedback_target="asset",
        )
        session.add(req)
        session.add(cov)
        session.add(case)
        session.add(run)
        session.add(asset)
        session.add(diag)
        coverage_id = cov.coverage_id
        case_id = case.case_id
        asset_id = asset.asset_id
        diag_id = diag.diag_id
        await session.commit()

        saved_cov = (
            await session.exec(select(CoverageItem).where(CoverageItem.coverage_id == coverage_id))
        ).one()
        saved_case = await session.get(TestCase, case_id)
        saved_asset = await session.get(RegressionAsset, asset_id)
        saved_diag = await session.get(Diagnosis, diag_id)

    assert saved_cov.review_status == "accepted"
    assert saved_case is not None
    assert saved_case.coverage_id == coverage_id
    assert saved_asset is not None
    assert saved_asset.status == "approved"
    assert saved_diag is not None
    assert saved_diag.feedback_target == "asset"
