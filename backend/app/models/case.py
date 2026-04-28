"""TestCase model.

Stores AI-generated and human-edited test cases. Versions are immutable
(prev_version_id chains them); status transitions handled by the review API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestCase(SQLModel, table=True):
    __tablename__ = "test_cases"
    __test__ = False  # tell pytest this is a domain class, not a test class

    case_id: str = Field(primary_key=True, description="TC-YYYYMMDD-NNN")
    project_id: str = Field(index=True)

    name: str
    intent: str
    module: str = ""
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    priority: str = "P1"

    auth_state: str = "logged-in"
    """Auth precondition for this case. One of:
      - logged-in    feature behind login wall (most common)
      - logged-out   feature MUST run with no session (registration,
                     forgot password, login UI rendering)
      - wrong-creds  testing rejection / error message with bad creds
      - public       auth-irrelevant (marketing, docs, public search)

    The runtime executor reads this and skips auto-login for the last
    three. Validated on insert/edit by Pydantic Literal types in the
    API layer; persisted as a plain string for forward-compat (new
    states don't need a schema migration)."""

    preconditions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    steps: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    assertions: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # Provenance
    source: str = "ai-generated"  # ai-generated | manual | imported
    prompt_version: str | None = None
    model_version: str | None = None
    generated_from: str | None = None  # e.g. "prd:<prd_id>:chapter:<idx>"

    # Review
    review_status: str = "pending"  # pending | approved | rejected
    manual_edited_fields: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Versioning
    version: int = 1
    prev_version_id: str | None = None

    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
