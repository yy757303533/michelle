from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import app.db as db_mod
from app.config import settings
from app.models import RuntimeSetting
from app.services.dev_context.workspace import inspect_workspace


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


def test_inspect_workspace_reports_disabled_when_root_empty() -> None:
    status = inspect_workspace("")

    assert status.enabled is False
    assert status.ok is False
    assert status.root == ""
    assert status.repos == []


def test_inspect_workspace_reads_gitmodules_and_repo_dirs(tmp_path: Path) -> None:
    root = tmp_path / "zstack-workspace"
    root.mkdir()
    (root / ".gitmodules").write_text(
        '[submodule "zstack"]\n\tpath = zstack\n\turl = ../zstack.git\n',
        encoding="utf-8",
    )
    (root / "zstack").mkdir()

    status = inspect_workspace(str(root))

    assert status.enabled is True
    assert status.ok is True
    assert status.root == str(root.resolve())
    assert status.repos[0].name == "zstack"
    assert status.repos[0].path == "zstack"
    assert status.repos[0].exists is True


@pytest.mark.asyncio
async def test_dev_context_status_reports_configured_paths(
    memory_db, monkeypatch, tmp_path: Path
) -> None:
    from app.main import app

    workspace = tmp_path / "zstack-workspace"
    workspace.mkdir()
    mcp_dir = workspace / "zstack-dev-mcp"
    mcp_dir.mkdir()
    monkeypatch.setattr(settings, "michelle_workspace_root", str(workspace))
    monkeypatch.setattr(settings, "michelle_zdev_mcp_command", "node")
    monkeypatch.setattr(settings, "michelle_zdev_mcp_args", str(mcp_dir / "dist" / "index.js"))
    monkeypatch.setattr(settings, "michelle_zdev_mcp_cwd", str(mcp_dir))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/dev-context/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["workspace"]["enabled"] is True
    assert data["workspace"]["root"] == str(workspace.resolve())
    assert data["zdev_mcp"]["configured"] is True
    assert data["zdev_mcp"]["command"] == "node"
    assert data["zdev_mcp"]["cwd"] == str(mcp_dir)
    assert data["zdev_mcp"]["cwd_exists"] is True
    assert data["security"]["boundary"]


@pytest.mark.asyncio
async def test_dev_context_status_reports_security_findings(
    memory_db, monkeypatch, tmp_path: Path
) -> None:
    from app.main import app

    monkeypatch.setattr(settings, "michelle_workspace_root", str(tmp_path / "missing"))
    monkeypatch.setattr(settings, "michelle_zdev_mcp_args", "")
    monkeypatch.setattr(settings, "michelle_server_logs_json", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/dev-context/status")

    assert response.status_code == 200
    security = response.json()["data"]["security"]
    assert security["ok"] is False
    assert "workspace root is configured but not healthy" in security["findings"]
    assert "MICHELLE_ZDEV_MCP_ARGS is not configured" in security["findings"]


@pytest.mark.asyncio
async def test_dev_context_status_uses_runtime_overrides(
    memory_db, monkeypatch, tmp_path: Path
) -> None:
    from app.main import app

    workspace = tmp_path / "runtime-workspace"
    workspace.mkdir()
    mcp_dir = workspace / "zstack-dev-mcp"
    mcp_dist = mcp_dir / "dist"
    mcp_dist.mkdir(parents=True)
    (mcp_dist / "index.js").write_text("", encoding="utf-8")
    async with memory_db() as session:
        session.add(RuntimeSetting(key="michelle_workspace_root", value=str(workspace)))
        session.add(RuntimeSetting(key="michelle_zdev_mcp_args", value="dist/index.js"))
        session.add(RuntimeSetting(key="michelle_zdev_mcp_cwd", value=str(mcp_dir)))
        session.add(RuntimeSetting(key="michelle_dev_context_repos", value="zstack,premium"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/dev-context/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["workspace"]["root"] == str(workspace.resolve())
    assert data["zdev_mcp"]["entrypoint"] == str(mcp_dist / "index.js")
    assert data["zdev_mcp"]["entrypoint_exists"] is True
    assert data["code_search"]["repos"] == ["zstack", "premium"]
