"""Tracks asynchronous PRD-to-coverage design analysis jobs.

The current API analyzes coverage synchronously, but the model name reflects
the coverage-first product spine. Keep this table distinct from retired
PRD-direct case generation job storage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DesignGenerationJob(SQLModel, table=True):
    __tablename__ = "design_generation_jobs"
    __table_args__ = (
        Index(
            "ix_design_generation_one_active_per_prd",
            "prd_id",
            unique=True,
            sqlite_where=text("status in ('pending', 'running')"),
            postgresql_where=text("status in ('pending', 'running')"),
        ),
    )

    job_id: str = Field(primary_key=True)
    prd_id: str = Field(index=True)
    project_id: str = Field(index=True)

    status: str = "pending"
    total_chapters: int = 0
    completed_chapters: int = 0
    requirements_created: int = 0
    coverage_created: int = 0

    results: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    request_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    error: str | None = None
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(TZDateTime(), nullable=True))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(TZDateTime(), nullable=True)
    )
