from __future__ import annotations

import pytest
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
    import app.mcp.server as mcp_server
    import app.services.run_orchestrator as ro

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    monkeypatch.setattr(mcp_server.db_mod, "async_session_maker", maker)
    monkeypatch.setattr(ro, "async_session_maker", maker)
    monkeypatch.setattr(ro, "_RUN_TASKS", {})
    yield maker
    await engine.dispose()


async def _seed_case(memory_db, *, review_status: str = "approved") -> str:
    async with memory_db() as session:
        session.add(Project(project_id="demo", name="Demo"))
        case = TestCase(
            case_id="TC-MCP-1",
            project_id="demo",
            name="MCP smoke",
            intent="Verify MCP can read cases",
            module="mcp",
            review_status=review_status,
            steps=[{"intent": "open"}],
            assertions=[{"description": "page opens"}],
        )
        session.add(case)
        await session.commit()
    return case.case_id


@pytest.mark.asyncio
async def test_mcp_case_tools_read_real_cases(memory_db):
    from app.mcp.server import get_case_tool, list_cases_tool

    case_id = await _seed_case(memory_db)

    listed = await list_cases_tool(project_id="demo", status="approved")
    fetched = await get_case_tool(case_id)

    assert "stub" not in listed
    assert listed["count"] == 1
    assert listed["data"][0]["case_id"] == case_id
    assert "stub" not in fetched
    assert fetched["data"]["name"] == "MCP smoke"


@pytest.mark.asyncio
async def test_mcp_execute_case_creates_run_and_kicks_off(memory_db, monkeypatch):
    from app.mcp import server as mcp_server

    case_id = await _seed_case(memory_db)
    kicked: list[dict] = []

    def fake_kick_off(**kwargs):
        kicked.append(kwargs)

    monkeypatch.setattr(mcp_server, "kick_off", fake_kick_off)

    result = await mcp_server.execute_case_tool(case_id, env="mcp")

    assert "stub" not in result
    assert result["data"]["case_id"] == case_id
    assert result["data"]["status"] == "pending"
    assert kicked == [
        {
            "case_id": case_id,
            "run_id": result["data"]["run_id"],
            "env": "mcp",
            "timeout_seconds": mcp_server.DEFAULT_RUN_TIMEOUT,
        }
    ]


@pytest.mark.asyncio
async def test_mcp_execute_case_rejects_duplicate_active_run(memory_db, monkeypatch):
    from app.mcp import server as mcp_server

    case_id = await _seed_case(memory_db)
    async with memory_db() as session:
        session.add(
            Run(
                run_id="run-active",
                trace_id="trace-active",
                project_id="demo",
                case_id=case_id,
                status="running",
            )
        )
        await session.commit()
    monkeypatch.setattr(mcp_server, "kick_off", lambda **_kwargs: None)

    result = await mcp_server.execute_case_tool(case_id, env="mcp")

    assert result["error"]["code"] == "active_run_exists"


@pytest.mark.asyncio
async def test_mcp_get_run_includes_steps(memory_db):
    from app.mcp.server import get_run_tool

    case_id = await _seed_case(memory_db)
    async with memory_db() as session:
        session.add(
            Run(
                run_id="run_mcp",
                trace_id="trace_mcp",
                project_id="demo",
                case_id=case_id,
                status="failed",
                error_message="boom",
            )
        )
        session.add(
            StepEvent(
                run_id="run_mcp",
                step_index=1,
                event="agent.step.executed",
                tool_name="browser_snapshot",
                status="failed",
                error_message="boom",
            )
        )
        await session.commit()

    result = await get_run_tool("run_mcp")

    assert "stub" not in result
    assert result["data"]["run"]["status"] == "failed"
    assert result["data"]["steps"][0]["tool_name"] == "browser_snapshot"
