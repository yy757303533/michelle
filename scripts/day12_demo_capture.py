"""Day 12 demo capture.

Walks the full Michelle UI with Playwright, records a webm video and one
PNG per page. Output:
    docs/day12-demo/{dashboard,prd,cases,runs,run-detail,diagnosis,patterns}.png
    docs/day12-demo/walkthrough.webm

Pre-req: backend on :8000 + frontend on :5173.
Usage:
    cd backend && uv run python ../scripts/day12_demo_capture.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "day12-demo"


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    target = "http://127.0.0.1:5173"
    backend = "http://127.0.0.1:8000"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(OUT),
            record_video_size={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()

        # 1. Dashboard
        await _shot(page, target, "dashboard.png", wait_text="Dashboard")

        # 2. PRD
        await _shot(page, f"{target}/prd", "prd.png", wait_text="PRD ingest")

        # 3. Cases
        await _shot(page, f"{target}/cases", "cases.png", wait_text="Test cases")

        # 4. Runs list
        await _shot(page, f"{target}/runs", "runs.png", wait_text="Runs")

        # 5. Run detail (latest run)
        try:
            req = await page.request.get(f"{backend}/api/runs/?limit=1")
            run_ids = [r["run_id"] for r in (await req.json()).get("data", [])]
        except Exception:
            run_ids = []
        if run_ids:
            await _shot(
                page, f"{target}/runs/{run_ids[0]}", "run-detail.png",
                wait_text="step timeline",
            )

        # 6. Diagnosis (need a failed run)
        try:
            req = await page.request.get(f"{backend}/api/runs/?limit=20")
            runs = (await req.json()).get("data", [])
            failed = next((r["run_id"] for r in runs if r["status"] in ("failed", "flaky")), None)
        except Exception:
            failed = None
        if failed:
            await _shot(
                page, f"{target}/diagnosis/{failed}", "diagnosis.png",
                wait_text="AI diagnosis",
            )
            # also click "open HTML report" → screenshot the standalone report would
            # require a fresh tab, skip for brevity
        else:
            print("(no failed runs found → skipping diagnosis screenshot)")

        # 7. The dashboard widgets at the end (loop close)
        await _shot(page, target, "dashboard-end.png", wait_text="Dashboard")

        await ctx.close()  # finalize the webm
        await browser.close()

    # Rename the random video file to a stable name
    videos = sorted(OUT.glob("*.webm"))
    if videos:
        target_path = OUT / "walkthrough.webm"
        if target_path.exists():
            target_path.unlink()
        videos[-1].rename(target_path)
        for v in videos[:-1]:
            v.unlink()
        print(f"\nvideo: {target_path.relative_to(ROOT)} ({target_path.stat().st_size//1024} KB)")

    print("\n=== screenshots ===")
    for f in sorted(OUT.glob("*.png")):
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size // 1024} KB)")

    return 0


async def _shot(page, url: str, fname: str, *, wait_text: str | None) -> None:
    print(f"==> {url}")
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    except Exception as e:
        print(f"   (warning: goto failed: {e})")
        return
    if wait_text:
        try:
            await page.wait_for_selector(f"text={wait_text}", timeout=5000)
        except Exception:
            pass
    await page.wait_for_timeout(1500)  # let queries settle for clean shots
    out = OUT / fname
    await page.screenshot(path=out, full_page=True)
    print(f"   saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
