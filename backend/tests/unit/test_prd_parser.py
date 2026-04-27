"""Tests for the markdown PRD parser."""

from __future__ import annotations

from app.services.prd_parser import normalize_title, parse_prd


def test_parse_empty():
    p = parse_prd("")
    assert p.chapter_count() == 0
    assert p.title == "<empty>"


def test_parse_no_headings_only_preamble():
    p = parse_prd("Just some text\nMore text")
    assert p.chapter_count() == 0
    assert p.title == "<untitled>"
    assert "Just some text" in p.preamble


def test_parse_single_h1_no_chapters():
    p = parse_prd("# Title\n\nIntro text")
    assert p.title == "Title"
    assert p.chapter_count() == 0


def test_parse_h1_then_h2_chapters():
    md = """# My PRD

intro paragraph.

## 1. Goals

We want X and Y.

## 2. Scope

In scope: A.
Out of scope: B.
"""
    p = parse_prd(md)
    assert p.title == "My PRD"
    assert p.chapter_count() == 2
    assert p.chapters[0].title == "1. Goals"
    assert p.chapters[0].normalized_title == "goals"
    assert "We want X and Y" in p.chapters[0].body
    assert p.chapters[1].normalized_title == "scope"
    assert "In scope: A" in p.chapters[1].body


def test_parse_h3_subchapters_attached_to_h2():
    md = """# T

## A

a-body

### A.1

a1-body

## B

b-body
"""
    p = parse_prd(md)
    assert p.chapter_count() == 3
    titles = [c.normalized_title for c in p.chapters]
    assert titles == ["a", "a.1", "b"]


def test_normalize_title_strips_numbering():
    assert normalize_title("1. Foo") == "foo"
    assert normalize_title("1.2.3 Bar") == "bar"
    assert normalize_title("§ 3.4 Baz") == "baz"
    assert normalize_title("  Hello  ") == "hello"
    assert normalize_title("FOO BAR") == "foo bar"


def test_chapter_hash_stable_across_runs():
    md = "# T\n\n## Chap\n\nBody."
    p1 = parse_prd(md)
    p2 = parse_prd(md)
    assert p1.chapters[0].hash == p2.chapters[0].hash


def test_chapter_hash_changes_when_body_changes():
    a = parse_prd("# T\n\n## C\n\nbody1")
    b = parse_prd("# T\n\n## C\n\nbody2")
    assert a.chapters[0].hash != b.chapters[0].hash


def test_chapter_hash_unchanged_when_only_title_numbering_changes():
    """Renumbering a chapter shouldn't invalidate it."""
    a = parse_prd("# T\n\n## 1. Goals\n\nbody")
    b = parse_prd("# T\n\n## 2. Goals\n\nbody")
    # Same normalized title, same body → same hash
    assert a.chapters[0].normalized_title == b.chapters[0].normalized_title
    assert a.chapters[0].hash == b.chapters[0].hash


def test_frontmatter_is_stripped():
    md = """---
key: value
---
# T

## C

body
"""
    p = parse_prd(md)
    assert "key: value" in p.frontmatter
    assert p.title == "T"
    assert p.chapter_count() == 1


def test_real_michelle_prd_parses_into_many_chapters():
    """Smoke: feed Michelle's own PRD and expect a healthy chapter count."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    prd_path = repo / "docs" / "prd.md"
    if not prd_path.exists():
        return  # not in this checkout; skip silently
    md = prd_path.read_text(encoding="utf-8")
    p = parse_prd(md)
    assert p.chapter_count() >= 10  # at minimum the major sections
    titles = [c.normalized_title for c in p.chapters]
    assert any("概述" in t or "overview" in t for t in titles)
