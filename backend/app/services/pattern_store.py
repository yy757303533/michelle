"""Sediment pattern library — Day 11.

When a human confirms a diagnosis, fold its signature into the Pattern table.
Future failures are matched against accumulated patterns and surfaced as
"we've seen this before — last fix worked: ...".

This is the practical core of compound engineering: every confirmed failure
makes the next diagnosis cheaper.

Schema (already in models/pattern.py):
  pattern_id (uuid)
  pattern_type      → category from Diagnosis (real_bug | flaky | ...)
  title             → short description
  description       → long-form
  matcher           → JSON with extracted features:
                        { "tool": "browser_click",
                          "intent_keywords": ["登录"],
                          "error_keywords": ["timeout"], ... }
  suggested_action  → carries the human-confirmed fix_suggestion
  hit_count         → bumps every time matched
  confirmed_by_diag_ids
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import Diagnosis, Pattern, Run, StepEvent
from app.obs import EVENTS, get_logger

_log = get_logger(__name__)

_STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "in",
    "on",
    "to",
    "of",
    "and",
    "or",
    "for",
    "with",
    "at",
    "by",
    "this",
    "that",
    "it",
    "be",
    "as",
}
_KEYWORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[一-鿿]{2,}")


# ── Public API ─────────────────────────────────────────────────────────────


async def absorb_diagnosis(*, diag: Diagnosis, session: AsyncSession) -> Pattern:
    """Called when a human confirms a diagnosis. Either merges into an existing
    pattern (matcher-equivalent) or creates a new one.

    Returns the persisted Pattern row.
    """
    matcher = await _extract_matcher(diag=diag, session=session)
    title = (diag.fix_suggestion or diag.reasoning or diag.category)[:120]

    existing = await _find_equivalent(matcher=matcher, category=diag.category, session=session)
    now = datetime.now(UTC)

    if existing is not None:
        existing.hit_count += 1
        existing.last_hit_at = now
        existing.updated_at = now
        prev = list(existing.confirmed_by_diag_ids or [])
        if diag.diag_id not in prev:
            prev.append(diag.diag_id)
            existing.confirmed_by_diag_ids = prev
        # Merge new matcher fields (union keywords)
        existing.matcher = _merge_matchers(existing.matcher or {}, matcher)
        await session.commit()
        _log.info(
            "pattern.absorbed_existing",
            pattern_id=existing.pattern_id,
            diag_id=diag.diag_id,
            hit_count=existing.hit_count,
        )
        return existing

    new = Pattern(
        pattern_id="pat_" + uuid4().hex[:12],
        pattern_type=diag.category,
        title=title,
        description=(diag.reasoning or "")[:1000],
        matcher=matcher,
        suggested_action=(diag.fix_suggestion or "")[:500],
        hit_count=1,
        last_hit_at=now,
        confirmed_by_diag_ids=[diag.diag_id],
    )
    session.add(new)
    await session.commit()
    _log.info(
        "pattern.absorbed_new",
        pattern_id=new.pattern_id,
        diag_id=diag.diag_id,
        category=diag.category,
    )
    return new


async def find_matches_for_run(
    *, run_id: str, session: AsyncSession, top_n: int = 3
) -> list[Pattern]:
    """For a (typically failed) Run, return Patterns whose matcher is consistent
    with the failure signature, sorted by score desc.

    Cheap: extracts a candidate matcher from the run, then scores existing
    patterns by token overlap. No LLM call.
    """
    run = await session.get(Run, run_id)
    if run is None:
        return []
    steps = (
        (
            await session.execute(
                select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    candidate = _matcher_from_run(run=run, steps=list(steps))

    patterns = (await session.execute(select(Pattern))).scalars().all()
    if not patterns:
        return []

    scored: list[tuple[float, Pattern]] = []
    for p in patterns:
        score = _score(candidate, p.matcher or {})
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)

    matched = [p for _, p in scored[:top_n]]
    for p in matched:
        p.hit_count += 1
        p.last_hit_at = datetime.now(UTC)
        _log.info(EVENTS.PATTERN_MATCHED.name, pattern_id=p.pattern_id, pattern_type=p.pattern_type)
    if matched:
        await session.commit()
    return matched


# ── Internals ──────────────────────────────────────────────────────────────


async def _extract_matcher(*, diag: Diagnosis, session: AsyncSession) -> dict[str, Any]:
    run = await session.get(Run, diag.run_id)
    if run is None:
        return {}
    steps = (
        (
            await session.execute(
                select(StepEvent)
                .where(StepEvent.run_id == diag.run_id)
                .order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )
    return _matcher_from_run(run=run, steps=list(steps), reasoning=diag.reasoning)


def _matcher_from_run(
    *, run: Run, steps: list[StepEvent], reasoning: str | None = None
) -> dict[str, Any]:
    failed = next((s for s in steps if s.status == "failed"), None) or (
        steps[-1] if steps else None
    )
    intent_text = ""
    error_text = run.error_message or ""
    tool = None
    if failed is not None:
        tool = failed.tool_name
        intent_text = failed.intent or ""
        error_text = (error_text + "\n" + (failed.error_message or "")).strip()

    blob = " ".join(filter(None, [intent_text, error_text, reasoning or ""]))
    return {
        "tool": tool,
        "intent_keywords": _keywords(intent_text)[:8],
        "error_keywords": _keywords(error_text)[:12],
        "blob_keywords": _keywords(blob)[:16],
    }


def _keywords(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _KEYWORD_RE.finditer(text.lower()):
        w = m.group()
        if w in _STOP_WORDS:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _merge_matchers(a: dict, b: dict) -> dict:
    out = dict(a)
    for k in ("intent_keywords", "error_keywords", "blob_keywords"):
        out[k] = sorted(set((a.get(k) or []) + (b.get(k) or [])))[:24]
    if not out.get("tool") and b.get("tool"):
        out["tool"] = b["tool"]
    return out


async def _find_equivalent(
    *, matcher: dict, category: str, session: AsyncSession
) -> Pattern | None:
    """Find a Pattern of the same category whose matcher is highly similar
    (Jaccard ≥ 0.5 across blob_keywords)."""
    rows = (
        (await session.execute(select(Pattern).where(Pattern.pattern_type == category)))
        .scalars()
        .all()
    )
    cand_kw = set(matcher.get("blob_keywords") or [])
    if not cand_kw:
        return None
    best: tuple[float, Pattern] | None = None
    for p in rows:
        kw = set((p.matcher or {}).get("blob_keywords") or [])
        if not kw:
            continue
        union = kw | cand_kw
        if not union:
            continue
        score = len(kw & cand_kw) / len(union)
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, p)
    return best[1] if best else None


def _score(candidate: dict, stored: dict) -> float:
    """Symmetric Jaccard on blob_keywords, with a small bonus when tools match."""
    a = set(candidate.get("blob_keywords") or [])
    b = set(stored.get("blob_keywords") or [])
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    score = len(a & b) / len(union)
    if candidate.get("tool") and stored.get("tool") and candidate["tool"] == stored["tool"]:
        score += 0.1
    return min(1.0, score)
