"""add soft delete columns

Revision ID: 6d3f4a8b9c10
Revises: 9b2c7d1e5a44
Create Date: 2026-05-12 21:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d3f4a8b9c10"
down_revision: str | Sequence[str] | None = "9b2c7d1e5a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("prds", "coverage_items", "test_cases", "runs")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("deleted_by", sa.String(), nullable=False, server_default=""))
        op.add_column(table, sa.Column("delete_reason", sa.String(), nullable=False, server_default=""))
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])
        op.create_index(f"ix_{table}_deleted_by", table, ["deleted_by"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_deleted_by", table_name=table)
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_column(table, "delete_reason")
        op.drop_column(table, "deleted_by")
        op.drop_column(table, "deleted_at")
