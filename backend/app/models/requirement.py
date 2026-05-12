"""Requirement items extracted from PRDs for coverage-first test design."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RequirementItem(SQLModel, table=True):
    __tablename__ = "requirement_items"

    requirement_id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    prd_id: str = Field(index=True)
    chapter_index: int = Field(index=True)
    chapter_hash: str = ""

    text: str
    type: str = "behavior"
    evidence: str = ""
    confidence: float = 0.0
    status: str = Field(default="active", index=True)

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
