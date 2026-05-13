"""Read PRD markdown from a configured local workspace repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.services.prd_sources.models import PRDSourceDocument

MAX_WORKSPACE_PRD_BYTES = 2 * 1024 * 1024


def fetch_workspace_file(
    *,
    root: Path,
    repo: str,
    file_path: str,
    ref: str | None,
) -> PRDSourceDocument:
    _validate_relative_child(file_path, label="file_path")
    repo_path = _safe_child(root, repo, label="repo")
    if not repo_path.is_dir():
        raise ValueError(f"workspace repo not found: {repo}")
    if ref:
        markdown = _read_git_file_at_ref(repo_path=repo_path, file_path=file_path, ref=ref)
    else:
        target = _safe_child(repo_path, file_path, label="file_path")
        if not target.exists():
            raise ValueError(f"workspace file not found: {repo}/{file_path}")
        if target.is_dir():
            raise ValueError("workspace file path points to a directory")
        if target.stat().st_size > MAX_WORKSPACE_PRD_BYTES:
            raise ValueError("workspace file is too large")
        markdown = target.read_text(encoding="utf-8")
    return PRDSourceDocument(
        markdown=markdown,
        suggested_name=Path(file_path).name or repo,
        source_ref={
            "source_type": "workspace",
            "repo": repo,
            "file_path": file_path,
            "ref": ref or "",
            "url": "",
        },
    )


def _safe_child(root: Path, child: str, *, label: str) -> Path:
    _validate_relative_child(child, label=label)
    root_resolved = root.expanduser().resolve()
    target = (root_resolved / child).resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"{label} must stay inside the configured workspace")
    return target


def _validate_relative_child(child: str, *, label: str) -> None:
    if not child or Path(child).is_absolute() or ".." in Path(child).parts:
        raise ValueError(f"{label} must stay inside the configured workspace")


def _read_git_file_at_ref(*, repo_path: Path, file_path: str, ref: str) -> str:
    if not file_path or Path(file_path).is_absolute() or ".." in Path(file_path).parts:
        raise ValueError("file_path must stay inside the configured workspace")
    result = subprocess.run(
        ["git", "show", f"{ref}:{file_path}"],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:300]
        raise ValueError(f"failed to read workspace file at ref: {detail}")
    content = result.stdout
    if len(content.encode("utf-8")) > MAX_WORKSPACE_PRD_BYTES:
        raise ValueError("workspace file is too large")
    return content
