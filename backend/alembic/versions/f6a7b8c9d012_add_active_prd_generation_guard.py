"""add active prd generation guard

Revision ID: f6a7b8c9d012
Revises: e5b7a29d4c13
Create Date: 2026-05-08 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d012"
down_revision: str | None = "e5b7a29d4c13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE prd_generation_jobs
            SET
                status = 'failed',
                error = 'superseded by newer active generation job during migration',
                finished_at = CURRENT_TIMESTAMP
            WHERE status in ('pending', 'running')
              AND job_id NOT IN (
                SELECT job_id
                FROM (
                    SELECT
                        job_id,
                        row_number() OVER (
                            PARTITION BY prd_id
                            ORDER BY created_at DESC, job_id DESC
                        ) AS rn
                    FROM prd_generation_jobs
                    WHERE status in ('pending', 'running')
                ) ranked
                WHERE rn = 1
              )
            """
        )
    )
    op.create_index(
        "ix_prd_generation_one_active_per_prd",
        "prd_generation_jobs",
        ["prd_id"],
        unique=True,
        postgresql_where=sa.text("status in ('pending', 'running')"),
        sqlite_where=sa.text("status in ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prd_generation_one_active_per_prd",
        table_name="prd_generation_jobs",
        postgresql_where=sa.text("status in ('pending', 'running')"),
        sqlite_where=sa.text("status in ('pending', 'running')"),
    )
