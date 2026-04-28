"""Day 11: diagnoser parsing + pattern store tests (LLM mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.llm.base import LLMResult
from app.models import Diagnosis, Pattern, Run, StepEvent, TestCase
from app.services.diagnoser import (
    DiagnoserError,
    _parse_diagnosis_json,
    diagnose_run,
    record_feedback,
)
from app.services.pattern_store import (
    _keywords,
    _matcher_from_run,
    _score,
    find_matches_for_run,
)

# ── _parse_diagnosis_json ─────────────────────────────────────────────────


def test_parse_clean_json():
    p = _parse_diagnosis_json(
        '{"category":"flaky","confidence":0.7,"reasoning":"x","fix_suggestion":"retry"}'
    )
    assert p["category"] == "flaky"
    assert p["confidence"] == 0.7
    assert p["fix_suggestion"] == "retry"


def test_parse_strips_fences():
    p = _parse_diagnosis_json('```json\n{"category":"real_bug","confidence":0.9}\n```')
    assert p["category"] == "real_bug"
    assert p["confidence"] == 0.9


def test_parse_unknown_category_falls_back_to_unknown():
    p = _parse_diagnosis_json('{"category":"made-up-cat","confidence":0.5}')
    assert p["category"] == "unknown"


def test_parse_clamps_confidence_to_unit_range():
    assert _parse_diagnosis_json('{"category":"flaky","confidence":99}')["confidence"] == 1.0
    assert _parse_diagnosis_json('{"category":"flaky","confidence":-3}')["confidence"] == 0.0


def test_parse_garbage_returns_unknown_zero():
    p = _parse_diagnosis_json("garbage that is not json")
    assert p["category"] == "unknown"
    assert p["confidence"] == 0.0


def test_parse_extracts_first_json_block_from_prose():
    p = _parse_diagnosis_json(
        'Here is the analysis: {"category":"env_issue","confidence":0.8} thanks!'
    )
    assert p["category"] == "env_issue"


# ── diagnose_run + record_feedback (LLM mocked) ────────────────────────────


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


def _seed_failed_run(s: AsyncSession) -> tuple[Run, TestCase]:
    case = TestCase(
        case_id="TC-X",
        project_id="demo",
        name="login flow",
        intent="user logs in",
        module="auth",
        steps=[{"intent": "click 登录"}],
        assertions=[{"description": "see dashboard"}],
        review_status="approved",
    )
    run = Run(
        run_id="R1",
        trace_id="t",
        project_id="demo",
        case_id="TC-X",
        case_version=1,
        env="x",
        status="failed",
        error_message="timeout waiting for navigation after click",
    )
    s.add(case)
    s.add(run)
    s.add(
        StepEvent(
            run_id="R1",
            step_index=3,
            event="agent.step.executed",
            tool_name="browser_click",
            intent="click 登录 button",
            status="failed",
            error_message="element timeout",
        )
    )
    return run, case


@pytest.mark.asyncio
async def test_diagnose_run_persists_diagnosis(db):
    _seed_failed_run(db)
    await db.commit()

    fake = LLMResult(
        text='{"category":"flaky","confidence":0.6,'
        '"reasoning":"the click step timed out which is a known transient issue",'
        '"fix_suggestion":"increase wait_for to 5s"}',
        provider="mock",
        model="mock-1",
        input_tokens=10,
        output_tokens=30,
    )
    with patch("app.services.diagnoser.get_gateway") as gw_mock:
        gw_mock.return_value.chat = AsyncMock(return_value=fake)
        diag = await diagnose_run(run_id="R1", session=db)

    assert diag.category == "flaky"
    assert 0 < diag.confidence <= 1
    assert "wait_for" in diag.fix_suggestion
    assert diag.diag_id.startswith("diag_")

    rows = (await db.execute(select(Diagnosis))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_diagnose_run_idempotent_unless_overwrite(db):
    _seed_failed_run(db)
    await db.commit()

    fake = LLMResult(
        text='{"category":"real_bug","confidence":0.5}',
        provider="mock",
        model="mock-1",
    )
    with patch("app.services.diagnoser.get_gateway") as gw_mock:
        gw_mock.return_value.chat = AsyncMock(return_value=fake)
        a = await diagnose_run(run_id="R1", session=db)
        b = await diagnose_run(run_id="R1", session=db)
        assert a.diag_id == b.diag_id  # idempotent

        c = await diagnose_run(run_id="R1", session=db, overwrite_existing=True)
        assert c.diag_id != a.diag_id  # new row


@pytest.mark.asyncio
async def test_diagnose_run_rejects_passed_run(db):
    db.add(
        Run(
            run_id="ok",
            trace_id="t",
            project_id="d",
            case_id="c",
            case_version=1,
            env="x",
            status="passed",
        )
    )
    await db.commit()
    with pytest.raises(DiagnoserError):
        await diagnose_run(run_id="ok", session=db)


@pytest.mark.asyncio
async def test_record_feedback_confirmed_creates_pattern(db):
    _seed_failed_run(db)
    await db.commit()
    fake = LLMResult(
        text='{"category":"flaky","confidence":0.6,"reasoning":"timeout race","fix_suggestion":"retry"}',
        provider="m",
        model="m",
    )
    with patch("app.services.diagnoser.get_gateway") as gw_mock:
        gw_mock.return_value.chat = AsyncMock(return_value=fake)
        diag = await diagnose_run(run_id="R1", session=db)

    updated = await record_feedback(
        diag_id=diag.diag_id, feedback="confirmed", note="yes flaky", session=db
    )
    assert updated.human_feedback == "confirmed"

    patterns = (await db.execute(select(Pattern))).scalars().all()
    assert len(patterns) == 1
    p = patterns[0]
    assert p.pattern_type == "flaky"
    assert p.hit_count == 1
    assert diag.diag_id in (p.confirmed_by_diag_ids or [])


@pytest.mark.asyncio
async def test_record_feedback_wrong_does_not_sediment(db):
    _seed_failed_run(db)
    await db.commit()
    fake = LLMResult(
        text='{"category":"flaky","confidence":0.6}',
        provider="m",
        model="m",
    )
    with patch("app.services.diagnoser.get_gateway") as gw_mock:
        gw_mock.return_value.chat = AsyncMock(return_value=fake)
        diag = await diagnose_run(run_id="R1", session=db)
    await record_feedback(diag_id=diag.diag_id, feedback="wrong", session=db)
    assert (await db.execute(select(Pattern))).scalars().all() == []


# ── pattern_store internals ───────────────────────────────────────────────


def test_keywords_drops_stop_words_and_short_tokens():
    kw = _keywords("the timeout is a problem when login is slow")
    assert "the" not in kw
    assert "is" not in kw
    assert "timeout" in kw
    assert "login" in kw


def test_keywords_handles_chinese():
    kw = _keywords("登录按钮 没有 反应")
    # Chinese word-like sequences are kept (CJK char clusters)
    assert any("登录" in w for w in kw)


def test_score_higher_when_more_overlap():
    a = {"blob_keywords": ["timeout", "click", "login"]}
    b1 = {"blob_keywords": ["timeout", "click", "login"]}
    b2 = {"blob_keywords": ["dashboard", "menu"]}
    assert _score(a, b1) > _score(a, b2)


def test_score_zero_when_no_keywords():
    assert _score({}, {}) == 0
    assert _score({"blob_keywords": []}, {"blob_keywords": ["x"]}) == 0


def test_matcher_from_run_extracts_failed_step_features():
    run = Run(
        run_id="r",
        trace_id="t",
        project_id="p",
        case_id="c",
        case_version=1,
        env="x",
        status="failed",
        error_message="connection refused localhost",
    )
    steps = [
        StepEvent(
            run_id="r",
            step_index=0,
            event="agent.step.executed",
            tool_name="browser_navigate",
            intent="open page",
            status="ok",
        ),
        StepEvent(
            run_id="r",
            step_index=1,
            event="agent.step.executed",
            tool_name="browser_click",
            intent="click 登录 button",
            status="failed",
            error_message="timeout",
        ),
    ]
    m = _matcher_from_run(run=run, steps=steps)
    assert m["tool"] == "browser_click"
    assert any("connection" in k or "refused" in k for k in m["error_keywords"])


@pytest.mark.asyncio
async def test_find_matches_for_run_returns_relevant_only(db):
    """A pattern about "login timeout flaky" should match a similar new failure."""
    _seed_failed_run(db)
    await db.commit()
    fake = LLMResult(
        text='{"category":"flaky","confidence":0.6,"reasoning":"timeout race",'
        '"fix_suggestion":"increase wait"}',
        provider="m",
        model="m",
    )
    with patch("app.services.diagnoser.get_gateway") as gw_mock:
        gw_mock.return_value.chat = AsyncMock(return_value=fake)
        diag = await diagnose_run(run_id="R1", session=db)
    await record_feedback(diag_id=diag.diag_id, feedback="confirmed", session=db)

    # Now check a fresh failure with the same fingerprint matches
    matches = await find_matches_for_run(run_id="R1", session=db)
    assert len(matches) >= 1
    assert matches[0].pattern_type == "flaky"
