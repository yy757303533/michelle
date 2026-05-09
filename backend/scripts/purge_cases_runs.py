"""Purge generated cases and execution history for a fresh pilot run.

This intentionally keeps projects, PRDs, users, project memberships, audit
logs, runtime settings, and the shared Playwright MCP npm cache.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

from sqlalchemy import delete, func, select

from app.config import settings
from app.db import async_session_maker, init_db
from app.models.case import TestCase
from app.models.diagnosis import Diagnosis
from app.models.run import LLMCall, Run, StepEvent


async def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all cases and run history.")
    parser.add_argument("--yes", action="store_true", help="Required confirmation flag.")
    parser.add_argument(
        "--delete-artifacts",
        action="store_true",
        help="Also delete run artifact files while preserving .npm-cache.",
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to purge without --yes")

    await init_db()
    async with async_session_maker() as session:
        before = {
            "test_cases": await _count(session, TestCase),
            "runs": await _count(session, Run),
            "step_events": await _count(session, StepEvent),
            "diagnoses": await _count(session, Diagnosis),
            "llm_calls": await _count(session, LLMCall),
        }

        # Delete children first for databases with FK checks enabled.
        await session.execute(delete(StepEvent))
        await session.execute(delete(Diagnosis))
        await session.execute(delete(LLMCall))
        await session.execute(delete(Run))
        await session.execute(delete(TestCase))
        await session.commit()

        after = {
            "test_cases": await _count(session, TestCase),
            "runs": await _count(session, Run),
            "step_events": await _count(session, StepEvent),
            "diagnoses": await _count(session, Diagnosis),
            "llm_calls": await _count(session, LLMCall),
        }

    artifact_result = None
    if args.delete_artifacts:
        artifact_result = _purge_artifacts(settings.artifacts_path)

    print({"before": before, "after": after, "artifacts": artifact_result})


async def _count(session, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


def _purge_artifacts(root: Path) -> dict[str, int | str]:
    root.mkdir(parents=True, exist_ok=True)
    deleted_files = 0
    deleted_dirs = 0
    preserved = {".npm-cache", ".mcp-probe", "bootstrap-admin.txt"}
    for child in root.iterdir():
        if child.name in preserved:
            continue
        if child.is_dir():
            shutil.rmtree(child)
            deleted_dirs += 1
        else:
            child.unlink()
            deleted_files += 1
    return {
        "root": str(root.resolve()),
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
    }


if __name__ == "__main__":
    asyncio.run(main())
