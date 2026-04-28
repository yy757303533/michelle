"""Parse markdown PRDs into chapters with stable hashes.

A "chapter" is everything between two `##` (or `###`) headings, plus the heading itself.

Stable identity = (level, normalized_title). Normalization:
  - lowercase
  - collapse whitespace
  - strip leading numbering ("1. " / "1.1 " / "§5.3 " / "## ")
  - strip leading/trailing punctuation

This lets a chapter survive minor edits to its title (capitalization,
numbering, trailing punctuation). Body changes are detected via content_hash.

Skipped:
  - code fences inside the body are kept verbatim — they don't get re-parsed
  - YAML frontmatter (between leading `---` lines) is captured separately as
    metadata
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

# Headings we treat as chapter boundaries. Top-level (#) is the document title.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_NUMBERING_RE = re.compile(r"^(?:[#§]+\s*)?(?:\d+(?:\.\d+)*\.?\s+)?(?:[一-鿿]?\s*)?")


@dataclass
class Chapter:
    level: int  # 1, 2, or 3
    title: str  # raw title text, as it appears in the source
    normalized_title: str
    body: str  # everything from the line AFTER the heading to the start of the next chapter
    hash: str  # SHA-256 of (level + normalized_title + body)
    position: int  # 0-based index in document order

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedPRD:
    title: str
    frontmatter: str
    preamble: str  # text between frontmatter and first heading
    chapters: list[Chapter]
    raw_hash: str

    def chapter_count(self) -> int:
        return len(self.chapters)


def normalize_title(title: str) -> str:
    """Strip numbering, punctuation, lowercase, collapse spaces."""
    s = title.strip()
    # Drop leading numbering like "1. " / "1.1.2 " / "§ 3.4 "
    while True:
        m = re.match(r"^([§#]+\s*|\d+(?:\.\d+)*\.?\s+|[一-鿿]\.\s*)", s)
        if not m:
            break
        s = s[m.end() :]
    # Lowercase + collapse spaces
    s = re.sub(r"\s+", " ", s.lower()).strip()
    # Strip surrounding punctuation. `.strip()` is character-set, so each
    # codepoint counts individually — `「」` etc are listed once each.
    _PUNCT = ".,;:—-–·•(){}[]<>「」『』"
    s = s.strip(_PUNCT)
    return s


def _hash_chapter(level: int, normalized_title: str, body: str) -> str:
    h = hashlib.sha256()
    h.update(f"{level}|{normalized_title}|".encode())
    h.update(body.encode("utf-8"))
    return h.hexdigest()


def parse_prd(markdown: str) -> ParsedPRD:
    """Parse a markdown PRD into a ParsedPRD.

    Document title is taken from the first H1 (`# ...`) heading; if absent,
    `<untitled>`. Chapters start at H2 (`##`).
    """
    if not markdown:
        return ParsedPRD(
            title="<empty>",
            frontmatter="",
            preamble="",
            chapters=[],
            raw_hash=hashlib.sha256(b"").hexdigest(),
        )

    text = markdown
    fm = ""
    if m := _FRONTMATTER_RE.match(text):
        fm = m.group(1)
        text = text[m.end() :]

    # Find all H1/H2/H3 positions
    headings = []
    for m in _HEADING_RE.finditer(text):
        level = len(m.group(1))
        headings.append((m.start(), m.end(), level, m.group(2).strip()))

    title = "<untitled>"
    preamble = ""
    chapters: list[Chapter] = []

    if not headings:
        return ParsedPRD(
            title=title,
            frontmatter=fm,
            preamble=text.strip(),
            chapters=[],
            raw_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        )

    # Title = first H1, or first heading if no H1
    h1s = [h for h in headings if h[2] == 1]
    if h1s:
        title = h1s[0][3]

    # Preamble = text from start to first H2 (or H3 if no H2)
    boundary_levels = [h for h in headings if h[2] >= 2]
    if boundary_levels:
        first_boundary_start = boundary_levels[0][0]
        preamble = text[:first_boundary_start].strip()
    else:
        preamble = text.strip()

    # Chapters = each H2/H3 + its body up to the next same-or-higher level boundary
    n_headings = len(headings)
    pos = 0
    for i, (_start, end, level, title_text) in enumerate(headings):
        if level < 2:
            continue  # H1 doesn't open a chapter
        body_start = end
        body_end = len(text)
        for j in range(i + 1, n_headings):
            other_level = headings[j][2]
            if other_level <= level:
                body_end = headings[j][0]
                break
        body = text[body_start:body_end].strip()
        norm = normalize_title(title_text)
        h = _hash_chapter(level, norm, body)
        chapters.append(
            Chapter(
                level=level,
                title=title_text,
                normalized_title=norm,
                body=body,
                hash=h,
                position=pos,
            )
        )
        pos += 1

    return ParsedPRD(
        title=title,
        frontmatter=fm,
        preamble=preamble,
        chapters=chapters,
        raw_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    )
