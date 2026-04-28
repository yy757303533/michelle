"""switch datetime columns to TZDateTime (UTC-aware)

Revision ID: 180af6cfd61b
Revises: a124cb417b9d
Create Date: 2026-04-28 17:18:36.933183

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '180af6cfd61b'
down_revision: Union[str, Sequence[str], None] = 'a124cb417b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Schema-level no-op.

    Switched all datetime columns to a custom `TZDateTime` TypeDecorator
    that re-attaches UTC on read. SQLite stores datetimes as TEXT
    regardless of `timezone=True`, so there's no DDL change — the conversion
    happens in Python at the SA boundary. We keep this revision as a
    bookmark so the alembic head reflects the ORM change, and so anyone
    reviewing migrations sees that the version bumped intentionally.

    Existing rows are not touched: their stored ISO strings are still
    parseable, and the TypeDecorator's `process_result_value` re-attaches
    UTC on read.
    """
    pass


def downgrade() -> None:
    """Equally a no-op — going back to plain DateTime is a code-side change."""
    pass
