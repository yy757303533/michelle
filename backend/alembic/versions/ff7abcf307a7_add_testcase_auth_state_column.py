"""add testcase.auth_state column

Revision ID: ff7abcf307a7
Revises: 180af6cfd61b
Create Date: 2026-04-28 17:28:12.746306

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'ff7abcf307a7'
down_revision: Union[str, Sequence[str], None] = '180af6cfd61b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema. Existing rows get the default `logged-in` so the
    NOT NULL constraint is satisfiable; new inserts go through the model
    default which is the same value."""
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auth_state",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="logged-in",
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("test_cases", schema=None) as batch_op:
        batch_op.drop_column("auth_state")
