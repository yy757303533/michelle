"""Project + PRD models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Project(SQLModel, table=True):
    __tablename__ = "projects"

    project_id: str = Field(primary_key=True)
    """Stable opaque id, e.g. 'p_abc123' (server-minted) or a slug like
    'michelle' (legacy / hand-set)."""

    name: str
    base_url: str = ""
    """Default execution target URL (used by render_login_smoke_prompt etc.)."""
    login_url: str = ""
    """Optional deterministic login page URL for authenticated test runs."""
    description: str = ""

    default_username: str = ""
    default_password: str = ""

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )


class PRD(SQLModel, table=True):
    __tablename__ = "prds"

    prd_id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    name: str

    raw_markdown: str
    """Full markdown body, kept for diff + re-generation."""

    content_hash: str = Field(index=True)
    """SHA-256 of raw_markdown — fast equality check on re-upload."""

    chapters: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    """List of {level, title, normalized_title, body, hash, position}."""

    source_ref: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    """Where this PRD content came from: markdown, workspace, gitlab_mcp, confluence, jira."""

    version: int = 1
    prev_version_id: str | None = None
    """Chain of versions; latest is canonical."""

    uploaded_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(TZDateTime(), nullable=True),
    )
    deleted_by: str = Field(default="", index=True)
    delete_reason: str = ""
