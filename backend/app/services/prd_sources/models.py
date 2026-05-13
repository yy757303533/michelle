"""Shared PRD source provider models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceType = Literal["markdown", "workspace", "gitlab_mcp", "confluence_mcp"]


@dataclass(frozen=True)
class GitLabFileRef:
    project: str
    file_path: str
    ref: str | None


@dataclass(frozen=True)
class PRDSourceDocument:
    markdown: str
    suggested_name: str
    source_ref: dict[str, Any] = field(default_factory=dict)
