from __future__ import annotations

from pathlib import Path

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
