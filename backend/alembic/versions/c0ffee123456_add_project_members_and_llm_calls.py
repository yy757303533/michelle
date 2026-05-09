"""add project members and llm calls

Revision ID: c0ffee123456
Revises: bb1a2c3d4e5f
Create Date: 2026-05-08 14:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

import app.models._types
from alembic import op

revision: str = "c0ffee123456"
down_revision: str | Sequence[str] | None = "bb1a2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", app.models._types.TZDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_project_members_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_project_members_user_id"), ["user_id"], unique=False)

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", app.models._types.TZDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_llm_calls_provider"), ["provider"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_llm_calls_prompt_version"), ["prompt_version"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_llm_calls_prompt_version"))
        batch_op.drop_index(batch_op.f("ix_llm_calls_provider"))
    op.drop_table("llm_calls")

    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_project_members_user_id"))
        batch_op.drop_index(batch_op.f("ix_project_members_project_id"))
    op.drop_table("project_members")
