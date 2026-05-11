"""Feedback loop for improving PRD-to-case generation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaseGenerationFeedback(SQLModel, table=True):
    __tablename__ = "case_generation_feedback"

    feedback_id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    case_id: str = Field(index=True)
    project_id: str = Field(index=True)
    generated_from: str | None = Field(default=None, index=True)
    generation_job_id: str | None = Field(default=None, index=True)
    category: str = Field(index=True)
    note: str = ""
    evidence: str = ""
    status: str = Field(default="open", index=True)
    resolved_by_commit: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    resolved_at: datetime | None = Field(default=None, sa_column=Column(TZDateTime()))
