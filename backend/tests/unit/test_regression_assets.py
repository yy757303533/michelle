from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.models import Diagnosis, Project, RegressionAsset, Run, StepEvent, TestCase


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


async def _seed_passed_run(memory_db) -> str:
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add(
            TestCase(
                case_id="TC-20260512-001",
                project_id="demo",
                name="Login works",
                intent="User logs in",
                module="auth",
                steps=[{"intent": "open login"}, {"intent": "submit credentials"}],
                assertions=[{"description": "dashboard appears"}],
                review_status="approved",
                version=3,
            )
        )
        session.add(
            Run(
                run_id="run_passed",
                trace_id="trace_passed",
                project_id="demo",
                case_id="TC-20260512-001",
                case_version=3,
                status="passed",
                execution_mode="agentic",
            )
        )
        session.add(
            StepEvent(
                run_id="run_passed",
                step_index=0,
                event="agent.step.executed",
                intent="open login",
                tool_name="browser_navigate",
                tool_args={"url": "https://example.test/login"},
                tool_result={"ok": True},
                status="ok",
            )
        )
        session.add(
            StepEvent(
                run_id="run_passed",
                step_index=1,
                event="agent.step.executed",
                intent="submit credentials",
                tool_name="browser_click",
                tool_args={"selector": "button[type=submit]"},
                tool_result={"ok": True},
                status="ok",
            )
        )
        await session.commit()
    return "run_passed"


