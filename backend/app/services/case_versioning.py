"""Case versioning + diff-aware PRD re-generation logic.

Core invariants the review workflow relies on:

  1. **Approved cases are NEVER overwritten** by automated regeneration.
     A regen against a chapter that already has approved cases is a no-op for
     those cases (we still report what we saw, but don't blow them away).

  2. **Manually edited fields are NEVER overwritten**, even on pending cases.
     This is the contract that makes the "human in the loop" worth anything.
     `manual_edited_fields` on a TestCase row lists fields the human has touched.

  3. **Stale = the chapter that produced this case no longer exists**.
     Detected when re-uploading a PRD with chapters removed; the old cases are
     not deleted, just marked `review_status="stale"` so the review UI can
     filter them and the human decides whether to keep or reject.

  4. **Diff-aware regen runs LLM only for added + modified chapters**.
     Unchanged chapters are skipped; their existing cases stay as-is.
     Modified chapters: existing pending cases are version-bumped (new row,
     prev_version_id chain), but approved/edited fields carry forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import PRD, Project, TestCase
from app.obs import get_logger
from app.services.prd_diff import diff_prds
from app.services.prd_parser import Chapter, ParsedPRD

_log = get_logger(__name__)


@dataclass
class ChapterDecision:
    chapter_index: int
    title: str
    action: str  # "regenerate" | "skip_unchanged" | "mark_stale" | "skip_all_approved"
    reason: str
    existing_case_ids: list[str]


def _chapter_signature(c: Chapter | dict[str, Any]) -> str:
    """generated_from = chapter:<normalized_title>#<position> (matches case_generator.py)."""
    if isinstance(c, dict):
        return f"chapter:{c['normalized_title']}#{c['position']}"
    return f"chapter:{c.normalized_title}#{c.position}"


async def _cases_from_chapter(
    session: AsyncSession, project_id: str, signature: str
) -> list[TestCase]:
    rows = (
        await session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .where(TestCase.generated_from == signature)
        )
    ).scalars().all()
    return list(rows)


async def plan_regeneration(
    *,
    session: AsyncSession,
    new_prd: PRD,
    prev_prd: PRD | None,
    chapter_indices: list[int] | None = None,
) -> list[ChapterDecision]:
    """Return one ChapterDecision per requested (or all) chapter on the NEW prd.

    Decisions:
      - "regenerate"           - generate fresh cases for this chapter
      - "skip_unchanged"       - prev had it w/ same hash, do nothing
      - "skip_all_approved"    - chapter has any approved case, don't blow away
      - "mark_stale"           - returned for chapters in PREV but not NEW
    """
    new_chapters = [Chapter(**c) for c in new_prd.chapters]
    if chapter_indices is None:
        chapter_indices = list(range(len(new_chapters)))
    indices = [i for i in chapter_indices if 0 <= i < len(new_chapters)]

    decisions: list[ChapterDecision] = []

    # No previous version → everything is "regenerate" (no chapters to mark stale)
    if prev_prd is None:
        for idx in indices:
            chap = new_chapters[idx]
            existing = await _cases_from_chapter(
                session, new_prd.project_id, _chapter_signature(chap)
            )
            if any(c.review_status == "approved" for c in existing):
                decisions.append(
                    ChapterDecision(
                        chapter_index=idx,
                        title=chap.title,
                        action="skip_all_approved",
                        reason="chapter already has approved cases",
                        existing_case_ids=[c.case_id for c in existing],
                    )
                )
            else:
                decisions.append(
                    ChapterDecision(
                        chapter_index=idx,
                        title=chap.title,
                        action="regenerate",
                        reason="first generation for this chapter (no prev PRD)",
                        existing_case_ids=[c.case_id for c in existing],
                    )
                )
        return decisions

    # Compare new vs prev
    prev_chapters = [Chapter(**c) for c in prev_prd.chapters]
    prev_parsed = ParsedPRD(
        title=prev_prd.name,
        frontmatter="",
        preamble="",
        chapters=prev_chapters,
        raw_hash=prev_prd.content_hash,
    )
    new_parsed = ParsedPRD(
        title=new_prd.name,
        frontmatter="",
        preamble="",
        chapters=new_chapters,
        raw_hash=new_prd.content_hash,
    )
    diff = diff_prds(prev_parsed, new_parsed)

    # Build a (level, normalized_title) → status lookup
    delta_by_key: dict[tuple[int, str], str] = {}
    for d in diff.deltas:
        key_ch = d.new or d.old
        if key_ch is None:
            continue
        delta_by_key[(key_ch.level, key_ch.normalized_title)] = d.status

    for idx in indices:
        chap = new_chapters[idx]
        sig = _chapter_signature(chap)
        existing = await _cases_from_chapter(session, new_prd.project_id, sig)

        # Approved cases? leave alone regardless of diff status
        if any(c.review_status == "approved" for c in existing):
            decisions.append(
                ChapterDecision(
                    chapter_index=idx,
                    title=chap.title,
                    action="skip_all_approved",
                    reason="chapter has approved cases — never auto-regenerate",
                    existing_case_ids=[c.case_id for c in existing],
                )
            )
            continue

        delta = delta_by_key.get((chap.level, chap.normalized_title))
        if delta == "unchanged" or delta == "moved":
            decisions.append(
                ChapterDecision(
                    chapter_index=idx,
                    title=chap.title,
                    action="skip_unchanged",
                    reason=f"chapter {delta} vs prev PRD",
                    existing_case_ids=[c.case_id for c in existing],
                )
            )
        elif delta in {"added", "modified", None}:
            decisions.append(
                ChapterDecision(
                    chapter_index=idx,
                    title=chap.title,
                    action="regenerate",
                    reason=f"chapter {delta or 'untracked'} → regenerate",
                    existing_case_ids=[c.case_id for c in existing],
                )
            )
        else:
            decisions.append(
                ChapterDecision(
                    chapter_index=idx,
                    title=chap.title,
                    action="regenerate",
                    reason=f"unknown diff status {delta!r} → regenerate",
                    existing_case_ids=[c.case_id for c in existing],
                )
            )

    return decisions


async def mark_stale_for_removed_chapters(
    *,
    session: AsyncSession,
    new_prd: PRD,
    prev_prd: PRD | None,
) -> list[str]:
    """When a chapter present in prev_prd is gone in new_prd, mark its cases stale.

    Skips approved cases (never silently change human-confirmed state) and
    cases already at status="stale".

    Returns list of case_ids touched.
    """
    if prev_prd is None:
        return []

    new_keys = {(c["level"], c["normalized_title"]) for c in new_prd.chapters}
    removed_chapters = [
        Chapter(**c)
        for c in prev_prd.chapters
        if (c["level"], c["normalized_title"]) not in new_keys
    ]
    if not removed_chapters:
        return []

    touched: list[str] = []
    for chap in removed_chapters:
        sig = _chapter_signature(chap)
        existing = await _cases_from_chapter(session, new_prd.project_id, sig)
        for c in existing:
            if c.review_status in {"approved", "stale"}:
                continue
            c.review_status = "stale"
            touched.append(c.case_id)
    if touched:
        await session.commit()
        _log.info(
            "prd.regenerate.marked_stale",
            project_id=new_prd.project_id,
            count=len(touched),
            removed_chapters=len(removed_chapters),
        )
    return touched


async def find_prev_prd(
    session: AsyncSession, current: PRD
) -> PRD | None:
    """Resolve the immediate predecessor PRD via prev_version_id."""
    if not current.prev_version_id:
        return None
    return await session.get(PRD, current.prev_version_id)
