"""Tests for chapter-level PRD diffing."""

from __future__ import annotations

from app.services.prd_diff import diff_prds
from app.services.prd_parser import parse_prd


def test_byte_identical_marked_unchanged():
    md = "# T\n\n## A\n\nbody-a\n\n## B\n\nbody-b"
    a = parse_prd(md)
    b = parse_prd(md)
    d = diff_prds(a, b)
    assert d.raw_unchanged is True
    s = d.summary()
    assert s["unchanged"] == 2
    assert s["modified"] == 0


def test_chapter_added():
    a = parse_prd("# T\n\n## A\n\nbody-a")
    b = parse_prd("# T\n\n## A\n\nbody-a\n\n## B\n\nbody-b")
    d = diff_prds(a, b)
    s = d.summary()
    assert s["added"] == 1
    assert s["unchanged"] == 1
    new_chapters = d.chapters_to_regenerate()
    assert len(new_chapters) == 1
    assert new_chapters[0].normalized_title == "b"


def test_chapter_removed():
    a = parse_prd("# T\n\n## A\n\nbody-a\n\n## B\n\nbody-b")
    b = parse_prd("# T\n\n## A\n\nbody-a")
    d = diff_prds(a, b)
    s = d.summary()
    assert s["removed"] == 1
    stale = d.chapters_to_mark_stale()
    assert len(stale) == 1
    assert stale[0].normalized_title == "b"


def test_chapter_modified():
    a = parse_prd("# T\n\n## A\n\nv1")
    b = parse_prd("# T\n\n## A\n\nv2")
    d = diff_prds(a, b)
    s = d.summary()
    assert s["modified"] == 1
    regen = d.chapters_to_regenerate()
    assert len(regen) == 1
    assert regen[0].normalized_title == "a"


def test_chapter_moved_not_marked_modified():
    a = parse_prd("# T\n\n## A\n\nbody-a\n\n## B\n\nbody-b")
    b = parse_prd("# T\n\n## B\n\nbody-b\n\n## A\n\nbody-a")
    d = diff_prds(a, b)
    s = d.summary()
    assert s["modified"] == 0
    assert s["moved"] == 2
    # Moved chapters do NOT need regeneration
    assert d.chapters_to_regenerate() == []


def test_renumbered_chapter_treated_as_unchanged():
    a = parse_prd("# T\n\n## 1. A\n\nbody")
    b = parse_prd("# T\n\n## 2. A\n\nbody")
    d = diff_prds(a, b)
    s = d.summary()
    assert s["unchanged"] == 1
    assert s["modified"] == 0
