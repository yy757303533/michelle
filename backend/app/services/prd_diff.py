"""Diff two parsed PRDs at chapter granularity.

Match key = (level, normalized_title). Bodies compared by hash.

  - both sides have the key, hashes match    → unchanged
  - both sides have the key, hashes differ   → modified
  - only old side has the key                → removed
  - only new side has the key                → added

We also surface chapter position changes (chapters reordered without content
change) as `moved`, but moves are not considered "stale" for case-regen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.prd_parser import Chapter, ParsedPRD


@dataclass
class ChapterDelta:
    status: str  # added | removed | modified | unchanged | moved
    new: Chapter | None = None
    old: Chapter | None = None

    def __post_init__(self):
        assert self.status in {"added", "removed", "modified", "unchanged", "moved"}

    @property
    def title(self) -> str:
        return (self.new or self.old).title  # type: ignore[union-attr]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "title": self.title,
            "new": self.new.to_dict() if self.new else None,
            "old": self.old.to_dict() if self.old else None,
        }


@dataclass
class PRDDiff:
    deltas: list[ChapterDelta] = field(default_factory=list)
    raw_unchanged: bool = False
    """True if the entire markdown body is byte-for-byte identical."""

    def chapters_to_regenerate(self) -> list[Chapter]:
        """Chapters whose cases should be re-generated on this upload."""
        return [d.new for d in self.deltas if d.status in {"added", "modified"} and d.new]

    def chapters_to_mark_stale(self) -> list[Chapter]:
        """Chapters whose existing cases should be marked stale (or removed)."""
        return [d.old for d in self.deltas if d.status == "removed" and d.old]

    def summary(self) -> dict[str, int]:
        out = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0, "moved": 0}
        for d in self.deltas:
            out[d.status] += 1
        return out


def diff_prds(old: ParsedPRD, new: ParsedPRD) -> PRDDiff:
    if old.raw_hash == new.raw_hash:
        # Byte-identical: every chapter is unchanged
        return PRDDiff(
            deltas=[ChapterDelta("unchanged", new=c, old=c) for c in new.chapters],
            raw_unchanged=True,
        )

    old_by_key: dict[tuple[int, str], Chapter] = {
        (c.level, c.normalized_title): c for c in old.chapters
    }
    deltas: list[ChapterDelta] = []

    seen_keys: set[tuple[int, str]] = set()

    # Walk new in order so output is reader-friendly
    for c in new.chapters:
        k = (c.level, c.normalized_title)
        seen_keys.add(k)
        old_c = old_by_key.get(k)
        if old_c is None:
            deltas.append(ChapterDelta("added", new=c))
        elif old_c.hash == c.hash:
            if old_c.position != c.position:
                deltas.append(ChapterDelta("moved", new=c, old=old_c))
            else:
                deltas.append(ChapterDelta("unchanged", new=c, old=old_c))
        else:
            deltas.append(ChapterDelta("modified", new=c, old=old_c))

    # Removed = in old but not in new
    for c in old.chapters:
        k = (c.level, c.normalized_title)
        if k not in seen_keys:
            deltas.append(ChapterDelta("removed", old=c))

    return PRDDiff(deltas=deltas, raw_unchanged=False)
