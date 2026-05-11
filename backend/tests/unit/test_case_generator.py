"""Tests for the case generator service. LLM is mocked at the gateway level."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.llm.base import LLMResult, RateLimitError
from app.llm.gateway import GatewayClient, LLMGateway
from app.models import (
    Project,  # noqa: F401
    TestCase,  # noqa: F401  registers tables
)
from app.services.case_generator import (
    _parse_batch,
    _strip_fences,
    dedupe_generated_cases,
    estimate_target_cases,
    generate_cases_for_chapter,
    is_actionable_chapter,
)
from app.services.prd_parser import parse_prd
from tests.unit.test_llm_gateway import FakeClient


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _gw_returning(text: str) -> LLMGateway:
    class _Stub(FakeClient):
        async def chat(self, prompt, *, prompt_version, **kwargs):
            return LLMResult(
                text=text,
                provider=self.name,
                model="stub-model",
                input_tokens=1,
                output_tokens=1,
            )

    stub = _Stub("stub")
    return LLMGateway(
        clients=[GatewayClient(name="stub", client=stub, priority=10, available=True)]
    )


def test_estimate_target_cases_scales_with_actionable_detail():
    prd = parse_prd(
        "# T\n\n"
        "## Login\n\nUser can enter email and password to log in.\n\n"
        "## Checkout\n\n"
        + "\n".join(
            [
                "User can select a product, add it to cart, edit quantity, apply coupon, "
                "submit payment, view order confirmation, and handle payment errors."
            ]
            * 12
        )
    )

    assert estimate_target_cases(prd.chapters[0], max_cases=8) < 8
    assert estimate_target_cases(prd.chapters[1], max_cases=8) > estimate_target_cases(
        prd.chapters[0], max_cases=8
    )


def test_internal_api_chapter_is_not_actionable_for_browser_cases():
    prd = parse_prd(
        "# T\n\n"
        "## Settlement API\n\n"
        "The backend service must expose POST /api/settlements. The database should "
        "store idempotency keys and emit Kafka events. No browser UI is involved.\n"
    )

    assert is_actionable_chapter(prd.chapters[0]) is False


def test_dedupe_generated_cases_keeps_stronger_duplicate():
    a = _parse_batch(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "A",
                        "intent": "verify valid login",
                        "module": "Login",
                        "steps": [{"intent": "open login"}],
                        "assertions": [{"description": "user reaches dashboard", "confidence": 0.6}],
                    },
                    {
                        "name": "B",
                        "intent": "verify valid login",
                        "module": "Login",
                        "steps": [{"intent": "open login"}, {"intent": "submit valid credentials"}],
                        "assertions": [{"description": "user reaches dashboard", "confidence": 0.9}],
                    },
                ]
            }
        ),
        _DummyLog(),
    )

    out = dedupe_generated_cases(a.cases)
    assert len(out) == 1
    assert out[0].name == "B"


@pytest.mark.asyncio
async def test_call_retries_rate_limit_before_succeeding(session, monkeypatch):
    session.add(Project(project_id="demo", name="Demo", base_url="http://x"))
    await session.commit()
    prd = parse_prd("# T\n\n## Login\n\nUser enters email and password to log in.\n")

    class _FlakyClient(FakeClient):
        async def chat(self, prompt, *, prompt_version, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise RateLimitError("429 too many requests", provider=self.name)
            return LLMResult(
                text=json.dumps(
                    {
                        "coverage_notes": "ok",
                        "cases": [
                            {
                                "name": "login",
                                "intent": "valid login",
                                "steps": [{"intent": "open login"}, {"intent": "submit"}],
                                "assertions": [{"description": "dashboard opens"}],
                            }
                        ],
                    }
                ),
                provider=self.name,
                model="stub-model",
            )

    sleeps: list[float] = []

    async def _no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.case_generator.asyncio.sleep", _no_sleep)
    flaky = _FlakyClient("flaky")
    gw = LLMGateway(
        clients=[GatewayClient(name="flaky", client=flaky, priority=10, available=True)]
    )

    saved, _ = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="http://x",
        chapter=prd.chapters[0],
        session=session,
        gateway=gw,
    )

    assert len(saved) == 1
    assert flaky.call_count == 2
    assert sleeps


@pytest.mark.asyncio
async def test_generate_cases_for_chapter_uses_configured_batch_timeout(session):
    session.add(Project(project_id="demo", name="Demo", base_url="http://x"))
    await session.commit()
    prd = parse_prd("# T\n\n## Login\n\nUser enters email and password to log in.\n")

    class _AssertingClient(FakeClient):
        async def chat(self, prompt, *, prompt_version, **kwargs):
            assert kwargs["timeout_seconds"] == 240
            return LLMResult(
                text=json.dumps(
                    {
                        "coverage_notes": "ok",
                        "cases": [
                            {
                                "name": "login",
                                "intent": "valid login",
                                "steps": [{"intent": "open login"}, {"intent": "submit"}],
                                "assertions": [{"description": "dashboard opens"}],
                            }
                        ],
                    }
                ),
                provider=self.name,
                model="stub-model",
            )

    gw = LLMGateway(
        clients=[GatewayClient(name="stub", client=_AssertingClient("stub"), priority=10, available=True)]
    )

    saved, _ = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="http://x",
        chapter=prd.chapters[0],
        session=session,
        gateway=gw,
        generation_timeout_seconds=240,
    )

    assert len(saved) == 1


# ── Parsing helpers ──


def test_strip_fences_unfenced():
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_strip_fences_json_fence():
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_fences_bare_fence():
    assert _strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_parse_batch_well_formed():
    payload = {
        "coverage_notes": "happy + edge",
        "cases": [
            {
                "name": "登录成功",
                "intent": "用户使用合法账号登录",
                "module": "登录",
                "tags": ["happy"],
                "priority": "P0",
                "preconditions": ["账号存在"],
                "steps": [
                    {"intent": "打开登录页", "expected": "看到表单"},
                    {"intent": "输入用户名和密码"},
                    {"intent": "点击登录"},
                ],
                "assertions": [
                    {
                        "description": "URL 跳转到 /home",
                        "source": "prd_explicit",
                        "confidence": 0.9,
                        "rationale": "PRD mentions login success",
                    }
                ],
            }
        ],
    }
    batch = _parse_batch(json.dumps(payload), _DummyLog())
    assert len(batch.cases) == 1
    assert batch.cases[0].name == "登录成功"
    assert batch.cases[0].priority == "P0"
    assert batch.cases[0].assertions[0].source == "prd_explicit"
    assert batch.cases[0].assertions[0].confidence == 0.9


def test_parse_batch_partial_validation_salvages_good_cases():
    payload = {
        "coverage_notes": "mixed",
        "cases": [
            # bad case: missing required `name`
            {"intent": "...", "steps": []},
            # good case
            {
                "name": "OK case",
                "intent": "i",
                "steps": [{"intent": "step1"}],
            },
        ],
    }
    batch = _parse_batch(json.dumps(payload), _DummyLog())
    assert len(batch.cases) == 1
    assert batch.cases[0].name == "OK case"


def test_parse_batch_unparseable_raises():
    with pytest.raises(ValueError):
        _parse_batch("definitely not json", _DummyLog())


def test_metadata_chapter_is_not_actionable():
    prd = parse_prd(
        "# T\n\n"
        "## Document Information\n\n"
        "- Version: V1.0.0\n"
        "- Status: Production\n"
        "- Created Date: 2024\n\n"
        "---\n"
    )

    assert is_actionable_chapter(prd.chapters[0]) is False


def test_table_of_contents_is_not_actionable_even_with_feature_words():
    prd = parse_prd(
        "# T\n\n"
        "## Table of Contents\n\n"
        "1. [Project Overview](#1-project-overview)\n"
        "2. [User Registration and Sign In](#2-user-registration-and-sign-in)\n"
        "3. [Investment Flow](#3-investment-flow)\n"
    )

    assert is_actionable_chapter(prd.chapters[0]) is False


def test_short_functional_chapter_is_actionable():
    prd = parse_prd("# T\n\n## Login\n\nUser enters email and password to log in.\n")

    assert is_actionable_chapter(prd.chapters[0]) is True


# ── End-to-end with mocked LLM + in-memory SQLite ──


@pytest.mark.asyncio
async def test_generate_cases_persists_to_db_and_assigns_ids(session):
    project = Project(project_id="demo", name="Demo", base_url="http://x")
    session.add(project)
    await session.commit()

    prd = parse_prd("# T\n\n## Login\n\nUser enters email and password to log in.\n")
    chapter = prd.chapters[0]

    payload = {
        "coverage_notes": "good",
        "cases": [
            {
                "name": "happy login",
                "intent": "valid creds login",
                "tags": ["happy"],
                "priority": "P0",
                "steps": [
                    {"intent": "open login"},
                    {"intent": "fill creds"},
                    {"intent": "click submit"},
                ],
                "assertions": [{"description": "redirected to /home"}],
            },
            {
                "name": "wrong password",
                "intent": "rejected on bad creds",
                "tags": ["error"],
                "priority": "P1",
                "steps": [
                    {"intent": "open login"},
                    {"intent": "fill bad creds"},
                    {"intent": "click submit"},
                ],
                "assertions": [{"description": "shows error message"}],
            },
        ],
    }
    gw = _gw_returning(json.dumps(payload))

    saved, batch = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="http://x",
        chapter=chapter,
        session=session,
        max_cases=5,
        gateway=gw,
    )

    assert len(saved) == 2
    assert all(c.case_id.startswith("TC-") for c in saved)
    # IDs should be sequential
    seqs = [int(c.case_id.rsplit("-", 1)[1]) for c in saved]
    assert seqs[1] == seqs[0] + 1

    # All persisted
    rows = (await session.execute(select(TestCase))).scalars().all()
    assert len(rows) == 2
    assert all(r.review_status == "pending" for r in rows)
    assert all(r.source == "ai-generated" for r in rows)
    assert all(r.prompt_version == "case_gen_v1" for r in rows)
    assert all(r.quality.get("score") is not None for r in rows)


@pytest.mark.asyncio
async def test_generate_cases_skips_metadata_chapter_without_llm_call(session):
    session.add(Project(project_id="demo", name="Demo", base_url="http://x"))
    await session.commit()
    prd = parse_prd(
        "# T\n\n"
        "## Document Information\n\n"
        "- Version: V1.0.0\n"
        "- Status: Production\n"
        "- Created Date: 2024\n\n"
        "---\n"
    )

    class _ExplodingClient(FakeClient):
        async def chat(self, prompt, *, prompt_version, **kwargs):
            raise AssertionError("metadata chapters should not call the LLM")

    gw = LLMGateway(
        clients=[
            GatewayClient(
                name="explode",
                client=_ExplodingClient("explode"),
                priority=10,
                available=True,
            )
        ]
    )

    saved, batch = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="http://x",
        chapter=prd.chapters[0],
        session=session,
        gateway=gw,
    )

    assert saved == []
    assert batch.cases == []
    assert "Skipped before LLM call" in batch.coverage_notes


@pytest.mark.asyncio
async def test_generate_cases_flags_over_specific_boundary_assertion(session):
    session.add(Project(project_id="demo", name="Demo", base_url="http://x"))
    await session.commit()
    prd = parse_prd("# T\n\n## Investment\n\nUsers can enter investment amount.\n")
    chapter = prd.chapters[0]
    payload = {
        "coverage_notes": "",
        "cases": [
            {
                "name": "negative amount",
                "intent": "verify negative amount",
                "tags": ["edge"],
                "steps": [{"intent": "输入 -100 到投资金额"}],
                "assertions": [
                    {
                        "description": "负数输入下页面显示明确的校验错误提示文案",
                        "source": "exploratory",
                        "confidence": 0.4,
                    }
                ],
            }
        ],
    }
    saved, _ = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="http://x",
        chapter=chapter,
        session=session,
        gateway=_gw_returning(json.dumps(payload)),
    )

    flags = saved[0].quality["flags"]
    assert "assertion_too_specific" in flags
    assert "exploratory_boundary" in flags


@pytest.mark.asyncio
async def test_generate_cases_skips_cases_without_steps(session):
    session.add(Project(project_id="demo", name="Demo"))
    await session.commit()
    prd = parse_prd("# T\n\n## A\n\nUser can open the dashboard page.")
    chapter = prd.chapters[0]

    payload = {
        "coverage_notes": "",
        "cases": [
            {"name": "no-steps", "intent": "x", "steps": []},
            {
                "name": "ok",
                "intent": "y",
                "steps": [{"intent": "do thing"}],
            },
        ],
    }
    gw = _gw_returning(json.dumps(payload))
    saved, _ = await generate_cases_for_chapter(
        project_id="demo",
        project_name="Demo",
        base_url="",
        chapter=chapter,
        session=session,
        gateway=gw,
    )
    assert len(saved) == 1
    assert saved[0].name == "ok"


# ── support ──


class _DummyLog:
    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def bind(self, **k):
        return self
