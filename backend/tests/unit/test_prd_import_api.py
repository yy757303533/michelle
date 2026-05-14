from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import app.db as db_mod
from app.models import PRD, RuntimeSetting


@pytest.fixture
async def app_client(monkeypatch):
    from app.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_markdown_prd_stores_source_ref(app_client) -> None:
    client, maker = app_client

    response = await client.post(
        "/api/prd/import",
        json={
            "project_id": "demo",
            "source": {
                "type": "markdown",
                "markdown": "# Imported Spec\n\n## Login\n\nUsers can sign in.",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Imported Spec"
    assert data["source_ref"]["source_type"] == "markdown"
    async with maker() as session:
        prd = (await session.execute(select(PRD))).scalars().one()
    assert prd.source_ref["source_type"] == "markdown"


@pytest.mark.asyncio
async def test_upload_prd_keeps_markdown_source_ref(app_client) -> None:
    client, _maker = app_client

    response = await client.post(
        "/api/prd/upload",
        json={
            "project_id": "demo",
            "name": "",
            "markdown": "# Uploaded Spec\n\n## Login\n\nUsers can sign in.",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["source_ref"]["source_type"] == "markdown"


@pytest.mark.asyncio
async def test_import_workspace_prd_uses_runtime_workspace_root(app_client, tmp_path) -> None:
    client, maker = app_client
    workspace = tmp_path / "workspace"
    repo = workspace / "zstack"
    repo.mkdir(parents=True)
    (repo / "prd.md").write_text("# Runtime Workspace PRD\n\n## Flow\n\nWorks.", encoding="utf-8")
    async with maker() as session:
        session.add(RuntimeSetting(key="michelle_workspace_root", value=str(workspace)))
        await session.commit()

    response = await client.post(
        "/api/prd/import",
        json={
            "project_id": "demo",
            "source": {"type": "workspace", "repo": "zstack", "file_path": "prd.md"},
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Runtime Workspace PRD"
    assert data["source_ref"]["repo"] == "zstack"
