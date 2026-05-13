"""Redaction helpers for developer-context evidence."""

from __future__ import annotations

import re

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(authorization:\s*bearer\s+)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)=([^\s&;]+)"),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(cookie:\s*)[^\n\r]+"),
        r"\1[REDACTED]",
    ),
]


def redact_sensitive_text(text: str, *, max_chars: int | None = None) -> str:
    out = text
    for pattern, replacement in _REDACTION_PATTERNS:
        out = pattern.sub(replacement, out)
    if max_chars is not None and len(out) > max_chars:
        return out[-max_chars:]
    return out
