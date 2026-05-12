"""retired active prd generation guard

Revision ID: f6a7b8c9d012
Revises: e5b7a29d4c13
Create Date: 2026-05-08 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f6a7b8c9d012"
down_revision: str | None = "e5b7a29d4c13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op; PRD generation jobs were retired."""


def downgrade() -> None:
    """No-op; PRD generation jobs were retired."""
