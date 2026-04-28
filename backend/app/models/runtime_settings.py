"""Runtime-mutable platform settings.

Knobs that the operator wants to tune without restarting the backend live
here. Each row is one knob keyed by `key`. We keep values as TEXT and
coerce in the reader so adding a knob doesn't need a schema migration.

Currently only `max_concurrent_runs` lives here, but the table shape is
intentionally generic so the next knob (e.g. default run timeout, default
diagnoser provider) is one INSERT away."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RuntimeSetting(SQLModel, table=True):
    __tablename__ = "runtime_settings"

    key: str = Field(primary_key=True, max_length=100)
    value: str
    updated_at: datetime = Field(default_factory=_utcnow)
