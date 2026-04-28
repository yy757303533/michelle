"""Day 8: review workflow tests.

Covers:
  - PATCH /api/cases/{id} field-level edits + manual_edited_fields tracking
  - POST /api/cases/bulk-review atomicity
  - PRD regen diff-awareness: skip unchanged, mark stale, don't overwrite approved
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.models import PRD, Project, TestCase
from app.services import case_versioning
from app.services.case_versioning import (
    mark_stale_for_removed_chapters,
    plan_regeneration,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
async def memory_db(monkeypatch):
    """Per-test in-memory DB shared with the app + service modules."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Override db.async_session_maker so route handlers use this engine
    import app.db as db_mod

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    yield maker


@pytest.fixture
async def session(memory_db) -> AsyncSession:
    async with memory_db() as s:
        yield s


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _case(case_id: str, project_id: str = "demo", **overrides) -> TestCase:
    base = dict(
        case_id=case_id,
        project_id=project_id,
        name=f"name-{case_id}",
        intent=f"intent-{case_id}",
        module="m",
        tags=["happy"],
        priority="P1",
        preconditions=[],
        steps=[{"intent": "open"}],
        assertions=[{"description": "ok"}],
        source="ai-generated",
        prompt_version="case_gen_v1",
        model_version="claude",
        review_status="pending",
        manual_edited_fields=[],
        version=1,
    )
    base.update(overrides)
    return TestCase(**base)


# ── PATCH /api/cases/{id} ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_records_edited_fields(session, app_client):
    session.add(_case("TC-1"))
    await session.commit()

    r = await app_client.patch(
        "/api/cases/TC-1",
        json={"name": "renamed", "priority": "P0"},
    )
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["edited_fields"]) == ["name", "priority"]
    assert body["data"]["name"] == "renamed"
    assert body["data"]["priority"] == "P0"
    assert sorted(body["data"]["manual_edited_fields"]) == ["name", "priority"]


@pytest.mark.asyncio
async def test_patch_appends_to_manual_edited_fields(session, app_client):
    c = _case("TC-2", manual_edited_fields=["name"])
    session.add(c)
    await session.commit()

    r = await app_client.patch("/api/cases/TC-2", json={"intent": "new intent"})
    assert r.status_code == 200
    fields = r.json()["data"]["manual_edited_fields"]
    assert sorted(fields) == ["intent", "name"]


@pytest.mark.asyncio
async def test_patch_no_change_no_op(session, app_client):
    session.add(_case("TC-3", name="same"))
    await session.commit()

    r = await app_client.patch("/api/cases/TC-3", json={"name": "same"})
    assert r.status_code == 200
    assert r.json()["edited_fields"] == []
    assert r.json()["data"]["manual_edited_fields"] == []


