"""retired testcase generation job id

Revision ID: a7c9d1e2f304
Revises: f6a7b8c9d012
Create Date: 2026-05-08 17:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a7c9d1e2f304"
down_revision: str | Sequence[str] | None = "f6a7b8c9d012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op; direct PRD case generation jobs were retired."""


def downgrade() -> None:
    """No-op; direct PRD case generation jobs were retired."""
