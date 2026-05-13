from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.services.dev_context.workspace import inspect_workspace


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
async def test_dev_context_status_reports_configured_paths(monkeypatch, tmp_path: Path) -> None:
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
