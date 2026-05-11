"""Tests for projects auto-id, project credentials echoing through to
prompts, PRD delete/cascading, and the background generation job
(create_job + run_job + status endpoints).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.models import (
    PRD,
    Diagnosis,
    PRDGenerationJob,
    Project,
    ProjectMember,
    Run,
    StepEvent,
    TestCase,
)


async def _noop_async(*_args, **_kwargs):
    return None


@pytest.fixture
async def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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


# ── projects auto-id ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_without_id_auto_mints(app_client):
    r = await app_client.post(
        "/api/projects/",
        json={"name": "Demo", "base_url": "http://localhost:5000/"},
    )
    assert r.status_code == 200
    pid = r.json()["data"]["project_id"]
    assert pid.startswith("p_")
    assert len(pid) >= 5  # p_<6+ hex>


@pytest.mark.asyncio
async def test_create_project_with_id_uses_it(app_client):
    r = await app_client.post(
        "/api/projects/",
        json={"project_id": "explicit", "name": "Demo"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["project_id"] == "explicit"


@pytest.mark.asyncio
async def test_create_project_empty_name_rejected(app_client):
    r = await app_client.post("/api/projects/", json={"name": "   "})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_with_known_id_updates_existing(app_client, session):
    session.add(Project(project_id="demo", name="Old", base_url="http://old/"))
    await session.commit()

    r = await app_client.post(
        "/api/projects/",
        json={
            "project_id": "demo",
            "name": "New",
            "base_url": "http://new/",
            "login_url": "http://new/login",
            "default_username": "admin",
            "default_password": "p",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "New"
    assert data["base_url"] == "http://new/"
    assert data["login_url"] == "http://new/login"
    assert data["default_username"] == "admin"
    assert data["default_password"] == ""
    assert data["default_password_is_set"] is True


@pytest.mark.asyncio
async def test_delete_project_removes_owned_data(app_client, session):
    session.add(Project(project_id="tmp", name="Temporary"))
    session.add(ProjectMember(project_id="tmp", user_id="u1", role="admin"))
    session.add(
        PRD(
            prd_id="prd-tmp",
            project_id="tmp",
            name="Spec",
            raw_markdown="# Spec",
            content_hash="hash",
        )
    )
    session.add(
        PRDGenerationJob(
            job_id="job-tmp",
            prd_id="prd-tmp",
            project_id="tmp",
            total_chapters=1,
        )
    )
    session.add(
        TestCase(
            case_id="TC-TMP",
            project_id="tmp",
            name="case",
            intent="intent",
            module="module",
            steps=[{"intent": "open"}],
            assertions=[{"description": "works"}],
        )
    )
    session.add(
        Run(
            run_id="run-tmp",
            trace_id="trace-tmp",
            project_id="tmp",
            case_id="TC-TMP",
            status="failed",
        )
    )
    session.add(StepEvent(run_id="run-tmp", step_index=0, event="agent.step.executed"))
    session.add(
        Diagnosis(
            diag_id="diag-tmp",
            run_id="run-tmp",
            case_id="TC-TMP",
            diagnoser_prompt_version="v1",
            diagnoser_model="test",
            category="unknown",
        )
    )
    await session.commit()

    r = await app_client.delete("/api/projects/tmp")

    assert r.status_code == 204
    for model in (Project, ProjectMember, PRD, PRDGenerationJob, TestCase, Run, StepEvent, Diagnosis):
        assert (await session.execute(select(model))).scalars().all() == []


@pytest.mark.asyncio
async def test_import_cases_persists_every_case(app_client, session):
    session.add(Project(project_id="demo", name="Demo"))
    await session.commit()

    r = await app_client.post(
        "/api/cases/import",
        json={
            "project_id": "demo",
            "cases": [
                {
                    "project_id": "demo",
                    "name": "first",
                    "intent": "open first page",
                    "steps": [{"intent": "open first", "expected": "first shown"}],
                    "assertions": [{"description": "first shown"}],
                },
                {
                    "project_id": "demo",
                    "name": "second",
                    "intent": "open second page",
                    "steps": [{"intent": "open second", "expected": "second shown"}],
                    "assertions": [{"description": "second shown"}],
                },
            ],
        },
    )

    assert r.status_code == 200
    assert r.json()["count"] == 2
    rows = (await session.execute(select(TestCase).order_by(TestCase.name))).scalars().all()
    assert [row.name for row in rows] == ["first", "second"]


@pytest.mark.asyncio
async def test_list_cases_supports_server_pagination_and_search(app_client, session):
    session.add(Project(project_id="demo", name="Demo"))
    for idx, name in enumerate(["Alpha checkout", "Beta search", "Gamma checkout"], start=1):
        session.add(
            TestCase(
                case_id=f"TC-PAGE-{idx}",
                project_id="demo",
                name=name,
                intent=f"{name} flow",
                module="payments" if "checkout" in name else "search",
                review_status="approved",
                tags=["checkout"] if "checkout" in name else ["search"],
                steps=[{"intent": "open page"}],
                assertions=[{"description": "page opens"}],
            )
        )
    await session.commit()

    r = await app_client.get("/api/cases/?project_id=demo&limit=1&offset=1&q=checkout")

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["count"] == 1
    assert body["offset"] == 1
    assert body["limit"] == 1
    assert body["truncated"] is True
    assert body["data"][0]["name"] in {"Alpha checkout", "Gamma checkout"}


@pytest.mark.asyncio
async def test_concurrent_manual_case_creation_mints_unique_ids(app_client, session):
    session.add(Project(project_id="demo", name="Demo"))
    await session.commit()

    async def create_one(name: str):
        return await app_client.post(
            "/api/cases/",
            json={
                "project_id": "demo",
                "name": name,
                "intent": name,
                "steps": [{"intent": "open page"}],
                "assertions": [{"description": "page opens"}],
            },
        )

    r1, r2 = await asyncio.gather(create_one("first"), create_one("second"))

    assert r1.status_code == 201
    assert r2.status_code == 201
    ids = {r1.json()["data"]["case_id"], r2.json()["data"]["case_id"]}
    assert len(ids) == 2


# ── PRD delete cascades children's prev_version_id ─────────────────────────


def _prd(prd_id: str, version: int = 1, prev: str | None = None) -> PRD:
    return PRD(
        prd_id=prd_id,
        project_id="demo",
        name="P",
        raw_markdown="",
        content_hash="h-" + prd_id,
        chapters=[],
        version=version,
        prev_version_id=prev,
    )


@pytest.mark.asyncio
async def test_delete_prd_unchains_children(session, app_client):
    session.add(Project(project_id="demo", name="demo"))
    session.add(_prd("p1"))
    session.add(_prd("p2", version=2, prev="p1"))
    session.add(_prd("p3", version=3, prev="p2"))
    await session.commit()

    r = await app_client.delete("/api/prd/p2")
    assert r.status_code == 204
    assert (await session.get(PRD, "p2")) is None
    # p3 was pointing at the deleted row; pointer cleared, not dangling
    p3 = await session.get(PRD, "p3")
    assert p3 is not None and p3.prev_version_id is None


@pytest.mark.asyncio
async def test_delete_prd_keeps_generated_cases(session, app_client):
    """Deleting a PRD must not cascade to its generated cases — the
    user often wants to keep approved cases even after pruning a
    duplicate upload."""
    session.add(Project(project_id="demo", name="demo"))
    session.add(_prd("p1"))
    session.add(
        TestCase(
            case_id="TC-1",
            project_id="demo",
            name="x",
            intent="x",
            generated_from="chapter:2:login",
            review_status="approved",
            tags=[],
            preconditions=[],
            steps=[],
            assertions=[],
            manual_edited_fields=[],
        )
    )
    await session.commit()

    r = await app_client.delete("/api/prd/p1")
    assert r.status_code == 204
    cases = (await session.execute(select(TestCase))).scalars().all()
    assert len(cases) == 1
    assert cases[0].case_id == "TC-1"


# ── PRD generation background worker ───────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_endpoint_returns_202_and_job_id(app_client, session, monkeypatch):
    """POST /generate is no longer synchronous — returns 202 + job_id and
    leaves the row in `pending` for the background worker."""
    proj = Project(project_id="demo", name="demo")
    prd = PRD(
        prd_id="prd-1",
        project_id="demo",
        name="P",
        raw_markdown="",
        content_hash="h",
        chapters=[
            {
                "level": 2,
                "title": "A",
                "normalized_title": "a",
                "body": "x",
                "hash": "ha",
                "position": 0,
            }
        ],
        version=1,
    )
    session.add(proj)
    session.add(prd)
    await session.commit()

    # Don't actually fire the worker (it would call LLMs)
    from app.services import prd_generation_worker as worker

    monkeypatch.setattr(worker, "kick_off", lambda job_id: None)
    from app.api import prd as prd_api

    monkeypatch.setattr(prd_api, "kick_off", lambda job_id: None)

    r = await app_client.post(
        "/api/prd/prd-1/generate",
        json={"chapter_indices": [0], "generation_timeout_seconds": 240},
    )
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["job_id"].startswith("gen_")
    assert data["status"] == "pending"
    assert data["total_chapters"] == 1

    # Job row should exist
    job = await session.get(PRDGenerationJob, data["job_id"])
    assert job is not None
    assert job.status == "pending"
    assert job.total_chapters == 1


@pytest.mark.asyncio
async def test_generate_endpoint_reuses_and_resumes_active_job(
    app_client, session, monkeypatch
):
    proj = Project(project_id="demo", name="demo")
    prd = PRD(
        prd_id="prd-1",
        project_id="demo",
        name="P",
        raw_markdown="",
        content_hash="h",
        chapters=[
            {
                "level": 2,
                "title": "A",
                "normalized_title": "a",
                "body": "x",
                "hash": "ha",
                "position": 0,
            }
        ],
        version=1,
    )
    active = PRDGenerationJob(
        job_id="gen_active",
        prd_id="prd-1",
        project_id="demo",
        status="running",
        total_chapters=1,
        completed_chapters=0,
        saved_cases=0,
        results=[],
        request_payload={"chapter_indices": [0]},
    )
    session.add(proj)
    session.add(prd)
    session.add(active)
    await session.commit()

    from app.api import prd as prd_api

    kicked: list[str] = []
    monkeypatch.setattr(prd_api, "kick_off", lambda job_id: kicked.append(job_id))

    r = await app_client.post(
        "/api/prd/prd-1/generate",
        json={"chapter_indices": [0], "generation_timeout_seconds": 240},
    )
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["job_id"] == "gen_active"
    assert data["status"] == "running"
    assert data["reused"] is True
    assert kicked == ["gen_active"]


@pytest.mark.asyncio
async def test_generate_endpoint_uses_runtime_case_generation_provider(
    app_client, session, monkeypatch
):
    from app.models import RuntimeSetting

    proj = Project(project_id="demo", name="demo")
    prd = PRD(
        prd_id="prd-1",
        project_id="demo",
        name="P",
        raw_markdown="",
        content_hash="h",
        chapters=[
            {
                "level": 2,
                "title": "A",
                "normalized_title": "a",
                "body": "x",
                "hash": "ha",
                "position": 0,
            }
        ],
        version=1,
    )
    session.add(proj)
    session.add(prd)
    session.add(RuntimeSetting(key="case_generation_provider", value="codex-cli"))
    session.add(RuntimeSetting(key="case_generation_preflight_timeout_seconds", value="45"))
    await session.commit()

    from app.api import prd as prd_api

    monkeypatch.setattr(prd_api, "kick_off", lambda job_id: None)

    r = await app_client.post(
        "/api/prd/prd-1/generate",
        json={"chapter_indices": [0], "generation_timeout_seconds": 240},
    )
    assert r.status_code == 202
    data = r.json()["data"]
    job = await session.get(PRDGenerationJob, data["job_id"])
    assert job is not None
    assert job.request_payload["prefer_provider"] == "codex-cli"
    assert job.request_payload["preflight_timeout_seconds"] == 45
    assert job.request_payload["generation_timeout_seconds"] == 240


@pytest.mark.asyncio
async def test_get_job_endpoint_returns_status(app_client, session):
    session.add(
        PRDGenerationJob(
            job_id="gen_x",
            prd_id="p",
            project_id="demo",
            status="running",
            total_chapters=5,
            completed_chapters=2,
            saved_cases=12,
            results=[
                {"chapter_index": 0, "saved_count": 8, "skipped": False},
                {"chapter_index": 1, "saved_count": 4, "skipped": False},
            ],
            request_payload={},
        )
    )
    await session.commit()

    r = await app_client.get("/api/prd/jobs/gen_x")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "running"
    assert data["completed_chapters"] == 2
    assert data["saved_cases"] == 12
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404(app_client):
    r = await app_client.get("/api/prd/jobs/gen_does_not_exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_job_walks_chapters_and_marks_done(memory_db, monkeypatch):
    """End-to-end on the worker: create a job, feed a stub
    `generate_cases_for_chapter`, watch the job row progress through
    pending → running → done. No real LLMs hit."""
    from app.models import Project as ProjModel
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(ProjModel(project_id="demo", name="demo", base_url="http://x"))
        s.add(
            PRD(
                prd_id="p1",
                project_id="demo",
                name="P",
                raw_markdown="",
                content_hash="h",
                chapters=[
                    {
                        "level": 2,
                        "title": f"C{i}",
                        "normalized_title": f"c{i}",
                        "body": f"User can view C{i} page.",
                        "hash": f"h{i}",
                        "position": i,
                    }
                    for i in range(2)
                ],
                version=1,
            )
        )
        await s.commit()

    job_id = await worker.create_job(
        prd_id="p1",
        project_id="demo",
        request_payload={"chapter_indices": [0, 1], "max_cases_per_chapter": 3},
        total_chapters=2,
    )

    # Stub generate_cases_for_chapter to return 2 cases per chapter without
    # touching any LLM. Returns (saved, batch) where saved is a list of
    # TestCase rows already added to session, batch has coverage_notes.
    class _StubBatch:
        coverage_notes = "stubbed"

    async def _persist_stub(*, session, chapter, project_id, **_kw):
        from app.models import TestCase

        saved = []
        for i in range(2):
            tc = TestCase(
                case_id=f"TC-{chapter.normalized_title}-{i}",
                project_id=project_id,
                name="x",
                intent="x",
                generated_from=f"chapter:{chapter.level}:{chapter.normalized_title}",
                tags=[],
                preconditions=[],
                steps=[],
                assertions=[],
                manual_edited_fields=[],
                review_status="pending",
            )
            session.add(tc)
            saved.append(tc)
        await session.commit()
        return saved

    async def _stub_batch(*, chapters, **_kw):
        return [(chapter, _StubBatch(), "stub-model") for chapter in chapters]

    monkeypatch.setattr(worker, "generate_batches_for_chapters", _stub_batch)
    monkeypatch.setattr(worker, "persist_generated_batch", _persist_stub)
    monkeypatch.setattr(
        worker, "_preflight_llm_provider", lambda _prefer, _timeout=20: _noop_async()
    )

    await worker.run_job(job_id)

    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, job_id)
        assert job is not None
        assert job.status == "done"
        assert job.completed_chapters == 2
        assert job.saved_cases == 4  # 2 chapters × 2 cases each
        assert len(job.results) == 2


@pytest.mark.asyncio
async def test_run_job_batches_adjacent_actionable_chapters(memory_db, monkeypatch):
    from app.models import Project as ProjModel
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(ProjModel(project_id="demo", name="demo", base_url="http://x"))
        s.add(
            PRD(
                prd_id="p1",
                project_id="demo",
                name="P",
                raw_markdown="",
                content_hash="h",
                chapters=[
                    {
                        "level": 2,
                        "title": f"C{i}",
                        "normalized_title": f"c{i}",
                        "body": f"User can view C{i} page.",
                        "hash": f"h{i}",
                        "position": i,
                    }
                    for i in range(4)
                ],
                version=1,
            )
        )
        await s.commit()

    job_id = await worker.create_job(
        prd_id="p1",
        project_id="demo",
        request_payload={
            "chapter_indices": [0, 1, 2, 3],
            "max_cases_per_chapter": 8,
            "generation_timeout_seconds": 240,
        },
        total_chapters=4,
    )

    calls: list[list[str]] = []
    timeouts: list[int] = []

    class _StubBatch:
        def __init__(self, notes: str):
            self.coverage_notes = notes

    async def _stub_batch(
        *,
        chapters,
        generation_timeout_seconds=None,
        **_kw,
    ):
        calls.append([c.normalized_title for c in chapters])
        timeouts.append(generation_timeout_seconds)
        return [(chapter, _StubBatch(f"notes {chapter.normalized_title}"), "stub-model") for chapter in chapters]

    async def _persist_stub(
        *,
        session,
        chapter,
        project_id,
        generation_job_id=None,
        **_kw,
    ):
        from app.models import TestCase

        tc = TestCase(
            case_id=f"TC-{chapter.normalized_title}",
            project_id=project_id,
            name=f"case {chapter.normalized_title}",
            intent="x",
            generated_from=f"chapter:{chapter.level}:{chapter.normalized_title}",
            generation_job_id=generation_job_id,
            tags=[],
            preconditions=[],
            steps=[{"intent": "open page"}],
            assertions=[],
            manual_edited_fields=[],
            review_status="pending",
        )
        session.add(tc)
        await session.commit()
        return [tc]

    monkeypatch.setattr(worker, "generate_batches_for_chapters", _stub_batch)
    monkeypatch.setattr(worker, "persist_generated_batch", _persist_stub)
    monkeypatch.setattr(
        worker, "_preflight_llm_provider", lambda _prefer, _timeout=20: _noop_async()
    )

    await worker.run_job(job_id)

    assert calls == [["c0", "c1"], ["c2", "c3"]]
    assert timeouts == [240, 240]
    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, job_id)
        assert job is not None
        assert job.status == "done"
        assert job.completed_chapters == 4
        assert job.saved_cases == 4


@pytest.mark.asyncio
async def test_run_job_resumes_running_job_after_process_restart(memory_db, monkeypatch):
    from app.models import Project as ProjModel
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(ProjModel(project_id="demo", name="demo", base_url="http://x"))
        s.add(
            PRD(
                prd_id="p1",
                project_id="demo",
                name="P",
                raw_markdown="",
                content_hash="h",
                chapters=[
                    {
                        "level": 2,
                        "title": f"C{i}",
                        "normalized_title": f"c{i}",
                        "body": f"User can view C{i} page.",
                        "hash": f"h{i}",
                        "position": i,
                    }
                    for i in range(6)
                ],
                version=1,
            )
        )
        s.add(
            PRDGenerationJob(
                job_id="gen_resume",
                prd_id="p1",
                project_id="demo",
                status="running",
                total_chapters=6,
                completed_chapters=4,
                saved_cases=3,
                results=[
                    {
                        "chapter_index": i,
                        "chapter_title": f"C{i}",
                        "saved_count": 3 if i == 0 else 0,
                        "saved_case_ids": [],
                        "coverage_notes": "already done",
                        "skipped": False,
                    }
                    for i in range(4)
                ],
                request_payload={
                    "chapter_indices": [0, 1, 2, 3, 4, 5],
                    "max_cases_per_chapter": 8,
                    "batch_chapter_count": 4,
                },
            )
        )
        await s.commit()

    calls: list[list[int]] = []

    class _StubBatch:
        coverage_notes = "resumed"

    async def _stub_batch(*, chapters, **_kw):
        calls.append([c.position for c in chapters])
        return [(chapter, _StubBatch(), "stub-model") for chapter in chapters]

    async def _persist_stub(*, session, chapter, project_id, generation_job_id=None, **_kw):
        from app.models import TestCase

        tc = TestCase(
            case_id=f"TC-{chapter.normalized_title}",
            project_id=project_id,
            name=f"case {chapter.normalized_title}",
            intent="x",
            generated_from=f"chapter:{chapter.level}:{chapter.normalized_title}",
            generation_job_id=generation_job_id,
            tags=[],
            preconditions=[],
            steps=[{"intent": "open page"}],
            assertions=[],
            manual_edited_fields=[],
            review_status="pending",
        )
        session.add(tc)
        await session.commit()
        return [tc]

    monkeypatch.setattr(worker, "generate_batches_for_chapters", _stub_batch)
    monkeypatch.setattr(worker, "persist_generated_batch", _persist_stub)
    monkeypatch.setattr(
        worker, "_preflight_llm_provider", lambda _prefer, _timeout=20: _noop_async()
    )

    await worker.run_job("gen_resume")

    assert calls == [[4, 5]]
    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, "gen_resume")
        assert job is not None
        assert job.status == "done"
        assert job.completed_chapters == 6
        assert job.saved_cases == 5
        assert [r["chapter_index"] for r in job.results] == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_run_job_skips_metadata_chapter_without_llm(memory_db, monkeypatch):
    from app.models import Project as ProjModel
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(ProjModel(project_id="demo", name="demo", base_url="http://x"))
        s.add(
            PRD(
                prd_id="p1",
                project_id="demo",
                name="P",
                raw_markdown="",
                content_hash="h",
                chapters=[
                    {
                        "level": 2,
                        "title": "Document Information",
                        "normalized_title": "document information",
                        "body": "- Version: V1.0.0\n- Status: Production\n- Created Date: 2024",
                        "hash": "h0",
                        "position": 0,
                    }
                ],
                version=1,
            )
        )
        await s.commit()

    job_id = await worker.create_job(
        prd_id="p1",
        project_id="demo",
        request_payload={"chapter_indices": [0], "max_cases_per_chapter": 3},
        total_chapters=1,
    )

    async def _explode(**_kw):
        raise AssertionError("metadata chapters should not call the LLM")

    monkeypatch.setattr(worker, "generate_batches_for_chapters", _explode)

    await worker.run_job(job_id)

    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, job_id)
        assert job is not None
        assert job.status == "done"
        assert job.completed_chapters == 1
        assert job.saved_cases == 0
        assert job.results[0]["skipped"] is True
        assert job.results[0]["skip_action"] == "non_actionable"


@pytest.mark.asyncio
async def test_run_job_fails_fast_when_llm_provider_unavailable(memory_db, monkeypatch):
    from app.llm.base import LLMAuthError
    from app.models import Project as ProjModel
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(ProjModel(project_id="demo", name="demo", base_url="http://x"))
        s.add(
            PRD(
                prd_id="p1",
                project_id="demo",
                name="P",
                raw_markdown="",
                content_hash="h",
                chapters=[
                    {
                        "level": 2,
                        "title": "Login",
                        "normalized_title": "login",
                        "body": "User can enter email and password to log in.",
                        "hash": "h0",
                        "position": 0,
                    }
                ],
                version=1,
            )
        )
        await s.commit()

    job_id = await worker.create_job(
        prd_id="p1",
        project_id="demo",
        request_payload={
            "chapter_indices": [0],
            "prefer_provider": "codex-cli",
            "preflight_timeout_seconds": 55,
        },
        total_chapters=1,
    )

    async def _preflight(_prefer, timeout_seconds=20):
        assert timeout_seconds == 55
        raise LLMAuthError("Not logged in", provider="claude-cli")

    async def _explode(**_kw):
        raise AssertionError("generation should not start after failed preflight")

    monkeypatch.setattr(worker, "_preflight_llm_provider", _preflight)
    monkeypatch.setattr(worker, "generate_batches_for_chapters", _explode)

    await worker.run_job(job_id)

    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.completed_chapters == 0
        assert "LLM provider unavailable" in (job.error or "")


@pytest.mark.asyncio
async def test_run_job_marks_failed_on_missing_prd(memory_db):
    from app.services import prd_generation_worker as worker

    job_id = await worker.create_job(
        prd_id="ghost",
        project_id="ghost",
        request_payload={},
        total_chapters=0,
    )
    await worker.run_job(job_id)

    async with memory_db() as s:
        job = await s.get(PRDGenerationJob, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error is not None
        assert "missing" in (job.error or "").lower()


@pytest.mark.asyncio
async def test_cancel_job_rolls_back_pending_generated_cases(memory_db):
    from app.services import prd_generation_worker as worker

    async with memory_db() as s:
        s.add(Project(project_id="demo", name="demo"))
        s.add(
            PRDGenerationJob(
                job_id="gen_cancel",
                prd_id="p1",
                project_id="demo",
                status="running",
                total_chapters=2,
                completed_chapters=1,
            )
        )
        s.add(
            TestCase(
                case_id="TC-JOB-1",
                project_id="demo",
                name="x",
                intent="x",
                source="ai-generated",
                review_status="pending",
                generation_job_id="gen_cancel",
            )
        )
        s.add(
            TestCase(
                case_id="TC-JOB-2",
                project_id="demo",
                name="x",
                intent="x",
                source="ai-generated",
                review_status="approved",
                generation_job_id="gen_cancel",
            )
        )
        s.add(
            TestCase(
                case_id="TC-OTHER",
                project_id="demo",
                name="x",
                intent="x",
                source="ai-generated",
                review_status="pending",
                generation_job_id="gen_other",
            )
        )
        await s.commit()

        deleted = await worker.rollback_generated_cases(s, "gen_cancel")
        assert deleted == 1

        remaining = (
            (await s.execute(select(TestCase.case_id).order_by(TestCase.case_id))).scalars().all()
        )
        assert remaining == ["TC-JOB-2", "TC-OTHER"]


# ── TZDateTime tz-awareness round-trip ─────────────────────────────────────


@pytest.mark.asyncio
async def test_tzdatetime_reads_back_utc_aware(session):
    from datetime import UTC

    from app.models import RuntimeSetting

    session.add(RuntimeSetting(key="x", value="y"))
    await session.commit()
    row = await session.get(RuntimeSetting, "x")
    assert row is not None
    # The point of TZDateTime: even though SQLite stored a naive ISO string,
    # the column type re-attaches UTC on read.
    assert row.updated_at.tzinfo is not None
    assert row.updated_at.utcoffset().total_seconds() == 0
    _ = UTC  # silence unused import warning if any


# ── Project credentials reach the execute prompt ───────────────────────────


def test_render_execute_prompt_includes_creds(monkeypatch):
    """The fix for "Precondition not met: user is not logged in" was to
    flow `project.default_username/password` into the agent prompt. Lock
    the surface so a future refactor can't silently break it."""
    from app.models import Project as ProjModel
    from app.models import TestCase as CaseModel
    from app.services.run_orchestrator import render_execute_prompt

    proj = ProjModel(
        project_id="demo",
        name="Demo",
        base_url="http://x/",
        default_username="admin@example.com",
        default_password="hunter2",
    )
    case = CaseModel(
        case_id="TC-1",
        project_id="demo",
        name="login",
        intent="i",
        priority="P1",
        tags=[],
        preconditions=["fresh session"],
        steps=[{"intent": "open /login"}],
        assertions=[{"description": "URL ends /home"}],
        manual_edited_fields=[],
        auth_state="logged-in",
    )
    rendered = render_execute_prompt(case, proj)
    assert "admin@example.com" in rendered
    assert "hunter2" in rendered
    assert "logged-in" in rendered


def test_render_execute_prompt_omits_creds_when_unset() -> None:
    from app.models import Project as ProjModel
    from app.models import TestCase as CaseModel
    from app.services.run_orchestrator import render_execute_prompt

    proj = ProjModel(project_id="demo", name="Demo", base_url="http://x/")
    case = CaseModel(
        case_id="TC-1",
        project_id="demo",
        name="x",
        intent="i",
        priority="P1",
        tags=[],
        preconditions=[],
        steps=[{"intent": "open"}],
        assertions=[],
        manual_edited_fields=[],
        auth_state="logged-in",
    )
    rendered = render_execute_prompt(case, proj)
    assert "no default credentials configured" in rendered.lower()


# Suppress unused-import linter on Any (kept for type hints in fixtures)
_ = Any
