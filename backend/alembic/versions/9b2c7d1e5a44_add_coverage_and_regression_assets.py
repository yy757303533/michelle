"""add coverage-first and regression asset models

Revision ID: 9b2c7d1e5a44
Revises: casefb20260511
Create Date: 2026-05-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "9b2c7d1e5a44"
down_revision: str | Sequence[str] | None = "casefb20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_generation_jobs",
        sa.Column("job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prd_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("total_chapters", sa.Integer(), nullable=False),
        sa.Column("completed_chapters", sa.Integer(), nullable=False),
        sa.Column("requirements_created", sa.Integer(), nullable=False),
        sa.Column("coverage_created", sa.Integer(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
    )
    with op.batch_alter_table("design_generation_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_design_generation_jobs_prd_id"), ["prd_id"])
        batch_op.create_index(batch_op.f("ix_design_generation_jobs_project_id"), ["project_id"])
        batch_op.create_index(
            "ix_design_generation_one_active_per_prd",
            ["prd_id"],
            unique=True,
            sqlite_where=sa.text("status in ('pending', 'running')"),
            postgresql_where=sa.text("status in ('pending', 'running')"),
        )

    op.create_table(
        "requirement_items",
        sa.Column("requirement_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prd_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("chapter_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("evidence", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("requirement_id"),
    )
    with op.batch_alter_table("requirement_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_requirement_items_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_requirement_items_prd_id"), ["prd_id"])
        batch_op.create_index(
            batch_op.f("ix_requirement_items_chapter_index"),
            ["chapter_index"],
        )
        batch_op.create_index(batch_op.f("ix_requirement_items_status"), ["status"])

    op.create_table(
        "coverage_items",
        sa.Column("coverage_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prd_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("requirement_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("risk_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("coverage_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scenario", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("rationale", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("priority", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("review_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("linked_case_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("coverage_id"),
    )
    with op.batch_alter_table("coverage_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_coverage_items_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_coverage_items_prd_id"), ["prd_id"])
        batch_op.create_index(
            batch_op.f("ix_coverage_items_requirement_id"),
            ["requirement_id"],
        )
        batch_op.create_index(batch_op.f("ix_coverage_items_chapter_index"), ["chapter_index"])
        batch_op.create_index(batch_op.f("ix_coverage_items_review_status"), ["review_status"])
        batch_op.create_index(batch_op.f("ix_coverage_items_linked_case_id"), ["linked_case_id"])

    op.create_table(
        "regression_assets",
        sa.Column("asset_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("case_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("source_run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("action_plan", sa.JSON(), nullable=True),
        sa.Column("locator_candidates", sa.JSON(), nullable=True),
        sa.Column("assertions", sa.JSON(), nullable=True),
        sa.Column("last_replay_run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("last_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    with op.batch_alter_table("regression_assets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_regression_assets_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_regression_assets_case_id"), ["case_id"])
        batch_op.create_index(batch_op.f("ix_regression_assets_source_run_id"), ["source_run_id"])
        batch_op.create_index(batch_op.f("ix_regression_assets_status"), ["status"])
        batch_op.create_index(
            batch_op.f("ix_regression_assets_last_replay_run_id"),
            ["last_replay_run_id"],
        )

    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("coverage_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_test_cases_coverage_id"), ["coverage_id"])

    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("asset_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "execution_mode",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="agentic",
            )
        )
        batch_op.create_index(batch_op.f("ix_runs_asset_id"), ["asset_id"])
        batch_op.create_index(batch_op.f("ix_runs_execution_mode"), ["execution_mode"])

    with op.batch_alter_table("diagnoses", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("asset_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "feedback_target",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(batch_op.f("ix_diagnoses_asset_id"), ["asset_id"])


def downgrade() -> None:
    with op.batch_alter_table("design_generation_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_design_generation_one_active_per_prd")
        batch_op.drop_index(batch_op.f("ix_design_generation_jobs_project_id"))
        batch_op.drop_index(batch_op.f("ix_design_generation_jobs_prd_id"))
    op.drop_table("design_generation_jobs")

    with op.batch_alter_table("diagnoses", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diagnoses_asset_id"))
        batch_op.drop_column("feedback_target")
        batch_op.drop_column("asset_id")

    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_runs_execution_mode"))
        batch_op.drop_index(batch_op.f("ix_runs_asset_id"))
        batch_op.drop_column("execution_mode")
        batch_op.drop_column("asset_id")

    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_test_cases_coverage_id"))
        batch_op.drop_column("coverage_id")

    with op.batch_alter_table("regression_assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_regression_assets_last_replay_run_id"))
        batch_op.drop_index(batch_op.f("ix_regression_assets_status"))
        batch_op.drop_index(batch_op.f("ix_regression_assets_source_run_id"))
        batch_op.drop_index(batch_op.f("ix_regression_assets_case_id"))
        batch_op.drop_index(batch_op.f("ix_regression_assets_project_id"))
    op.drop_table("regression_assets")

    with op.batch_alter_table("coverage_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_coverage_items_linked_case_id"))
        batch_op.drop_index(batch_op.f("ix_coverage_items_review_status"))
        batch_op.drop_index(batch_op.f("ix_coverage_items_chapter_index"))
        batch_op.drop_index(batch_op.f("ix_coverage_items_requirement_id"))
        batch_op.drop_index(batch_op.f("ix_coverage_items_prd_id"))
        batch_op.drop_index(batch_op.f("ix_coverage_items_project_id"))
    op.drop_table("coverage_items")

    with op.batch_alter_table("requirement_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_requirement_items_status"))
        batch_op.drop_index(batch_op.f("ix_requirement_items_chapter_index"))
        batch_op.drop_index(batch_op.f("ix_requirement_items_prd_id"))
        batch_op.drop_index(batch_op.f("ix_requirement_items_project_id"))
    op.drop_table("requirement_items")
