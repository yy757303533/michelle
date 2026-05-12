"""Draft TestCase rows from accepted coverage items."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import CoverageItem, Project, RequirementItem, TestCase

CASE_ID_ALLOCATION_LOCK = asyncio.Lock()


def _mint_case_id(seq: int) -> str:
    today = datetime.now(UTC).strftime("%Y%m%d")
    return f"TC-{today}-{seq:04d}"


async def _next_seq(session: AsyncSession, project_id: str) -> int:
    """Find the next global case sequence for today."""
    _ = project_id
    today_prefix = f"TC-{datetime.now(UTC).strftime('%Y%m%d')}-"
    rows = await session.execute(
        select(TestCase.case_id).where(TestCase.case_id.like(f"{today_prefix}%"))
    )
    ids = [row[0] for row in rows.all()]
    if not ids:
        return 1
    max_seq = 0
    for case_id in ids:
        try:
            max_seq = max(max_seq, int(case_id.rsplit("-", 1)[1]))
        except ValueError:
            continue
    return max_seq + 1


async def draft_case_from_coverage_item(
    *,
    coverage: CoverageItem,
    session: AsyncSession,
) -> tuple[TestCase, bool]:
    if coverage.review_status != "accepted":
        raise ValueError("coverage must be accepted before drafting case")
    if coverage.linked_case_id:
        existing = await session.get(TestCase, coverage.linked_case_id)
        if existing is not None:
            return existing, True

    project = await session.get(Project, coverage.project_id)
    if project is None:
        raise ValueError("project not found")
    requirement = await session.get(RequirementItem, coverage.requirement_id)

    async with CASE_ID_ALLOCATION_LOCK:
        seq = await _next_seq(session, coverage.project_id)
        case_id = _mint_case_id(seq)
        case = TestCase(
            case_id=case_id,
            project_id=coverage.project_id,
            coverage_id=coverage.coverage_id,
            name=coverage.title[:200],
            intent=coverage.scenario[:1000],
            module=coverage.risk_type,
            tags=[coverage.coverage_type, coverage.risk_type],
            priority=coverage.priority if coverage.priority in {"P0", "P1", "P2"} else "P1",
            preconditions=[],
            steps=[
                {
                    "intent": coverage.scenario,
                    "expected": "The behavior matches the accepted coverage item.",
                }
            ],
            assertions=[
                {
                    "description": coverage.scenario,
                    "source": "prd_explicit" if requirement else "domain_inferred",
                    "confidence": 0.7,
                    "evidence": getattr(requirement, "evidence", "") if requirement else "",
                    "rationale": coverage.rationale,
                }
            ],
            quality={
                "score": 0.7,
                "severity": "medium",
                "flags": ["drafted_from_coverage"],
                "reviewer_notes": ["Review executable steps before approval."],
            },
            source="ai-generated",
            prompt_version="coverage_draft_v1",
            model_version="deterministic",
            generated_from=f"coverage:{coverage.coverage_id}",
            review_status="pending",
            version=1,
        )
        coverage.linked_case_id = case_id
        coverage.updated_at = datetime.now(UTC)
        session.add(case)
        await session.commit()
        await session.refresh(case)
        return case, False
