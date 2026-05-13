"""Lightweight workspace code search for failure diagnosis."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./:-]{2,}")


def extract_failure_keywords(parts: list[str], *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for token in _TOKEN_RE.findall(part or ""):
            cleaned = token.strip(".,;()[]{}'\"")
            if len(cleaned) < 4:
                continue
            if cleaned.startswith(("http://", "https://")):
                continue
            if cleaned.lower() in {"status", "failed", "error", "returned", "timeout"}:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                out.append(cleaned)
            if len(out) >= limit:
                return out
    return out


def search_workspace_code(*, workspace_root: str, repos: list[str], keywords: list[str]) -> list[dict[str, Any]]:
    if not workspace_root or not keywords:
        return []
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        return []
    candidates: dict[str, dict[str, Any]] = {}
    for repo in repos:
        repo_path = _safe_repo_path(root, repo)
        if repo_path is None:
            continue
        for keyword in keywords[:8]:
            for match in _rg(repo_path=repo_path, keyword=keyword):
                key = f"{repo}:{match['path']}"
                entry = candidates.setdefault(
                    key,
                    {"repo": repo, "path": match["path"], "matches": []},
                )
                if len(entry["matches"]) < settings.michelle_dev_context_max_matches_per_file:
                    entry["matches"].append(
                        {
                            "keyword": keyword,
                            "line_number": match["line_number"],
                            "line": match["line"],
                        }
                    )
                if len(candidates) >= settings.michelle_dev_context_max_files:
                    return list(candidates.values())
    return list(candidates.values())


def configured_code_repos() -> list[str]:
    return [
        repo.strip()
        for repo in settings.michelle_dev_context_repos.split(",")
        if repo.strip()
    ]


def _safe_repo_path(root: Path, repo: str) -> Path | None:
    if not repo or Path(repo).is_absolute() or ".." in Path(repo).parts:
        return None
    path = (root / repo).resolve()
    if root != path and root not in path.parents:
        return None
    return path if path.is_dir() else None


def _rg(*, repo_path: Path, keyword: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "rg",
                "--line-number",
                "--fixed-strings",
                "--glob",
                "!node_modules/**",
                "--glob",
                "!dist/**",
                "--glob",
                "!build/**",
                keyword,
                str(repo_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode not in {0, 1}:
        return []
    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        path, line_number, text = _parse_rg_line(line)
        if not path:
            continue
        try:
            rel = str(Path(path).resolve().relative_to(repo_path.resolve()))
        except ValueError:
            rel = path
        matches.append({"path": rel, "line_number": line_number, "line": text[:500]})
    return matches


def _parse_rg_line(line: str) -> tuple[str, int, str]:
    path, sep, rest = line.partition(":")
    if not sep:
        return "", 0, ""
    line_no, sep, text = rest.partition(":")
    if not sep:
        return "", 0, ""
    try:
        return path, int(line_no), text.strip()
    except ValueError:
        return "", 0, ""
