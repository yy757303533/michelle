"""Day 7 visual smoke: Playwright walks every Michelle page, screenshot each.

Pre-req: backend running on :8000 and frontend on :5173 (run `make dev` first).

Usage (from repo root):
    cd backend && uv run python ../scripts/day7_visual_smoke.py [--headed]

Output:
    docs/day7-screens/{dashboard,prd,cases,runs,run-detail}.png
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs" / "day7-screens"


async def main(headed: bool = False) -> int:
    from playwright.async_api import async_playwright

    SCREENSHOTS.mkdir(parents=True, exist_ok=True)

    target = "http://127.0.0.1:5173"
    backend = "http://127.0.0.1:8000"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        # 0. backend live?
        try:
            r = await page.request.get(f"{backend}/healthz")
            assert r.ok, f"backend /healthz returned {r.status}"
        except Exception as exc:
            print(f"backend not reachable: {exc}", file=sys.stderr)
            return 1

        # 1. dashboard
        await _capture(page, target, "dashboard.png", wait_text="Dashboard")
        # 2. PRD
        await _capture(page, f"{target}/prd", "prd.png", wait_text="PRD ingest")
        # 3. Cases
        await _capture(page, f"{target}/cases", "cases.png", wait_text="Test cases")
        # 4. Runs list
        await _capture(page, f"{target}/runs", "runs.png", wait_text="Runs")

        # 5. Run detail — pick the latest run if any
        try:
            runs = await (await page.request.get(f"{backend}/api/runs/?limit=1")).json()
            run_ids = [r["run_id"] for r in runs.get("data", [])]
        except Exception:
            run_ids = []
        if run_ids:
            await _capture(
                page,
                f"{target}/runs/{run_ids[0]}",
                "run-detail.png",
                wait_text="step timeline",
            )
        else:
            print("(no run yet → skipping run-detail screenshot)")

        # 6. Diagnosis placeholder
        await _capture(
            page,
            f"{target}/diagnosis/placeholder",
            "diagnosis-placeholder.png",
            wait_text="Day 11 placeholder",
        )

        await ctx.close()
        await browser.close()

    print("\n=== screenshots ===")
    for f in sorted(SCREENSHOTS.glob("*.png")):
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size // 1024} KB)")

    return 0


async def _capture(page, url: str, fname: str, *, wait_text: str | None) -> None:
    print(f"==> {url}")
    await page.goto(url, wait_until="networkidle", timeout=15000)
    if wait_text:
        try:
            await page.wait_for_selector(f"text={wait_text}", timeout=5000)
        except Exception:
            print(f"   (warning: '{wait_text}' not found; capturing anyway)")
    await page.wait_for_timeout(800)  # let queries settle
    out = SCREENSHOTS / fname
    await page.screenshot(path=out, full_page=True)
    print(f"   saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    headed = "--headed" in sys.argv
    sys.exit(asyncio.run(main(headed=headed)))
