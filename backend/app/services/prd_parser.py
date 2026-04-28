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
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FENCE_RE = re.compile(r"^(```+|~~~+)")


def _scan_headings(text: str) -> list[tuple[int, int, int, str]]:
    """Find headings outside fenced code blocks.

    Returns (start_offset, end_offset, level, title) tuples — same shape the
    parser used previously when it relied on `_HEADING_RE.finditer`. The fence
    awareness is the only difference: a `## API` line inside a ```...``` block
    no longer becomes a phantom chapter."""
    headings: list[tuple[int, int, int, str]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped_for_fence = line.lstrip()
        m_fence = _FENCE_RE.match(stripped_for_fence)
        if m_fence:
            marker = m_fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                # CommonMark requires the closer to be at least as long as the
                # opener. Without this, a 3-tick fence inside a 4-tick block
                # would prematurely close the outer fence.
                in_fence = False
                fence_char = ""
                fence_len = 0
            offset += len(line)
            continue

        if not in_fence:
            stripped_line = line.rstrip("\r\n")
            mh = _HEADING_RE.match(stripped_line)
            if mh:
                level = len(mh.group(1))
                title_text = mh.group(2).strip()
                # The match's start in `line` is 0; end of the heading line is
                # offset + length of the heading itself (no trailing newline).
                headings.append(
                    (
                        offset,
                        offset + len(stripped_line),
                        level,
                        title_text,
                    )
                )
        offset += len(line)
    return headings


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

    # Find all H1/H2/H3 positions, ignoring headings inside fenced code blocks.
    headings = _scan_headings(text)

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

    # Title = first H1, or fall back to the first heading of any level.
    h1s = [h for h in headings if h[2] == 1]
    if h1s:
        title = h1s[0][3]
    else:
        title = headings[0][3]

    # Preamble = text from start to first H2 (or H3 if no H2)
    boundary_levels = [h for h in headings if h[2] >= 2]
    if boundary_levels:
        first_boundary_start = boundary_levels[0][0]
        preamble = text[:first_boundary_start].strip()
    else:
        preamble = text.strip()

    # Each H2/H3 is its own chapter. The body for an H2 ends at the *next*
    # H2 OR H3 (so the H3 doesn't double-count: once as part of its parent
    # H2 body and once as its own chapter). H3 body ends at the next H2/H3.
    n_headings = len(headings)
    pos = 0
    for i, (_start, end, level, title_text) in enumerate(headings):
        if level < 2:
            continue  # H1 doesn't open a chapter
        body_start = end
        body_end = len(text)
        for j in range(i + 1, n_headings):
            other_level = headings[j][2]
            if other_level >= 2:
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
