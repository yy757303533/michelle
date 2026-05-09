"""Day 10: tests for /api/runs/{id}/artifacts/{filename} sandboxed file serving."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import Run


@pytest.fixture
async def memory_db(monkeypatch, tmp_path):
    """Per-test in-memory DB + redirected artifacts root."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.db as db_mod

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)

    # Redirect artifacts dir
    from app import storage

    monkeypatch.setattr(storage, "artifacts_root", lambda: tmp_path)

    yield maker, tmp_path
    await engine.dispose()


@pytest.fixture
async def app_client(memory_db):
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _seed_run(memory_db, run_id: str = "r1", project_id: str = "demo") -> tuple:
    return run_id, project_id


@pytest.mark.asyncio
async def test_list_artifacts_empty_run_returns_empty_list(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r1",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()

    r = await app_client.get("/api/runs/r1/artifacts")
    assert r.status_code == 200
    assert r.json() == {"data": []}


@pytest.mark.asyncio
async def test_list_artifacts_finds_files(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r2",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()

    rd = root / "demo" / "r2"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "screenshots").mkdir(exist_ok=True)
    (rd / "step-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (rd / "trace.jsonl").write_text("{}", encoding="utf-8")

    r = await app_client.get("/api/runs/r2/artifacts")
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["data"]}
    assert "step-1.png" in names
    assert "trace.jsonl" in names

    images = [f for f in r.json()["data"] if f["is_image"]]
    assert len(images) == 1
    assert images[0]["name"] == "step-1.png"
    assert images[0]["kind"] == "screenshot"
    trace = next(f for f in r.json()["data"] if f["name"] == "trace.jsonl")
    assert trace["kind"] == "trace"


@pytest.mark.asyncio
async def test_serve_png_returns_correct_content_type(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r3",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()

    rd = root / "demo" / "r3"
    rd.mkdir(parents=True, exist_ok=True)
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (rd / "shot.png").write_bytes(body)

    r = await app_client.get("/api/runs/r3/artifacts/shot.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == body


@pytest.mark.asyncio
async def test_path_traversal_blocked(memory_db, app_client, tmp_path):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r4",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()

    rd = root / "demo" / "r4"
    rd.mkdir(parents=True, exist_ok=True)
    # secret file outside the run dir
    (root / "secret.txt").write_text("CONFIDENTIAL", encoding="utf-8")

    # Server should canonicalise + reject paths that escape the run dir.
    r = await app_client.get("/api/runs/r4/artifacts/..%2Fsecret.txt")
    assert r.status_code in {400, 404}

    r = await app_client.get("/api/runs/r4/artifacts/../secret.txt")
    assert r.status_code in {400, 404}


@pytest.mark.asyncio
async def test_unknown_run_404(app_client):
    r = await app_client.get("/api/runs/does-not-exist/artifacts")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_missing_artifact_404(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r5",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()
    (root / "demo" / "r5").mkdir(parents=True, exist_ok=True)
    r = await app_client.get("/api/runs/r5/artifacts/nope.png")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_jsonl_content_type(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r6",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()
    rd = root / "demo" / "r6"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "trace.jsonl").write_text('{"step":1}\n{"step":2}\n', encoding="utf-8")
    r = await app_client.get("/api/runs/r6/artifacts/trace.jsonl")
    assert r.status_code == 200
    assert "ndjson" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_html_artifact_is_download_not_inline(memory_db, app_client):
    maker, root = memory_db
    async with maker() as s:
        s.add(
            Run(
                run_id="r7",
                trace_id="t",
                project_id="demo",
                case_id="c",
                case_version=1,
                env="x",
                status="passed",
            )
        )
        await s.commit()
    rd = root / "demo" / "r7"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "debug.html").write_text("<script>window.x=1</script>", encoding="utf-8")

    r = await app_client.get("/api/runs/r7/artifacts/debug.html")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in r.headers.get("content-disposition", "").lower()
