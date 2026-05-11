"""add project.login_url

Revision ID: 2f41e0a9c8b7
Revises: a7c9d1e2f304
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f41e0a9c8b7"
down_revision: str | Sequence[str] | None = "a7c9d1e2f304"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "login_url",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("login_url")
