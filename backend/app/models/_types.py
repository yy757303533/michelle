"""Custom SQLAlchemy column types shared across models.

The platform's invariant is "all timestamps are UTC". SQLite has no native
timezone support — it round-trips datetimes as ISO text and SQLAlchemy
hands them back to Python as naive `datetime` objects, which then can't
be subtracted from `datetime.now(UTC)` without a TypeError. Pre-fix code
worked around this with ad-hoc `replace(tzinfo=UTC)` calls scattered at
arithmetic sites; this type pushes the conversion down to the column
boundary so application code can treat every persisted timestamp as
already UTC-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class TZDateTime(TypeDecorator):
    """`DateTime(timezone=True)` with one extra guarantee: values are
    *always* UTC-aware on read, even when the underlying dialect (SQLite)
    has no real timezone support and would otherwise return naive values.

    On write, naive values get tagged as UTC (we never store local time
    on this platform, so this is a sound assumption — any business code
    handing us a naive datetime is by convention UTC).
    On read, naive values get UTC re-attached.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
