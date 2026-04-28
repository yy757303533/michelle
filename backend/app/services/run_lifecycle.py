"""Process-level cleanup for runs that didn't get to finish.

When the backend dies mid-run (uvicorn --reload, SIGKILL, crash, OOM),
the `asyncio.Task` driving the run vanishes but the corresponding `Run`
DB row stays at `running` / `pending` forever — there's nobody left to
update it. Without intervention, the UI shows a perpetually-running
"ghost" and the case can't be rerun cleanly.

We handle this with two complementary cleanups, both wired into the
FastAPI lifespan:

  1. **Startup heal** (always runs): if a row is `running` or `pending`
     when a fresh process boots, by definition no current task owns it,
     so it's safe to declare aborted. Catches SIGKILL / crash / forced
     reload — the cases where shutdown handlers don't get to run.

  2. **Shutdown heal** (best effort): on graceful shutdown, mark
     in-flight runs aborted before the worker exits. Covers the
     normal Ctrl+C path so the user sees the right status without
     waiting for the next startup.

Both write the SAME error_message prefix so it's filterable; the
running task itself isn't cancelled here (asyncio handles that), we
only update the bookkeeping.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import select

from app import db as _db
from app.models import Run
from app.obs import get_logger

_log = get_logger(__name__)

_STALE_STATUSES = ("running", "pending")
_REASON_PREFIX = "auto-aborted"


async def heal_stale_runs(*, reason: str) -> int:
    """Mark any `running` or `pending` runs as `aborted`. Returns count.

    `reason` is appended to a stable prefix so the error_message column
    can be grepped/filtered later (e.g. to distinguish startup heals from
    real failures the heuristic classifier sees)."""
    async with _db.async_session_maker() as session:
        rows = (
            (await session.execute(select(Run).where(Run.status.in_(_STALE_STATUSES))))
            .scalars()
            .all()
        )
        if not rows:
            return 0
        now = datetime.now(UTC)
        for r in rows:
            r.status = "aborted"
            r.error_message = f"{_REASON_PREFIX}: {reason}"
            if not r.ended_at:
                r.ended_at = now
            if r.started_at and r.duration_ms is None:
                # `started_at` is always UTC-aware now thanks to the
                # `TZDateTime` column type — no naive/aware mismatch.
                r.duration_ms = int((now - r.started_at).total_seconds() * 1000)
        await session.commit()
        _log.info(
            "run.lifecycle.healed",
            count=len(rows),
            reason=reason,
            run_ids=[r.run_id for r in rows][:20],
        )
        return len(rows)
