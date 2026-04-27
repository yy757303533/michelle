"""Generate per-run MCP server configurations for `@playwright/mcp`.

Each run gets its own config (and ideally its own browser context) so test runs
don't pollute each other's session state. We discovered on Day 2 that Chromium
keeps cookies across MCP invocations — without isolation a 'login' case run
twice in a row will skip the login form on the second run.

Isolation strategies (from cheap to clean):

1. Use `--isolated` flag on `@playwright/mcp` (per-run fresh context, no on-disk
   storage) — simplest, what we use by default.
2. Per-run `--user-data-dir` pointing to a fresh tempdir — full chrome profile
   isolation. Slower because no warm cache.
3. Reuse one server but call `browser_close` between cases — ok for sequential
   in-process orchestration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_playwright_mcp_config(
    *,
    isolated: bool = True,
    headless: bool = True,
    browser: str = "chromium",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Return an MCP servers config dict suitable for `claude --mcp-config <file>`."""
    args: list[str] = [
        "-y",
        "@playwright/mcp@latest",
        "--browser",
        browser,
    ]
    if headless:
        args.append("--headless")
    if isolated:
        # `--isolated` keeps profile in-memory; new browser context per run.
        args.append("--isolated")
    if extra_args:
        args.extend(extra_args)

    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": args,
            }
        }
    }


def write_config(path: Path, config: dict[str, Any]) -> Path:
    """Write the config to `path` and return it. Parents created as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    return path
