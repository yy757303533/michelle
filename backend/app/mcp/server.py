"""Michelle MCP server surface for agent clients.

The tools below intentionally reuse the same database models and service
functions as the REST/UI path. MCP is only an agent-facing transport layer, not
an alternate business-logic implementation.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import desc, select

from app import db as db_mod
from app.models import Run, StepEvent, TestCase
from app.obs import get_logger
from app.services.diagnoser import diagnose_run
from app.services.run_orchestrator import DEFAULT_RUN_TIMEOUT, create_run_row, kick_off

_log = get_logger(__name__)


async def list_cases_tool(
    project_id: str,
    status: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List active cases for a project."""
    async with db_mod.async_session_maker() as session:
        stmt = (
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .where(TestCase.deleted_at.is_(None))
            .order_by(desc(TestCase.created_at), desc(TestCase.case_id))
            .limit(max(1, min(limit, 500)))
        )
        if status:
            stmt = stmt.where(TestCase.review_status == status)
        rows = (await session.execute(stmt)).scalars().all()
        return {"data": [row.model_dump() for row in rows], "count": len(rows)}


async def get_case_tool(case_id: str) -> dict[str, Any]:
    """Fetch a single case."""
    async with db_mod.async_session_maker() as session:
        row = await session.get(TestCase, case_id)
        if row is None or row.deleted_at is not None:
            return {"error": {"code": "not_found", "message": "case not found"}}
        return {"data": row.model_dump()}


async def execute_case_tool(
    case_id: str,
    env: str = "default",
    timeout_seconds: int = DEFAULT_RUN_TIMEOUT,
) -> dict[str, Any]:
    """Create a run through the normal orchestrator and schedule execution."""
    async with db_mod.async_session_maker() as session:
        case = await session.get(TestCase, case_id)
        if case is None or case.deleted_at is not None:
            return {"error": {"code": "not_found", "message": "case not found"}}
        if case.review_status != "approved":
            return {
                "error": {
                    "code": "case_not_approved",
                    "message": f"case {case_id} must be approved before it can run",
                }
            }
        active = (
            (
                await session.execute(
                    select(Run)
                    .where(Run.case_id == case_id)
                    .where(Run.deleted_at.is_(None))
                    .where(Run.status.in_(["pending", "running"]))
                    .order_by(desc(Run.created_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if active is not None:
            return {
                "error": {
                    "code": "active_run_exists",
                    "message": (
                        f"case {case_id} already has an active run "
                        f"({active.status}: {active.run_id})"
                    ),
                }
            }
        run = await create_run_row(case_id=case_id, env=env, session=session)
        await session.commit()
        await session.refresh(run)
        kick_off(
            case_id=run.case_id,
            run_id=run.run_id,
            env=run.env,
            timeout_seconds=timeout_seconds,
        )
        return {"data": run.model_dump()}


async def get_run_tool(run_id: str) -> dict[str, Any]:
    """Fetch run status and persisted step events."""
    async with db_mod.async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None or run.deleted_at is not None:
            return {"error": {"code": "not_found", "message": "run not found"}}
        steps = (
            (
                await session.execute(
                    select(StepEvent)
                    .where(StepEvent.run_id == run_id)
                    .order_by(StepEvent.step_index)
                )
            )
            .scalars()
            .all()
        )
        return {
            "data": {
                "run": run.model_dump(),
                "steps": [step.model_dump() for step in steps],
            }
        }


async def diagnose_tool(
    run_id: str,
    overwrite_existing: bool = False,
    include_dev_context: bool = False,
    prefer_provider: str | None = None,
) -> dict[str, Any]:
    """Run or reuse diagnosis for a failed run."""
    async with db_mod.async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None or run.deleted_at is not None:
            return {"error": {"code": "not_found", "message": "run not found"}}
        diag = await diagnose_run(
            run_id=run_id,
            session=session,
            prefer_provider=prefer_provider,
            overwrite_existing=overwrite_existing,
            include_dev_context=include_dev_context,
        )
        await session.commit()
        return {"data": diag.model_dump()}


async def suggest_cases_tool(description: str, max_cases: int = 8) -> dict[str, Any]:
    """Decline raw case generation; Michelle's durable path is coverage-first."""
    return {
        "error": {
            "code": "coverage_first_required",
            "message": (
                "Michelle no longer generates executable cases directly from a raw description. "
                "Import a PRD, review coverage, then draft cases from accepted coverage items."
            ),
        },
        "description": description,
        "max_cases": max_cases,
    }


def build_mcp_server():
    """Construct an MCP server exposing Michelle tools.

    Returns:
        FastMCP server instance (or None if MCP deps missing).
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        _log.warning(
            "mcp.unavailable",
            note="Install 'mcp' package to enable Michelle's MCP surface",
        )
        return None

    mcp = FastMCP("michelle")

    @mcp.tool()
    async def list_cases(project_id: str, status: str | None = None) -> dict[str, Any]:
        """List Michelle test cases for a project, optionally filtered by review status."""
        return await list_cases_tool(project_id=project_id, status=status)

    @mcp.tool()
    async def get_case(case_id: str) -> dict[str, Any]:
        """Fetch a single test case by id."""
        return await get_case_tool(case_id)

    @mcp.tool()
    async def execute_case(case_id: str, env: str = "default") -> dict[str, Any]:
        """Execute a test case (same path as Web UI 'Run' button). Returns run_id."""
        return await execute_case_tool(case_id=case_id, env=env)

    @mcp.tool()
    async def get_run(run_id: str) -> dict[str, Any]:
        """Get run status + step events."""
        return await get_run_tool(run_id)

    @mcp.tool()
    async def diagnose(run_id: str) -> dict[str, Any]:
        """Trigger AI diagnosis on a failed run. Returns diagnosis structured fields."""
        return await diagnose_tool(run_id)

    @mcp.tool()
    async def suggest_cases(description: str, max_cases: int = 8) -> dict[str, Any]:
        """Light-weight: propose draft cases for a feature without saving."""
        return await suggest_cases_tool(description=description, max_cases=max_cases)

    _log.info("mcp.server.built", tools=6)
    return mcp


if __name__ == "__main__":
    server = build_mcp_server()
    if server is None:
        raise SystemExit("MCP deps not available; run `uv add mcp` first")
    server.run()
