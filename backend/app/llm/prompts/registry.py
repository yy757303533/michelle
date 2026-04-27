"""Prompt registry — versioned text templates loaded from disk.

Why versioned:
  - Sediment loop: every case/diagnosis records which prompt version produced it.
    When we change a prompt, we know which artifacts came from the old one.
  - A/B comparison: easy to A/B prompt v1 vs v2 on the golden regression set.

Convention:
  - One .txt file per (template_name, version) at app/llm/prompts/<name>_v<N>.txt
  - Template uses Python str.format with {placeholder}
  - Newest version is canonical; older versions kept for reproducibility
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent


class PromptNotFoundError(Exception):
    pass


@lru_cache(maxsize=64)
def load_prompt(name: str, version: str) -> str:
    """Load `<PROMPTS_DIR>/<name>_<version>.txt`. Cached forever (templates are read-only)."""
    candidates = [
        PROMPTS_DIR / f"{name}_{version}.txt",
        PROMPTS_DIR / f"{name}.{version}.txt",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise PromptNotFoundError(
        f"prompt not found: name={name} version={version} (tried {[str(p) for p in candidates]})"
    )


def render(name: str, version: str, **vars: Any) -> str:
    template = load_prompt(name, version)
    return template.format(**vars)


def prompt_id(name: str, version: str) -> str:
    """Stable id for storing alongside artifacts (e.g. case.prompt_version)."""
    return f"{name}_{version}"
