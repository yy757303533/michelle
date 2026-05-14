"""add diagnosis jobs

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b345
Create Date: 2026-05-13 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d9e0f1a2b345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_jobs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("include_dev_context", sa.Boolean(), nullable=False),
        sa.Column("overwrite_existing", sa.Boolean(), nullable=False),
        sa.Column("prefer_provider", sa.String(), nullable=False),
        sa.Column("diag_id", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_diagnosis_jobs_run_id", "diagnosis_jobs", ["run_id"])
    op.create_index("ix_diagnosis_jobs_project_id", "diagnosis_jobs", ["project_id"])
    op.create_index("ix_diagnosis_jobs_status", "diagnosis_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_diagnosis_jobs_status", table_name="diagnosis_jobs")
    op.drop_index("ix_diagnosis_jobs_project_id", table_name="diagnosis_jobs")
    op.drop_index("ix_diagnosis_jobs_run_id", table_name="diagnosis_jobs")
    op.drop_table("diagnosis_jobs")
