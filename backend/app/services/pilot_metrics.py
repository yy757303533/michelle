"""Pilot-readiness metrics for the coverage-first product loop."""

from __future__ import annotations

from statistics import mean
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import CoverageItem, Diagnosis, RegressionAsset, Run, TestCase

TERMINAL_RUN_STATUSES = {"passed", "failed", "flaky", "aborted"}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _avg(values: list[int]) -> int | None:
    return int(mean(values)) if values else None


def _speedup(agentic: int | None, replay: int | None) -> float | None:
    if not agentic or not replay:
        return None
    return round(agentic / replay, 4)


async def collect_pilot_metrics(
    *,
    session: AsyncSession,
    project_id: str | None = None,
) -> dict[str, Any]:
    coverage_rows = (
        (await session.execute(_scoped(select(CoverageItem), CoverageItem, project_id)))
        .scalars()
        .all()
    )
    case_rows = (
        (await session.execute(_scoped(select(TestCase), TestCase, project_id))).scalars().all()
    )
    run_rows = (
        (
            await session.execute(
                _scoped(select(Run).where(Run.deleted_at.is_(None)), Run, project_id)
            )
        )
        .scalars()
        .all()
    )
    asset_rows = (
        (await session.execute(_scoped(select(RegressionAsset), RegressionAsset, project_id)))
        .scalars()
        .all()
    )
    diagnosis_rows = (await session.execute(select(Diagnosis))).scalars().all()
    if project_id:
        case_project = {case.case_id: case.project_id for case in case_rows}
        diagnosis_rows = [
            diag for diag in diagnosis_rows if case_project.get(diag.case_id) == project_id
        ]

    coverage_reviewed = [
        row
        for row in coverage_rows
        if row.deleted_at is None and row.review_status in {"accepted", "rejected"}
    ]
    coverage_accepted = [row for row in coverage_reviewed if row.review_status == "accepted"]

    cases_reviewed = [
        row
        for row in case_rows
        if row.deleted_at is None and row.review_status in {"approved", "rejected"}
    ]
    cases_approved = [row for row in cases_reviewed if row.review_status == "approved"]

    agentic_terminal = [
        row
        for row in run_rows
        if row.execution_mode == "agentic" and row.status in TERMINAL_RUN_STATUSES
    ]
    agentic_passed = [row for row in agentic_terminal if row.status == "passed"]

    replay_terminal = [
        row
        for row in run_rows
        if row.execution_mode == "replay" and row.status in TERMINAL_RUN_STATUSES
    ]
    replay_passed = [row for row in replay_terminal if row.status == "passed"]

    active_assets = [row for row in asset_rows if row.status in {"draft", "approved"}]
    approved_assets = [row for row in asset_rows if row.status == "approved"]

    diagnosis_reviewed = [
        row
        for row in diagnosis_rows
        if row.human_feedback in {"confirmed", "wrong", "partially_correct"}
    ]
    diagnosis_confirmed = [row for row in diagnosis_reviewed if row.human_feedback == "confirmed"]
    routing: dict[str, int] = {}
    for row in diagnosis_reviewed:
        key = row.feedback_target or "unrouted"
        routing[key] = routing.get(key, 0) + 1

    avg_agentic = _avg([row.duration_ms for row in agentic_terminal if row.duration_ms is not None])
    avg_replay = _avg([row.duration_ms for row in replay_terminal if row.duration_ms is not None])

    return {
        "project_id": project_id,
        "coverage": {
            "total": len([row for row in coverage_rows if row.deleted_at is None]),
            "reviewed": len(coverage_reviewed),
            "accepted": len(coverage_accepted),
            "acceptance_rate": _rate(len(coverage_accepted), len(coverage_reviewed)),
        },
        "cases": {
            "total": len([row for row in case_rows if row.deleted_at is None]),
            "reviewed": len(cases_reviewed),
            "approved": len(cases_approved),
            "approval_rate": _rate(len(cases_approved), len(cases_reviewed)),
        },
        "execution": {
            "agentic_terminal": len(agentic_terminal),
            "agentic_passed": len(agentic_passed),
            "first_agentic_pass_rate": _rate(len(agentic_passed), len(agentic_terminal)),
            "avg_agentic_duration_ms": avg_agentic,
        },
        "assets": {
            "total": len(asset_rows),
            "extracted": len(active_assets),
            "approved": len(approved_assets),
            "asset_extraction_rate": _rate(len(active_assets), len(agentic_passed)),
            "asset_approval_rate": _rate(len(approved_assets), len(active_assets)),
        },
        "replay": {
            "terminal": len(replay_terminal),
            "passed": len(replay_passed),
            "replay_pass_rate": _rate(len(replay_passed), len(replay_terminal)),
            "avg_replay_duration_ms": avg_replay,
            "speedup_ratio": _speedup(avg_agentic, avg_replay),
        },
        "diagnosis": {
            "reviewed": len(diagnosis_reviewed),
            "confirmed": len(diagnosis_confirmed),
            "confirmation_rate": _rate(len(diagnosis_confirmed), len(diagnosis_reviewed)),
            "feedback_routing_distribution": routing,
        },
    }


def _scoped(stmt, model, project_id: str | None):
    if project_id:
        return stmt.where(model.project_id == project_id)
    return stmt
