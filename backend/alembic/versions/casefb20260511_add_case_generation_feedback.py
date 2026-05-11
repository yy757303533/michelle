"""add case generation feedback

Revision ID: casefb20260511
Revises: 2f41e0a9c8b7
Create Date: 2026-05-11 21:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "casefb20260511"
down_revision: str | Sequence[str] | None = "2f41e0a9c8b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_generation_feedback",
        sa.Column("feedback_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("generated_from", sa.String(), nullable=True),
        sa.Column("generation_job_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
        sa.Column("evidence", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("resolved_by_commit", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("feedback_id"),
    )
    for column in (
        "case_id",
        "project_id",
        "generated_from",
        "generation_job_id",
        "category",
        "status",
    ):
        op.create_index(f"ix_case_generation_feedback_{column}", "case_generation_feedback", [column])


def downgrade() -> None:
    for column in (
        "status",
        "category",
        "generation_job_id",
        "generated_from",
        "project_id",
        "case_id",
    ):
        op.drop_index(f"ix_case_generation_feedback_{column}", table_name="case_generation_feedback")
    op.drop_table("case_generation_feedback")
