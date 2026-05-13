"""add diagnosis dev context

Revision ID: d9e0f1a2b345
Revises: c8f1e2d3a456
Create Date: 2026-05-13 16:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e0f1a2b345"
down_revision: str | Sequence[str] | None = "c8f1e2d3a456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diagnoses",
        sa.Column("evidence_pack", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "diagnoses",
        sa.Column("candidate_files", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("diagnoses", "candidate_files")
    op.drop_column("diagnoses", "evidence_pack")
