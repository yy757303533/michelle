"""Workspace inspection for external zstack-workspace integrations."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceRepoStatus:
    name: str
    path: str
    exists: bool


@dataclass(frozen=True)
class WorkspaceStatus:
    enabled: bool
    ok: bool
    root: str
    detail: str
    repos: list[WorkspaceRepoStatus]


def inspect_workspace(root: str) -> WorkspaceStatus:
    """Return a read-only status snapshot for the configured workspace root."""
    if not root:
        return WorkspaceStatus(
            enabled=False,
            ok=False,
            root="",
            detail="MICHELLE_WORKSPACE_ROOT is not configured",
            repos=[],
        )
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        return WorkspaceStatus(
            enabled=True,
            ok=False,
            root=str(root_path),
            detail="workspace root does not exist",
            repos=[],
        )
    if not root_path.is_dir():
        return WorkspaceStatus(
            enabled=True,
            ok=False,
            root=str(root_path),
            detail="workspace root is not a directory",
            repos=[],
        )
    return WorkspaceStatus(
        enabled=True,
        ok=True,
        root=str(root_path),
        detail="ready",
        repos=_read_gitmodule_repos(root_path),
    )


def _read_gitmodule_repos(root_path: Path) -> list[WorkspaceRepoStatus]:
    gitmodules = root_path / ".gitmodules"
    if not gitmodules.exists():
        return []
    parser = configparser.ConfigParser()
    parser.read(gitmodules, encoding="utf-8")
    repos: list[WorkspaceRepoStatus] = []
    for section in parser.sections():
        path = parser.get(section, "path", fallback="")
        if not path:
            continue
        name = path.rstrip("/").split("/")[-1]
        repos.append(
            WorkspaceRepoStatus(
                name=name,
                path=path,
                exists=(root_path / path).exists(),
            )
        )
    return repos
