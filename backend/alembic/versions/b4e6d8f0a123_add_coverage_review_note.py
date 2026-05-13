"""add coverage review note

Revision ID: b4e6d8f0a123
Revises: 6d3f4a8b9c10
Create Date: 2026-05-13 11:55:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e6d8f0a123"
down_revision: str | Sequence[str] | None = "6d3f4a8b9c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coverage_items",
        sa.Column("review_note", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("coverage_items", "review_note")
