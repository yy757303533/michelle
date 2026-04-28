"""Day 4 dogfood: feed Michelle's own PRD and generate test cases.

This is the moment Michelle's user-facing pipeline runs against the project
that defines it — proof that the platform produces real, schema-valid cases
from a real PRD.

Usage:
    cd backend && uv run python ../scripts/day4_dogfood.py [chapter_idx ...]

If no chapter indices are given, generates for chapters 5-7 by default
(those describe the user-facing main flow — small enough to run quickly).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import async_session_maker, init_db  # noqa: E402
from app.models import Project  # noqa: E402
from app.obs import setup_logging  # noqa: E402
from app.services.case_generator import generate_cases_for_chapter  # noqa: E402
from app.services.prd_parser import parse_prd  # noqa: E402


async def main(chapter_indices: list[int]) -> int:
    setup_logging()
    await init_db()

    prd_path = ROOT / "docs" / "prd.md"
    md = prd_path.read_text(encoding="utf-8")
    parsed = parse_prd(md)

    print(f"==> PRD: {prd_path.relative_to(ROOT)}")
    print(f"==> title: {parsed.title}")
    print(f"==> {parsed.chapter_count()} chapters detected")
    print()

    if not chapter_indices:
        # Default: pick the user-facing main flow chapters
        # These map to "核心场景" + "功能需求" sections of Michelle's own PRD
        default = [c.position for c in parsed.chapters if c.level == 2][:3]
        chapter_indices = default
        print(f"==> using default chapter indices: {chapter_indices}")

    project_id = "michelle"
    project_name = "Michelle"
    base_url = "http://localhost:5000/"

    async with async_session_maker() as session:
        proj = await session.get(Project, project_id)
        if proj is None:
            proj = Project(
                project_id=project_id,
                name=project_name,
                base_url=base_url,
                description="The platform itself - dogfooding source.",
            )
            session.add(proj)
            await session.commit()

        all_saved = []
        for idx in chapter_indices:
            if idx < 0 or idx >= len(parsed.chapters):
                print(f"!! skipping out-of-range chapter idx {idx}")
                continue
            chap = parsed.chapters[idx]
            print(f"\n==> generating for chapter [{idx}] {chap.title!r} ({len(chap.body)} chars)")
            try:
                saved, batch = await generate_cases_for_chapter(
                    project_id=project_id,
                    project_name=project_name,
                    base_url=base_url,
                    chapter=chap,
                    session=session,
                    max_cases=4,
                )
                print(f"    coverage_notes: {batch.coverage_notes[:120]!r}")
                print(f"    saved {len(saved)} cases:")
                for c in saved:
                    print(
                        f"      • {c.case_id} [{c.priority}] {c.name}"
                        f" — {len(c.steps)} steps, {len(c.assertions)} assertions"
                    )
                all_saved.extend(saved)
            except Exception as exc:
                print(f"    FAILED: {exc}")

    print()
    print(f"==> dogfood total: {len(all_saved)} cases generated")
    print(f"==> see them at http://localhost:5173/cases (run `make dev`)")

    sample_path = ROOT / "docs" / "day4-dogfood-sample.json"
    sample_path.write_text(
        json.dumps(
            [
                {
                    "case_id": c.case_id,
                    "name": c.name,
                    "intent": c.intent,
                    "module": c.module,
                    "tags": c.tags,
                    "priority": c.priority,
                    "preconditions": c.preconditions,
                    "steps": c.steps,
                    "assertions": c.assertions,
                    "prompt_version": c.prompt_version,
                    "model_version": c.model_version,
                    "generated_from": c.generated_from,
                }
                for c in all_saved
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"==> sample written to {sample_path.relative_to(ROOT)}")

    return 0 if len(all_saved) >= 8 else 1


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:]]
    sys.exit(asyncio.run(main(args)))
