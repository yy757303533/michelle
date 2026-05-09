"""Environment helpers for `claude` CLI subprocesses.

Michelle has two separate Claude CLI call sites:

* `app.agent.claude_runner` — execution agent with `@playwright/mcp`
* `app.llm.claude_cli` — simple text completion provider

Both must see the same Anthropic-compatible gateway settings. Repo `.env`
values win when present, but empty `.env` placeholders should not erase a
developer's interactive shell exports from `.zshrc`.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import settings

ANTHROPIC_ENV_KEYS = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "CLAUDE_CODE_ATTRIBUTION_HEADER",
}


def read_dotenv_anthropic_overrides() -> dict[str, str]:
    """Read Anthropic/Claude CLI overrides directly from Michelle's `.env`.

    `pydantic-settings` intentionally gives the parent process environment
    precedence over `.env`. For subprocesses, non-empty Michelle `.env` values
    are the runtime contract; empty placeholders mean "inherit if available."
    """

    candidates = [
        Path(__file__).resolve().parents[3] / ".env",  # repo root
        Path(__file__).resolve().parents[2] / ".env",  # backend/
        Path.cwd() / ".env",
    ]
    overrides: dict[str, str] = {}
    for p in candidates:
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in ANTHROPIC_ENV_KEYS and k not in overrides:
                overrides[k] = v
        if overrides:
            break
    return overrides


def build_claude_subprocess_env(*, michelle_run: bool = False) -> dict[str, str]:
    """Compose a consistent environment for every `claude` subprocess."""

    env = dict(os.environ)
    if michelle_run:
        env["MICHELLE_RUN"] = "1"

    # First apply settings (covers env-only deployments without a `.env` file).
    configured = {
        "ANTHROPIC_BASE_URL": settings.anthropic_base_url,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "ANTHROPIC_MODEL": settings.anthropic_model,
        "CLAUDE_CODE_ATTRIBUTION_HEADER": settings.claude_code_attribution_header,
    }
    for k, v in configured.items():
        if v:
            env[k] = v

    # Then force non-empty repo `.env` values on top of inherited shell exports.
    # Empty placeholders intentionally do nothing so `.zshrc`-configured Claude
    # gateways remain usable in local development.
    for k, v in read_dotenv_anthropic_overrides().items():
        if v:
            env[k] = v
    return env
