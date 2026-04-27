"""Pattern — accumulated failure pattern from sediment loop."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Pattern(SQLModel, table=True):
    __tablename__ = "patterns"

    pattern_id: str = Field(primary_key=True)
    pattern_type: str  # flaky | selector_drift | prd_defect | env_jitter | vision_misjudge

    title: str
    description: str
    matcher: dict = Field(default_factory=dict, sa_column=Column(JSON))
    """How to recognise the pattern (regex, semantic features, etc.)"""

    suggested_action: str = ""
    hit_count: int = 0
    last_hit_at: datetime | None = None

    confirmed_by_diag_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
