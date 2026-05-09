"""add step event phase

Revision ID: d13a5f7c91b2
Revises: c0ffee123456
Create Date: 2026-05-08 16:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "d13a5f7c91b2"
down_revision: str | Sequence[str] | None = "c0ffee123456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("step_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "phase",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="action",
            )
        )
        batch_op.create_index(batch_op.f("ix_step_events_phase"), ["phase"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("step_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_step_events_phase"))
        batch_op.drop_column("phase")
