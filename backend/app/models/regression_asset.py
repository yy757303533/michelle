"""Reviewed replayable paths extracted from successful runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RegressionAsset(SQLModel, table=True):
    __tablename__ = "regression_assets"

    asset_id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    case_id: str = Field(index=True)
    case_version: int = 1
    source_run_id: str = Field(index=True)
    status: str = Field(default="draft", index=True)

    action_plan: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    locator_candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    assertions: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    last_replay_run_id: str | None = Field(default=None, index=True)
    last_status: str = ""

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
