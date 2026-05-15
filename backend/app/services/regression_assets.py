"""Regression asset extraction, approval, replay bookkeeping, and replay diagnosis."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import Diagnosis, RegressionAsset, Run, StepEvent, TestCase


class RegressionAssetError(ValueError):
    pass


_REPLAY_TASKS: dict[str, asyncio.Task] = {}


def kick_off_asset_replay(run_id: str) -> asyncio.Task:
    task = asyncio.create_task(_safe_execute_asset_replay(run_id=run_id))
    _REPLAY_TASKS[run_id] = task
    task.add_done_callback(lambda _task: _REPLAY_TASKS.pop(run_id, None))
    return task


async def _safe_execute_asset_replay(*, run_id: str) -> None:
    try:
        await execute_asset_replay(run_id=run_id)
    except Exception:  # noqa: BLE001
        from app.db import async_session_maker

        async with async_session_maker() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                run.status = "aborted"
                run.error_message = "asset replay crashed"
                run.ended_at = datetime.now(UTC)
                await session.commit()


async def extract_asset_from_passed_run(*, run_id: str, session: AsyncSession) -> RegressionAsset:
    run = await session.get(Run, run_id)
    if run is None:
        raise RegressionAssetError("run not found")
    if run.status != "passed":
        raise RegressionAssetError("only passed runs can become regression assets")
    case = await session.get(TestCase, run.case_id)
    if case is None:
        raise RegressionAssetError("case not found")

    existing = (
        (
            await session.execute(
                select(RegressionAsset).where(RegressionAsset.source_run_id == run_id)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    steps = (
        (
            await session.execute(
                select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    asset = RegressionAsset(
        asset_id="asset_" + uuid4().hex[:12],
        project_id=run.project_id,
        case_id=run.case_id,
        case_version=run.case_version,
        source_run_id=run.run_id,
        status="draft",
        action_plan=[
            _action_plan_entry_from_step(step)
            for step in steps
            if step.status == "ok" and (step.tool_name or "").startswith("browser_")
        ],
        locator_candidates=[
            {
                "step_index": step.step_index,
                "tool_name": step.tool_name or "",
                "selector": (step.tool_args or {}).get("selector"),
            }
            for step in steps
            if isinstance(step.tool_args, dict) and (step.tool_args or {}).get("selector")
        ],
        assertions=case.assertions or [],
        created_at=now,
        updated_at=now,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


def _action_plan_entry_from_step(step: StepEvent) -> dict[str, Any]:
    entry = {
        "step_index": step.step_index,
        "intent": step.intent or "",
        "tool_name": step.tool_name or "",
        "tool_args": step.tool_args or {},
    }
    locator = _semantic_locator_from_step(step)
    if locator is not None:
        entry["locator"] = locator
    return entry


def _semantic_locator_from_step(step: StepEvent) -> dict[str, Any] | None:
    args = step.tool_args if isinstance(step.tool_args, dict) else {}
    result = step.tool_result if isinstance(step.tool_result, dict) else {}
    fallbacks: list[dict[str, Any]] = []
    if text := args.get("text"):
        fallbacks.append({"strategy": "text", "value": str(text)})
    if element := args.get("element"):
        fallbacks.append({"strategy": "text", "value": str(element)})
    if selector := args.get("selector"):
        return {"strategy": "css", "value": str(selector), "fallbacks": fallbacks}
    if ref := args.get("ref"):
        return {"strategy": "raw_mcp", "value": str(ref), "fallbacks": fallbacks}
    if label := result.get("label"):
        return {"strategy": "label", "value": str(label), "fallbacks": fallbacks}
    return fallbacks[0] if fallbacks else None


async def approve_asset(*, asset_id: str, session: AsyncSession) -> RegressionAsset:
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise RegressionAssetError("asset not found")
    asset.status = "approved"
    asset.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(asset)
    return asset


async def repair_asset(
    *,
    asset_id: str,
    session: AsyncSession,
    status: str | None = None,
    action_plan: list[dict[str, Any]] | None = None,
    locator_candidates: list[dict[str, Any]] | None = None,
    assertions: list[dict[str, Any]] | None = None,
) -> RegressionAsset:
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise RegressionAssetError("asset not found")
    if status is not None:
        if status not in {"draft", "approved", "needs_repair", "retired"}:
            raise RegressionAssetError("invalid asset status")
        asset.status = status
    if action_plan is not None:
        asset.action_plan = action_plan
    if locator_candidates is not None:
        asset.locator_candidates = locator_candidates
    if assertions is not None:
        asset.assertions = assertions
    asset.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(asset)
    return asset


async def record_replay_started(
    *,
    asset: RegressionAsset,
    replay_run: Run,
    session: AsyncSession,
) -> RegressionAsset:
    replay_run.execution_mode = "replay"
    replay_run.asset_id = asset.asset_id
    asset.last_replay_run_id = replay_run.run_id
    asset.last_status = replay_run.status
    asset.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(asset)
    await session.refresh(replay_run)
    return asset


async def execute_asset_replay(
    *,
    run_id: str,
    call_tool: Any | None = None,
) -> Run:
    """Execute an approved asset action plan without LLM planning."""
    from app.db import async_session_maker

    async with async_session_maker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise RegressionAssetError("run not found")
        if run.execution_mode != "replay" or not run.asset_id:
            raise RegressionAssetError("run is not an asset replay")
        asset = await session.get(RegressionAsset, run.asset_id)
        if asset is None:
            raise RegressionAssetError("asset not found")
        run.status = "running"
        run.started_at = datetime.now(UTC)
        await session.commit()

        if call_tool is None:
            await _execute_replay_with_mcp(session=session, run=run, asset=asset)
        else:
            await _execute_replay_plan(session=session, run=run, asset=asset, call_tool=call_tool)
        await session.refresh(run)
        return run


async def _execute_replay_with_mcp(
    *,
    session: AsyncSession,
    run: Run,
    asset: RegressionAsset,
) -> None:
    from app.agent.mcp_stdio import build_playwright_stdio_client
    from app.runtime_config import get_headless
    from app.storage import run_dir as run_dir_for

    rd = run_dir_for(run.project_id, run.run_id)
    rd.mkdir(parents=True, exist_ok=True)
    async with build_playwright_stdio_client(
        cwd=rd,
        headless=await get_headless(session),
        isolated=True,
        output_dir=rd,
    ) as mcp:
        await _execute_replay_plan(session=session, run=run, asset=asset, call_tool=mcp.call_tool)


async def _execute_replay_plan(
    *,
    session: AsyncSession,
    run: Run,
    asset: RegressionAsset,
    call_tool: Any,
) -> None:
    failed = False
    error = ""
    started = run.started_at or datetime.now(UTC)
    for index, action in enumerate(asset.action_plan or []):
        tool_name = str(action.get("tool_name") or "")
        if not tool_name:
            continue
        attempted_args: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        is_error = True
        chosen_args: dict[str, Any] = {}
        for arguments in _replay_argument_candidates(action):
            attempted_args.append(arguments)
            try:
                result = await call_tool(tool_name, arguments)
                is_error = bool(result.get("isError") or result.get("is_error"))
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)}
                is_error = True
            if not is_error:
                chosen_args = arguments
                break
        if is_error and attempted_args:
            chosen_args = attempted_args[-1]
        tool_result = dict(result)
        tool_result["attempted_args"] = attempted_args
        tool_result["chosen_args"] = chosen_args
        step = StepEvent(
            run_id=run.run_id,
            step_index=index,
            phase="replay",
            event="replay.step.executed",
            intent=str(action.get("intent") or ""),
            tool_name=tool_name,
            tool_args=chosen_args,
            tool_result=tool_result,
            status="failed" if is_error else "ok",
            error_message=str(result.get("error") or "")[:500] if is_error else None,
        )
        session.add(step)
        if is_error:
            failed = True
            error = step.error_message or f"{tool_name} failed"
            break

    ended = datetime.now(UTC)
    run.status = "failed" if failed else "passed"
    run.error_message = error or None
    run.ended_at = ended
    run.duration_ms = int((ended - started).total_seconds() * 1000)
    asset.last_replay_run_id = run.run_id
    asset.last_status = run.status
    asset.updated_at = ended
    case = await session.get(TestCase, run.case_id)
    if case is not None:
        from app.services.report_html import run_to_report_input, write_report_files
        from app.storage import run_dir as run_dir_for

        rd = run_dir_for(run.project_id, run.run_id)
        steps = (
            (
                await session.execute(
                    select(StepEvent)
                    .where(StepEvent.run_id == run.run_id)
                    .order_by(StepEvent.step_index)
                )
            )
            .scalars()
            .all()
        )
        report = run_to_report_input(
            run=run,
            steps=steps,
            case_name=case.name,
            case_intent=case.intent,
            case_module=case.module,
        )
        paths = write_report_files(report, rd)
        run.artifacts_dir = str(rd)
        run.report_html_path = str(paths["html"])
        run.trace_jsonl_path = str(rd / "trace.jsonl")
    await session.commit()
    if failed:
        await ensure_replay_failure_diagnosis(run_id=run.run_id, session=session)


def _replay_argument_candidates(action: dict[str, Any]) -> list[dict[str, Any]]:
    raw_args = action.get("tool_args") if isinstance(action.get("tool_args"), dict) else {}
    locator = action.get("locator") if isinstance(action.get("locator"), dict) else None
    candidates: list[dict[str, Any]] = []
    for loc in _locator_sequence(locator):
        candidate = _locator_to_mcp_args(action, loc)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if raw_args not in candidates:
        candidates.append(raw_args)
    return candidates or [{}]


def _locator_sequence(locator: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not locator:
        return []
    sequence = [locator]
    fallbacks = locator.get("fallbacks")
    if isinstance(fallbacks, list):
        sequence.extend(item for item in fallbacks if isinstance(item, dict))
    priority = {"role": 0, "label": 1, "test_id": 2, "text": 3, "css": 4, "raw_mcp": 5}
    return sorted(sequence, key=lambda item: priority.get(str(item.get("strategy") or ""), 99))


def _locator_to_mcp_args(action: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any] | None:
    _ = action
    strategy = locator.get("strategy")
    value = str(locator.get("value") or locator.get("name") or "")
    if not value:
        return None
    if strategy in {"text", "role", "label", "test_id"}:
        return {"element": value, "ref": value}
    if strategy == "css":
        return {"selector": value}
    return None


async def ensure_replay_failure_diagnosis(*, run_id: str, session: AsyncSession) -> Diagnosis:
    run = await session.get(Run, run_id)
    if run is None:
        raise RegressionAssetError("run not found")
    if run.execution_mode != "replay" or not run.asset_id:
        raise RegressionAssetError("run is not an asset replay")
    if run.status not in {"failed", "flaky", "aborted"}:
        raise RegressionAssetError("replay run has not failed")

    existing = (
        (
            await session.execute(
                select(Diagnosis)
                .where(Diagnosis.run_id == run_id)
                .where(Diagnosis.asset_id == run.asset_id)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    asset = await session.get(RegressionAsset, run.asset_id)
    if asset is not None:
        asset.last_replay_run_id = run.run_id
        asset.last_status = run.status
        asset.updated_at = datetime.now(UTC)

    category = "selector_drift" if "selector" in (run.error_message or "").lower() else "unknown"
    diag = Diagnosis(
        diag_id="diag_" + uuid4().hex[:12],
        run_id=run.run_id,
        case_id=run.case_id,
        asset_id=run.asset_id,
        diagnoser_prompt_version="replay_asset_v1",
        diagnoser_model="deterministic",
        category=category,
        confidence=0.6,
        reasoning=(
            "Approved regression asset replay failed. Treat this as asset drift "
            "unless product behavior changed intentionally."
        ),
        fix_suggestion="Review the asset action plan and locator candidates, then repair or retire it.",
    )
    session.add(diag)
    await session.commit()
    await session.refresh(diag)
    return diag
