"""Day 2 smoke: drive ZStack login through Michelle's agent modules.

Run from the repo root:

    cd backend && uv run python ../scripts/day2_smoke.py

Verifies the full Day 2 contract:

    [Python orchestrator]
        ↓ spawn
    [claude -p --mcp-config ...]
        ↓ MCP stdio
    [@playwright/mcp]
        ↓ Playwright API
    [Chromium → ZStack AIOS]

Outputs:
    backend/artifacts/day2-smoke/
        ├── claude.stream.jsonl   (raw)
        ├── claude.err.log
        ├── mcp.json
        └── trace.parsed.json     (our structured view)
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make `app` importable when running this script from anywhere
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.claude_runner import (  # noqa: E402
    RunRequest,
    render_login_smoke_prompt,
    run_claude_with_playwright,
)
from app.config import settings  # noqa: E402
from app.obs import setup_logging  # noqa: E402


async def main() -> int:
    setup_logging()

    work_dir = ROOT / "backend" / "artifacts" / "day2-smoke"

    prompt = render_login_smoke_prompt(
        url=settings.default_target_url,
        username=settings.default_target_username,
        password=settings.default_target_password,
    )
    print(f"==> target: {settings.default_target_url}")
    print(f"==> work_dir: {work_dir}")

    outcome = await run_claude_with_playwright(
        RunRequest(prompt=prompt, work_dir=work_dir, timeout_seconds=300)
    )

    parsed = outcome.parsed
    print(f"\n==> exit={outcome.exit_code} elapsed={outcome.elapsed_ms} ms")
    print(f"==> success_hint={parsed.summary.success}")
    print(f"==> parsed_result={parsed.summary.parsed_result}")
    print(f"==> tool calls: {len(parsed.steps)} ({len(parsed.playwright_steps)} via @playwright/mcp)")
    print()
    for s in parsed.steps:
        print(f"  [{s.step_index}] {s.short_summary()}")

    # dump structured trace
    trace_path = work_dir / "trace.parsed.json"
    trace_path.write_text(
        json.dumps(
            {
                "summary": asdict(parsed.summary),
                "steps": [asdict(s) for s in parsed.steps],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    print(f"\n==> wrote {trace_path}")

    print(f"\n==> cost (Opus subscription, real $0): ${parsed.summary.cost_usd}")
    print(f"==> tokens: in={parsed.summary.input_tokens} out={parsed.summary.output_tokens} cache_read={parsed.summary.cache_read_tokens}")

    return 0 if parsed.summary.success else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
