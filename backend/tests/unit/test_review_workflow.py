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

from app.models import PRD, Diagnosis, Project, Run, StepEvent, TestCase
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
    await engine.dispose()


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
        prompt_version="coverage_draft_v1",
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
    # Pydantic Literal validation rejects with 422 (Unprocessable Entity).
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_review_reset_single_approved_back_to_pending(session, app_client):
    session.add(_case("TC-R1", review_status="approved"))
    await session.commit()

    r = await app_client.post("/api/cases/TC-R1/review", json={"action": "reset"})
    assert r.status_code == 200
    assert r.json()["data"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_review_reset_single_rejected_back_to_pending(session, app_client):
    session.add(_case("TC-R2", review_status="rejected"))
    await session.commit()

    r = await app_client.post("/api/cases/TC-R2/review", json={"action": "reset"})
    assert r.status_code == 200
    assert r.json()["data"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_review_approve_rejects_already_rejected(session, app_client):
    """Direct rejected → approved is forbidden — must reset first.

    The previous implementation silently flipped the verdict, which lets a
    single click overwrite a deliberate human decision."""
    session.add(_case("TC-RJ", review_status="rejected"))
    await session.commit()

    r = await app_client.post("/api/cases/TC-RJ/review", json={"action": "approve"})
    assert r.status_code == 409
    assert "reset" in r.json()["detail"].lower()

    row = await session.get(TestCase, "TC-RJ")
    assert row is not None and row.review_status == "rejected"  # untouched


@pytest.mark.asyncio
async def test_review_reject_rejects_already_approved(session, app_client):
    session.add(_case("TC-AP", review_status="approved"))
    await session.commit()

    r = await app_client.post("/api/cases/TC-AP/review", json={"action": "reject"})
    assert r.status_code == 409

    row = await session.get(TestCase, "TC-AP")
    assert row is not None and row.review_status == "approved"


@pytest.mark.asyncio
async def test_review_reset_pending_is_noop(session, app_client):
    """`reset` on a pending case is a no-op, not an error — the target
    state matches, so it short-circuits before the source-state check."""
    session.add(_case("TC-PD"))  # default = pending
    await session.commit()

    r = await app_client.post("/api/cases/TC-PD/review", json={"action": "reset"})
    assert r.status_code == 200
    assert r.json()["data"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_bulk_approve_skips_rejected_with_distinct_reason(session, app_client):
    """In a mixed selection, approve only flips pending+stale; rejected
    cases are reported under skipped_wrong_state, NOT silently approved."""
    session.add(_case("TC-P1", review_status="pending"))
    session.add(_case("TC-P2", review_status="stale"))
    session.add(_case("TC-R1", review_status="rejected"))
    session.add(_case("TC-A1", review_status="approved"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-P1", "TC-P2", "TC-R1", "TC-A1"], "action": "approve"},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert sorted(body["updated"]) == ["TC-P1", "TC-P2"]
    assert body["skipped_already_at_state"] == ["TC-A1"]
    assert body["skipped_wrong_state"] == ["TC-R1"]

    row = await session.get(TestCase, "TC-R1")
    assert row is not None and row.review_status == "rejected"  # not flipped


@pytest.mark.asyncio
async def test_bulk_reset_skips_pending_as_already_at_state(session, app_client):
    """Bulk reset target = pending; pending rows are already-at-state, NOT
    wrong-state. (Stale rows ARE wrong-state because target is pending and
    stale isn't in the allowed source set.)"""
    session.add(_case("TC-A", review_status="approved"))
    session.add(_case("TC-P", review_status="pending"))
    session.add(_case("TC-S", review_status="stale"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-A", "TC-P", "TC-S"], "action": "reset"},
    )
    body = r.json()["data"]
    assert body["updated"] == ["TC-A"]
    assert body["skipped_already_at_state"] == ["TC-P"]
    assert body["skipped_wrong_state"] == ["TC-S"]


@pytest.mark.asyncio
async def test_list_cases_reports_total_and_truncated(session, app_client):
    """`total` reflects the full table; `truncated` flips on once limit
    drops a real row. This is what the UI uses to detect the silent-drop
    case that confused users with "all (264) / 200 selected" mismatches."""
    for i in range(15):
        session.add(_case(f"TC-T{i:02d}"))
    await session.commit()

    r = await app_client.get("/api/cases/?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 10
    assert body["total"] == 15
    assert body["truncated"] is True

    r2 = await app_client.get("/api/cases/?limit=100")
    body2 = r2.json()
    assert body2["count"] == 15
    assert body2["total"] == 15
    assert body2["truncated"] is False


@pytest.mark.asyncio
async def test_bulk_review_reset_mixed_selection(session, app_client):
    """Reset reverts approved+rejected back to pending; leaves already-pending alone."""
    session.add(_case("TC-RA", review_status="approved"))
    session.add(_case("TC-RR", review_status="rejected"))
    session.add(_case("TC-RP", review_status="pending"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-review",
        json={"case_ids": ["TC-RA", "TC-RR", "TC-RP"], "action": "reset"},
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert sorted(body["updated"]) == ["TC-RA", "TC-RR"]
    assert body["skipped_already_at_state"] == ["TC-RP"]
    assert body["target_state"] == "pending"

    rows = {
        r.case_id: r.review_status
        for r in (await session.execute(select(TestCase))).scalars().all()
    }
    assert rows == {"TC-RA": "pending", "TC-RR": "pending", "TC-RP": "pending"}


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
    # An approved case from chapter A. Signature format is
    # `chapter:<level>:<normalized_title>` after the move-stability fix.
    session.add(
        _case(
            "TC-APPROVED",
            review_status="approved",
            generated_from=f"chapter:{a['level']}:{a['normalized_title']}",
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
            generated_from=f"chapter:{b['level']}:{b['normalized_title']}",
            review_status="pending",
        )
    )
    session.add(
        _case(
            "TC-FROM-B-APPROVED",
            generated_from=f"chapter:{b['level']}:{b['normalized_title']}",
            review_status="approved",
        )
    )
    session.add(
        _case(
            "TC-FROM-A-PEND",
            generated_from=f"chapter:{a['level']}:{a['normalized_title']}",
            review_status="pending",
        )
    )
    await session.commit()

    touched = await mark_stale_for_removed_chapters(session=session, new_prd=new, prev_prd=prev)

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


# ── DELETE /api/cases/{id} ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_pending_case(session, app_client):
    session.add(_case("TC-DEL-1"))
    await session.commit()

    r = await app_client.delete("/api/cases/TC-DEL-1")
    assert r.status_code == 204
    row = await session.get(TestCase, "TC-DEL-1")
    assert row is not None
    assert row.deleted_at is not None

    listed = await app_client.get("/api/cases/?project_id=demo")
    assert listed.status_code == 200
    assert listed.json()["data"] == []

    deleted = await app_client.get("/api/cases/?project_id=demo&deleted=deleted")
    assert deleted.status_code == 200
    assert [c["case_id"] for c in deleted.json()["data"]] == ["TC-DEL-1"]


@pytest.mark.asyncio
async def test_delete_case_preserves_its_run_history(session, app_client):
    session.add(Project(project_id="demo", name="demo"))
    session.add(_case("TC-RUN-DEL"))
    session.add(
        Run(
            run_id="run-del",
            trace_id="trace-run-del",
            project_id="demo",
            case_id="TC-RUN-DEL",
            status="aborted",
        )
    )
    session.add(StepEvent(run_id="run-del", step_index=0, event="agent.step.executed"))
    session.add(
        Diagnosis(
            diag_id="diag-del",
            run_id="run-del",
            case_id="TC-RUN-DEL",
            diagnoser_prompt_version="v1",
            diagnoser_model="fake",
            category="unknown",
        )
    )
    await session.commit()

    r = await app_client.delete("/api/cases/TC-RUN-DEL")

    assert r.status_code == 204
    assert [r.run_id for r in (await session.execute(select(Run))).scalars().all()] == ["run-del"]
    assert [e.run_id for e in (await session.execute(select(StepEvent))).scalars().all()] == [
        "run-del"
    ]
    assert [d.run_id for d in (await session.execute(select(Diagnosis))).scalars().all()] == [
        "run-del"
    ]


@pytest.mark.asyncio
async def test_restore_case_reactivates_soft_deleted_case(session, app_client):
    session.add(_case("TC-RESTORE"))
    await session.commit()

    deleted = await app_client.delete("/api/cases/TC-RESTORE")
    assert deleted.status_code == 204

    restored = await app_client.post("/api/cases/TC-RESTORE/restore")

    assert restored.status_code == 200
    assert restored.json()["data"]["deleted_at"] is None
    listed = await app_client.get("/api/cases/?project_id=demo")
    assert [c["case_id"] for c in listed.json()["data"]] == ["TC-RESTORE"]


@pytest.mark.asyncio
async def test_delete_approved_case_blocked(session, app_client):
    session.add(_case("TC-APP", review_status="approved"))
    await session.commit()

    r = await app_client.delete("/api/cases/TC-APP")
    assert r.status_code == 409
    # Row still there
    row = await session.get(TestCase, "TC-APP")
    assert row is not None and row.review_status == "approved"


@pytest.mark.asyncio
async def test_delete_missing_case_404(app_client):
    r = await app_client.delete("/api/cases/TC-DOES-NOT-EXIST")
    assert r.status_code == 404


# ── POST /api/cases/bulk-delete ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_delete_skips_approved_and_reports(session, app_client):
    session.add(_case("TC-PEND-1"))
    session.add(_case("TC-PEND-2"))
    session.add(_case("TC-APP", review_status="approved"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/bulk-delete",
        json={"case_ids": ["TC-PEND-1", "TC-PEND-2", "TC-APP", "TC-MISS"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert sorted(data["deleted"]) == ["TC-PEND-1", "TC-PEND-2"]
    assert data["skipped_approved"] == ["TC-APP"]
    assert data["missing"] == ["TC-MISS"]
    # Approved row survives
    assert (await session.get(TestCase, "TC-APP")) is not None


@pytest.mark.asyncio
async def test_bulk_delete_preserves_run_history_for_deleted_cases(session, app_client):
    session.add(Project(project_id="demo", name="demo"))
    session.add(_case("TC-BULK-RUN"))
    session.add(
        Run(
            run_id="run-bulk-del",
            trace_id="trace-run-bulk-del",
            project_id="demo",
            case_id="TC-BULK-RUN",
            status="aborted",
        )
    )
    session.add(StepEvent(run_id="run-bulk-del", step_index=0, event="agent.step.executed"))
    await session.commit()

    r = await app_client.post("/api/cases/bulk-delete", json={"case_ids": ["TC-BULK-RUN"]})

    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == ["TC-BULK-RUN"]
    assert [r.run_id for r in (await session.execute(select(Run))).scalars().all()] == [
        "run-bulk-del"
    ]
    assert [e.run_id for e in (await session.execute(select(StepEvent))).scalars().all()] == [
        "run-bulk-del"
    ]


# ── POST /api/cases/ (manual create) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_create_lands_pending(session, app_client):
    session.add(Project(project_id="demo", name="demo"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/",
        json={
            "project_id": "demo",
            "name": "manual login",
            "intent": "user logs in",
            "priority": "P0",
            "auth_state": "logged-out",
            "steps": [{"intent": "open /login"}],
        },
    )
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["source"] == "manual"
    assert data["review_status"] == "pending"
    assert data["auth_state"] == "logged-out"
    assert data["priority"] == "P0"


@pytest.mark.asyncio
async def test_manual_create_case_ids_are_global_across_projects(session, app_client):
    session.add(Project(project_id="demo-a", name="demo-a"))
    session.add(Project(project_id="demo-b", name="demo-b"))
    await session.commit()

    async def create(project_id: str):
        return await app_client.post(
            "/api/cases/",
            json={
                "project_id": project_id,
                "name": f"manual {project_id}",
                "steps": [{"intent": "open"}],
            },
        )

    first = await create("demo-a")
    second = await create("demo-b")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["case_id"] != second.json()["data"]["case_id"]


@pytest.mark.asyncio
async def test_manual_create_rejects_unknown_project(app_client):
    r = await app_client.post(
        "/api/cases/",
        json={"project_id": "ghost", "name": "x"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_manual_create_rejects_invalid_auth_state(session, app_client):
    session.add(Project(project_id="demo", name="demo"))
    await session.commit()
    r = await app_client.post(
        "/api/cases/",
        json={"project_id": "demo", "name": "x", "auth_state": "bogus"},
    )
    assert r.status_code == 422


# ── auth_state column behaviour ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_state_default_logged_in(session):
    """Cases that don't specify auth_state get the secure default (logged-in)."""
    session.add(_case("TC-DEFAULT"))
    await session.commit()
    row = await session.get(TestCase, "TC-DEFAULT")
    assert row is not None
    assert row.auth_state == "logged-in"


@pytest.mark.asyncio
async def test_patch_auth_state_round_trip(session, app_client):
    session.add(_case("TC-AUTH", review_status="pending"))
    await session.commit()

    r = await app_client.patch("/api/cases/TC-AUTH", json={"auth_state": "wrong-creds"})
    assert r.status_code == 200
    await session.refresh(await session.get(TestCase, "TC-AUTH"))
    row = await session.get(TestCase, "TC-AUTH")
    assert row is not None
    assert row.auth_state == "wrong-creds"
    assert "auth_state" in row.manual_edited_fields


# ── Bulk run preflight (POST /api/runs/) ───────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_run_creates_one_run_per_case_id(session, app_client, monkeypatch):
    """POST /api/runs/ with N case_ids spawns N Run rows. Background
    `kick_off` is monkeypatched to a no-op so the test doesn't actually
    start Chromium."""
    session.add(Project(project_id="demo", name="demo"))
    session.add(_case("TC-A", review_status="approved"))
    session.add(_case("TC-B", review_status="approved"))
    await session.commit()

    # Stub out background runner so HTTP returns immediately
    from app.api import runs as runs_api

    monkeypatch.setattr(runs_api, "kick_off", lambda **kw: None)

    r = await app_client.post("/api/runs/", json={"case_ids": ["TC-A", "TC-B"], "env": "default"})
    assert r.status_code == 200
    run_ids = r.json()["data"]["run_ids"]
    assert len(run_ids) == 2

    from app.models import Run

    rows = (await session.execute(select(Run))).scalars().all()
    assert {r.case_id for r in rows} == {"TC-A", "TC-B"}
    assert all(r.status == "pending" for r in rows)
