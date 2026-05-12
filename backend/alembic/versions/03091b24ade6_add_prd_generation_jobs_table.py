"""retired prd_generation_jobs table

Revision ID: 03091b24ade6
Revises: ff7abcf307a7
Create Date: 2026-04-28 17:33:14.077978

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "03091b24ade6"
down_revision: str | Sequence[str] | None = "ff7abcf307a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retained only to preserve revision ordering for new databases."""


def downgrade() -> None:
    """No-op; the retired table is not created on new databases."""
