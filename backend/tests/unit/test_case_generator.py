"""Tests for the case generator service. LLM is mocked at the gateway level."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.llm.base import LLMResult
from app.llm.gateway import GatewayClient, LLMGateway
from app.models import (
    Project,  # noqa: F401
    TestCase,  # noqa: F401  registers tables
)
from app.services.case_generator import (
    _parse_batch,
    _strip_fences,
    generate_cases_for_chapter,
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
                "assertions": [{"description": "URL 跳转到 /home"}],
            }
        ],
    }
    batch = _parse_batch(json.dumps(payload), _DummyLog())
    assert len(batch.cases) == 1
    assert batch.cases[0].name == "登录成功"
    assert batch.cases[0].priority == "P0"


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


@pytest.mark.asyncio
async def test_generate_cases_skips_cases_without_steps(session):
    session.add(Project(project_id="demo", name="Demo"))
    await session.commit()
    prd = parse_prd("# T\n\n## A\n\nbody")
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
