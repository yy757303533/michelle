"""Tracks the state of an asynchronous PRD-to-cases generation pass.

Generation is N synchronous LLM calls (one per chapter, sequentially);
for a 90-chapter PRD that's 7-45 minutes. Doing it in the request
handler ties up an HTTP connection that any reverse proxy will kill in
60-120s, leaves no progress signal, and turns navigation away into
"did my work get done?" anxiety.

Each /api/prd/<prd_id>/generate now creates one row here, schedules a
background task, and returns the job_id immediately. The frontend
polls `/api/prd/jobs/<job_id>` for `pending → running → done | failed`
plus per-chapter results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PRDGenerationJob(SQLModel, table=True):
    __tablename__ = "prd_generation_jobs"
    __table_args__ = (
        Index(
            "ix_prd_generation_one_active_per_prd",
            "prd_id",
            unique=True,
            sqlite_where=text("status in ('pending', 'running')"),
            postgresql_where=text("status in ('pending', 'running')"),
        ),
    )

    job_id: str = Field(primary_key=True)
    prd_id: str = Field(index=True)
    project_id: str = Field(index=True)

    status: str = "pending"  # pending | running | done | failed | cancelled
    total_chapters: int = 0
    completed_chapters: int = 0
    saved_cases: int = 0
    """Sum of cases persisted across all chapters processed so far."""

    # Per-chapter results (chapter_index → {action, saved_count, error?, ...})
    # Mirrors the old synchronous response shape so frontend rendering
    # stays the same once the job completes.
    results: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # The original GenerateRequest body, frozen at submit time so the
    # background worker is independent of the caller.
    request_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    error: str | None = None
    """Top-level error if the whole job blew up before processing chapters."""

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(TZDateTime(), nullable=True))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(TZDateTime(), nullable=True)
    )
