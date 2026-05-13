"""Parsers for external PRD source locators."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from app.services.prd_sources.models import GitLabFileRef


def parse_gitlab_file_url(url: str) -> GitLabFileRef:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    try:
        marker_index = parts.index("-")
    except ValueError as exc:
        raise ValueError("GitLab URL must contain /-/blob/ or /-/raw/") from exc
    if marker_index + 3 > len(parts):
        raise ValueError("GitLab URL is missing blob/raw, ref, or file path")
    mode = parts[marker_index + 1]
    if mode not in {"blob", "raw"}:
        raise ValueError("GitLab URL must contain /-/blob/ or /-/raw/")
    project_parts = parts[:marker_index]
    ref = parts[marker_index + 2]
    file_parts = parts[marker_index + 3 :]
    if not project_parts or not ref or not file_parts:
        raise ValueError("GitLab URL is missing project, ref, or file path")
    return GitLabFileRef(
        project="/".join(project_parts),
        ref=ref,
        file_path="/".join(file_parts),
    )
