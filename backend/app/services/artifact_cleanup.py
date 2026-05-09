"""Artifacts retention cleanup for run directories."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app import storage
from app.models import Run

TERMINAL_STATUSES = {"passed", "failed", "flaky", "aborted"}


@dataclass
class CleanupCandidate:
    run_id: str
    project_id: str
    status: str
    ended_at: str | None
    path: str
    bytes: int
    files: int


@dataclass
class CleanupResult:
    retention_days: int
    dry_run: bool
    cutoff: str
    candidates: list[CleanupCandidate]
    deleted_runs: int = 0
    deleted_bytes: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "retention_days": self.retention_days,
            "dry_run": self.dry_run,
            "cutoff": self.cutoff,
            "candidate_runs": len(self.candidates),
            "candidate_bytes": sum(c.bytes for c in self.candidates),
            "deleted_runs": self.deleted_runs,
            "deleted_bytes": self.deleted_bytes,
            "errors": self.errors or [],
            "candidates": [c.__dict__ for c in self.candidates[:200]],
        }


async def cleanup_artifacts(
    *,
    session: AsyncSession,
    retention_days: int,
    dry_run: bool,
) -> CleanupResult:
    cutoff_dt = datetime.now(UTC) - timedelta(days=retention_days)
    stmt = select(Run).where(Run.status.in_(TERMINAL_STATUSES)).where(Run.created_at < cutoff_dt)
    rows = (await session.execute(stmt)).scalars().all()

    candidates: list[CleanupCandidate] = []
    for run in rows:
        if run.status not in TERMINAL_STATUSES:
            continue
        marker = run.ended_at or run.created_at
        if marker >= cutoff_dt:
            continue
        path = _safe_run_dir(run)
        if path is None or not path.is_dir():
            continue
        size, files = _dir_stats(path)
        candidates.append(
            CleanupCandidate(
                run_id=run.run_id,
                project_id=run.project_id,
                status=run.status,
                ended_at=run.ended_at.isoformat() if run.ended_at else None,
                path=str(path),
                bytes=size,
                files=files,
            )
        )

    result = CleanupResult(
        retention_days=retention_days,
        dry_run=dry_run,
        cutoff=cutoff_dt.isoformat(),
        candidates=candidates,
        errors=[],
    )
    if dry_run:
        return result

    for candidate in candidates:
        path = Path(candidate.path)
        try:
            shutil.rmtree(path)
            result.deleted_runs += 1
            result.deleted_bytes += candidate.bytes
        except OSError as exc:
            result.errors = result.errors or []
            result.errors.append(f"{candidate.run_id}: {exc}")
    return result


def _safe_run_dir(run: Run) -> Path | None:
    expected = (storage.artifacts_root() / run.project_id / run.run_id).resolve()
    raw = Path(run.artifacts_dir).resolve() if run.artifacts_dir else expected
    try:
        raw.relative_to(expected)
    except ValueError:
        raw = expected
    return raw


def _dir_stats(path: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                files += 1
                total += item.stat().st_size
        except OSError:
            continue
    return total, files
