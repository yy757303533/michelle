"""Coverage items are reviewed test obligations before case drafting."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CoverageItem(SQLModel, table=True):
    __tablename__ = "coverage_items"

    coverage_id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    prd_id: str = Field(index=True)
    requirement_id: str = Field(index=True)
    chapter_index: int = Field(index=True)

    risk_type: str = "business"
    coverage_type: str = "happy"
    title: str
    scenario: str
    rationale: str = ""
    priority: str = "P1"
    review_status: str = Field(default="proposed", index=True)
    linked_case_id: str | None = Field(default=None, index=True)

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