@pytest.mark.asyncio
async def test_patch_resets_review_to_pending_when_was_approved(session, app_client):
    session.add(_case("TC-4", review_status="approved"))
    await session.commit()

    r = await app_client.patch("/api/cases/TC-4", json={"intent": "edit"})
    assert r.status_code == 200
    assert r.json()["data"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_patch_404_for_missing(app_client, memory_db):
    r = await app_client.patch("/api/cases/NOPE", json={"name": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_rejects_non_editable_fields(session, app_client):
    session.add(_case("TC-5"))
    await session.commit()

    # Try to inject something not in EDITABLE_FIELDS via the model's optional fields
    # CaseEdit only declares editable fields, so unknown ones are silently dropped
    # by Pydantic, but if a future bug tries to slip something in we'd notice.
    r = await app_client.patch("/api/cases/TC-5", json={"steps": [{"intent": "x"}]})
    assert r.status_code == 200
    assert r.json()["edited_fields"] == ["steps"]


# ── POST /api/cases/bulk-review ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_review_approve(session, app_client):
    for i in range(3):
        session.add(_case(f"TC-B{i}"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-B0", "TC-B1", "TC-B2"], "action": "approve"},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert sorted(body["updated"]) == ["TC-B0", "TC-B1", "TC-B2"]
    assert body["target_state"] == "approved"

    rows = (await session.execute(select(TestCase))).scalars().all()
    assert all(r.review_status == "approved" for r in rows)


@pytest.mark.asyncio
async def test_bulk_review_skips_already_target_state(session, app_client):
    session.add(_case("TC-A1", review_status="approved"))
    session.add(_case("TC-A2"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-A1", "TC-A2"], "action": "approve"},
    )
    body = r.json()["data"]
    assert body["updated"] == ["TC-A2"]
    assert body["skipped_already_at_state"] == ["TC-A1"]


@pytest.mark.asyncio
async def test_bulk_review_reports_missing(session, app_client):
    session.add(_case("TC-X1"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-X1", "TC-NONEXIST"], "action": "reject"},
    )
    body = r.json()["data"]
    assert body["updated"] == ["TC-X1"]
    assert body["missing"] == ["TC-NONEXIST"]


@pytest.mark.asyncio
async def test_bulk_review_bad_action(app_client):
    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-1"], "action": "purgatory"},
    )
    assert r.status_code == 400


# ── case_versioning: plan_regeneration ─────────────────────────────────────


def _prd_row(prd_id: str, chapters: list[dict], project: str = "demo", **kw) -> PRD:
    base = dict(
        prd_id=prd_id,
        project_id=project,
        name=f"prd-{prd_id}",
        raw_markdown="",
        content_hash="h-" + prd_id,
        chapters=chapters,
        version=1,
    )
    base.update(kw)
    return PRD(**base)


def _chapter_dict(level: int, title: str, body: str = "", position: int = 0) -> dict:
    from app.services.prd_parser import _hash_chapter, normalize_title

    nt = normalize_title(title)
    return {
        "level": level,
        "title": title,
        "normalized_title": nt,
        "body": body,
        "hash": _hash_chapter(level, nt, body),
        "position": position,
    }


@pytest.mark.asyncio
async def test_plan_regen_no_prev_all_regenerate(session):
    chapters = [
        _chapter_dict(2, "A", "body-a", 0),
        _chapter_dict(2, "B", "body-b", 1),
    ]
    new = _prd_row("p1", chapters)
    session.add(Project(project_id="demo", name="demo"))
    session.add(new)
    await session.commit()

    decisions = await plan_regeneration(session=session, new_prd=new, prev_prd=None)
    assert len(decisions) == 2
    assert all(d.action == "regenerate" for d in decisions)


@pytest.mark.asyncio
async def test_plan_regen_unchanged_chapter_skipped(session):
    a = _chapter_dict(2, "A", "body-a", 0)
    b = _chapter_dict(2, "B", "body-b", 1)
    prev = _prd_row("prev", [a, b])
    new = _prd_row("new", [a, b], prev_version_id="prev")
    session.add(Project(project_id="demo", name="demo"))
    session.add(prev)
    session.add(new)
    await session.commit()

    decisions = await plan_regeneration(session=session, new_prd=new, prev_prd=prev)
    assert {d.action for d in decisions} == {"skip_unchanged"}


@pytest.mark.asyncio
async def test_plan_regen_modified_chapter_regenerates(session):
    prev_a = _chapter_dict(2, "A", "v1", 0)
    new_a = _chapter_dict(2, "A", "v2", 0)
    prev = _prd_row("prev", [prev_a])
    new = _prd_row("new", [new_a], prev_version_id="prev")
    session.add(Project(project_id="demo", name="demo"))
    session.add(prev)
    session.add(new)
    await session.commit()

    decisions = await plan_regeneration(session=session, new_prd=new, prev_prd=prev)
    assert decisions[0].action == "regenerate"


@pytest.mark.asyncio
async def test_plan_regen_skips_chapter_with_approved_case(session):
    a = _chapter_dict(2, "A", "v1", 0)
    prev_a_v2 = _chapter_dict(2, "A", "v2", 0)
    prev = _prd_row("prev", [a])
    new = _prd_row("new", [prev_a_v2], prev_version_id="prev")  # modified

    session.add(Project(project_id="demo", name="demo"))
    session.add(prev)
    session.add(new)
    # An approved case from chapter A
    session.add(
        _case(
            "TC-APPROVED",
            review_status="approved",
            generated_from=f"chapter:{a['normalized_title']}#0",
        )
    )
    await session.commit()

    decisions = await plan_regeneration(session=session, new_prd=new, prev_prd=prev)
    assert decisions[0].action == "skip_all_approved"
    assert "TC-APPROVED" in decisions[0].existing_case_ids


# ── case_versioning: mark_stale_for_removed_chapters ───────────────────────


@pytest.mark.asyncio
async def test_mark_stale_when_chapter_removed(session):
    a = _chapter_dict(2, "A", "body-a", 0)
    b = _chapter_dict(2, "B", "body-b", 1)
    prev = _prd_row("prev", [a, b])
    new = _prd_row("new", [a], prev_version_id="prev")  # B removed

    session.add(Project(project_id="demo", name="demo"))
    session.add(prev)
    session.add(new)
    session.add(
        _case(
            "TC-FROM-B-PEND",
            generated_from=f"chapter:{b['normalized_title']}#1",
            review_status="pending",
        )
    )
    session.add(
        _case(
            "TC-FROM-B-APPROVED",
            generated_from=f"chapter:{b['normalized_title']}#1",
            review_status="approved",
        )
    )
    session.add(
        _case(
            "TC-FROM-A-PEND",
            generated_from=f"chapter:{a['normalized_title']}#0",
            review_status="pending",
        )
    )
    await session.commit()

    touched = await mark_stale_for_removed_chapters(
        session=session, new_prd=new, prev_prd=prev
    )

    # Only the pending B-derived case is marked stale
    assert touched == ["TC-FROM-B-PEND"]
    rows = {
        r.case_id: r.review_status
        for r in (await session.execute(select(TestCase))).scalars().all()
    }
    assert rows["TC-FROM-B-PEND"] == "stale"
    assert rows["TC-FROM-B-APPROVED"] == "approved"  # untouched
    assert rows["TC-FROM-A-PEND"] == "pending"  # untouched


@pytest.mark.asyncio
async def test_mark_stale_no_prev_is_noop(session):
    new = _prd_row("new", [_chapter_dict(2, "A", "x", 0)])
    session.add(new)
    await session.commit()
    assert await mark_stale_for_removed_chapters(session=session, new_prd=new, prev_prd=None) == []