@pytest.mark.asyncio
async def test_extract_approve_and_replay_regression_asset(app_client, memory_db, monkeypatch):
    run_id = await _seed_passed_run(memory_db)

    extracted = await app_client.post(f"/api/regression-assets/from-run/{run_id}")
    assert extracted.status_code == 201
    asset = extracted.json()["data"]
    assert asset["status"] == "draft"
    assert asset["source_run_id"] == run_id
    assert [step["intent"] for step in asset["action_plan"]] == [
        "open login",
        "submit credentials",
    ]

    approved = await app_client.post(f"/api/regression-assets/{asset['asset_id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"

    kicked_off: list[str] = []

    def fake_kick_off(run_id: str):
        kicked_off.append(run_id)

    monkeypatch.setattr("app.api.regression_assets.kick_off_asset_replay", fake_kick_off)
    replay = await app_client.post(f"/api/regression-assets/{asset['asset_id']}/replay")
    assert replay.status_code == 202
    replay_run = replay.json()["data"]
    assert replay_run["execution_mode"] == "replay"
    assert replay_run["asset_id"] == asset["asset_id"]
    assert kicked_off == [replay_run["run_id"]]

    async with memory_db() as session:
        saved = await session.get(RegressionAsset, asset["asset_id"])
    assert saved is not None
    assert saved.last_replay_run_id == replay_run["run_id"]
    assert saved.last_status == "pending"


@pytest.mark.asyncio
async def test_replay_failure_diagnosis_links_to_asset(memory_db):
    from app.services.regression_assets import ensure_replay_failure_diagnosis

    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add(
            TestCase(
                case_id="TC-20260512-001",
                project_id="demo",
                name="Login works",
                intent="User logs in",
                review_status="approved",
            )
        )
        session.add(
            RegressionAsset(
                asset_id="asset_test",
                project_id="demo",
                case_id="TC-20260512-001",
                source_run_id="run_passed",
                status="approved",
            )
        )
        session.add(
            Run(
                run_id="run_replay_failed",
                trace_id="trace_replay_failed",
                project_id="demo",
                case_id="TC-20260512-001",
                asset_id="asset_test",
                status="failed",
                execution_mode="replay",
                error_message="selector timed out",
            )
        )
        await session.commit()

        diag = await ensure_replay_failure_diagnosis(run_id="run_replay_failed", session=session)
        assert diag.asset_id == "asset_test"
        assert diag.category == "selector_drift"

        rows = (await session.execute(select(Diagnosis))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_deterministic_replay_executes_asset_action_plan(memory_db):
    from app.services.regression_assets import execute_asset_replay

    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add(
            TestCase(
                case_id="TC-20260512-001",
                project_id="demo",
                name="Login works",
                intent="User logs in",
                review_status="approved",
            )
        )
        session.add(
            RegressionAsset(
                asset_id="asset_test",
                project_id="demo",
                case_id="TC-20260512-001",
                source_run_id="run_passed",
                status="approved",
                action_plan=[
                    {
                        "intent": "open login",
                        "tool_name": "browser_navigate",
                        "tool_args": {"url": "https://example.test/login"},
                    },
                    {
                        "intent": "submit",
                        "tool_name": "browser_click",
                        "tool_args": {"selector": "button[type=submit]"},
                    },
                ],
            )
        )
        session.add(
            Run(
                run_id="run_replay",
                trace_id="trace_replay",
                project_id="demo",
                case_id="TC-20260512-001",
                asset_id="asset_test",
                status="pending",
                execution_mode="replay",
            )
        )
        await session.commit()

    calls: list[tuple[str, dict]] = []

    async def fake_call(tool_name: str, arguments: dict) -> dict:
        calls.append((tool_name, arguments))
        return {"content": [{"type": "text", "text": "ok"}]}

    run = await execute_asset_replay(run_id="run_replay", call_tool=fake_call)

    assert run.status == "passed"
    assert calls == [
        ("browser_navigate", {"url": "https://example.test/login"}),
        ("browser_click", {"selector": "button[type=submit]"}),
    ]
    async with memory_db() as session:
        steps = (
            (await session.execute(select(StepEvent).order_by(StepEvent.step_index)))
            .scalars()
            .all()
        )
        asset = await session.get(RegressionAsset, "asset_test")
    assert [step.tool_name for step in steps] == ["browser_navigate", "browser_click"]
    assert steps[0].event == "replay.step.executed"
    assert asset is not None
    assert asset.last_status == "passed"
    assert run.report_html_path
    assert run.artifacts_dir
    assert run.trace_jsonl_path


@pytest.mark.asyncio
async def test_repair_asset_updates_action_plan_and_status(app_client, memory_db):
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add(
            TestCase(
                case_id="TC-20260512-001",
                project_id="demo",
                name="Login works",
                intent="User logs in",
                review_status="approved",
            )
        )
        session.add(
            RegressionAsset(
                asset_id="asset_test",
                project_id="demo",
                case_id="TC-20260512-001",
                source_run_id="run_passed",
                status="needs_repair",
                action_plan=[{"tool_name": "browser_click", "tool_args": {"selector": "old"}}],
            )
        )
        await session.commit()

    response = await app_client.patch(
        "/api/regression-assets/asset_test",
        json={
            "status": "draft",
            "action_plan": [
                {
                    "intent": "submit",
                    "tool_name": "browser_click",
                    "tool_args": {"selector": "button.submit"},
                }
            ],
            "locator_candidates": [{"selector": "button.submit"}],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "draft"
    assert data["action_plan"][0]["tool_args"]["selector"] == "button.submit"


@pytest.mark.asyncio
async def test_confirmed_diagnosis_feedback_routes_to_asset(memory_db):
    from app.services.diagnoser import record_feedback

    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        session.add(
            TestCase(
                case_id="TC-20260512-001",
                project_id="demo",
                name="Login works",
                intent="User logs in",
                review_status="approved",
            )
        )
        session.add(
            RegressionAsset(
                asset_id="asset_test",
                project_id="demo",
                case_id="TC-20260512-001",
                source_run_id="run_passed",
                status="approved",
            )
        )
        session.add(
            Diagnosis(
                diag_id="diag_asset",
                run_id="run_failed",
                case_id="TC-20260512-001",
                asset_id="asset_test",
                diagnoser_prompt_version="v1",
                diagnoser_model="test",
                category="selector_drift",
                reasoning="selector changed",
            )
        )
        await session.commit()

        diag = await record_feedback(
            diag_id="diag_asset",
            feedback="confirmed",
            feedback_target="asset",
            session=session,
        )
        asset = await session.get(RegressionAsset, "asset_test")

    assert diag.feedback_target == "asset"
    assert asset is not None
    assert asset.status == "needs_repair"
    assert asset.last_status == "diagnosis_confirmed:selector_drift"
