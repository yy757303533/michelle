"""add testcase quality metadata

Revision ID: e5b7a29d4c13
Revises: d13a5f7c91b2
Create Date: 2026-05-08 15:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b7a29d4c13"
down_revision: str | Sequence[str] | None = "d13a5f7c91b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quality", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.drop_column("quality")
