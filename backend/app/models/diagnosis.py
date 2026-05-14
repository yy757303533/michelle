"""Diagnosis model — AI's analysis of a failed run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Diagnosis(SQLModel, table=True):
    __tablename__ = "diagnoses"

    diag_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    case_id: str = Field(index=True)
    asset_id: str | None = Field(default=None, index=True)

    diagnoser_prompt_version: str
    diagnoser_model: str

    category: str  # real_bug | flaky | selector_drift | vision_misjudge | env_issue | data_issue | unknown
    confidence: float = 0.0
    reasoning: str = ""
    fix_suggestion: str = ""
    evidence_pack: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    candidate_files: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # Human feedback (key for sediment loop)
    human_feedback: str | None = None  # confirmed | wrong | partially_correct
    feedback_target: str = ""
    feedback_note: str = ""
    feedback_at: datetime | None = Field(
        default=None, sa_column=Column(TZDateTime(), nullable=True)
    )

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )


class DiagnosisJob(SQLModel, table=True):
    __tablename__ = "diagnosis_jobs"

    job_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    project_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    include_dev_context: bool = False
    overwrite_existing: bool = False
    prefer_provider: str = ""
    diag_id: str = ""
    error: str = ""
    created_by: str = ""
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
