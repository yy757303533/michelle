"""add prd source ref

Revision ID: c8f1e2d3a456
Revises: b4e6d8f0a123
Create Date: 2026-05-13 15:45:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f1e2d3a456"
down_revision: str | Sequence[str] | None = "b4e6d8f0a123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prds",
        sa.Column("source_ref", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("prds", "source_ref")
