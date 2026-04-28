"""Run + StepEvent models — execution records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    run_id: str = Field(primary_key=True)
    trace_id: str = Field(index=True)
    project_id: str = Field(index=True)
    case_id: str = Field(index=True)
    case_version: int = 1
    env: str = "default"

    status: str = "pending"  # pending | running | passed | failed | flaky | aborted
    started_at: datetime | None = Field(
        default=None, sa_column=Column(TZDateTime(), nullable=True)
    )
    ended_at: datetime | None = Field(
        default=None, sa_column=Column(TZDateTime(), nullable=True)
    )
    duration_ms: int | None = None

    # Aggregated artifact paths (resolved relative to artifacts root)
    artifacts_dir: str | None = None
    report_html_path: str | None = None
    trace_jsonl_path: str | None = None

    # LLM usage rollup
    input_tokens: int = 0
    output_tokens: int = 0

    error_message: str | None = None
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )


class StepEvent(SQLModel, table=True):
    """One row per agent step (tool invocation or assertion)."""

    __tablename__ = "step_events"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    step_index: int

    event: str  # agent.step.executed | agent.assertion.evaluated | ...
    intent: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    tool_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    screenshot_before: str | None = None
    screenshot_after: str | None = None

    status: str = "ok"  # ok | failed
    latency_ms: int | None = None
    error_message: str | None = None

    occurred_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
