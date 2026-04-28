"""Diagnosis model — AI's analysis of a failed run."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Diagnosis(SQLModel, table=True):
    __tablename__ = "diagnoses"

    diag_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    case_id: str = Field(index=True)

    diagnoser_prompt_version: str
    diagnoser_model: str

    category: str  # real_bug | flaky | selector_drift | vision_misjudge | env_issue | data_issue | unknown
    confidence: float = 0.0
    reasoning: str = ""
    fix_suggestion: str = ""

    # Human feedback (key for sediment loop)
    human_feedback: str | None = None  # confirmed | wrong | partially_correct
    feedback_note: str = ""
    feedback_at: datetime | None = None

    created_at: datetime = Field(default_factory=_utcnow)
