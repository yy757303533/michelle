"""add testcase generation job id

Revision ID: a7c9d1e2f304
Revises: f6a7b8c9d012
Create Date: 2026-05-08 17:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9d1e2f304"
down_revision: str | Sequence[str] | None = "f6a7b8c9d012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("generation_job_id", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_test_cases_generation_job_id",
            ["generation_job_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.drop_index("ix_test_cases_generation_job_id")
        batch_op.drop_column("generation_job_id")
