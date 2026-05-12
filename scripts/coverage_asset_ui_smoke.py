"""Coverage-first UI smoke for PRD -> coverage -> case -> asset -> replay.

Pre-req:
  - backend on :8000 and frontend on :5173 (`make dev`)
  - run from backend with: uv run python ../scripts/coverage_asset_ui_smoke.py

The script drives the React UI with Playwright for the product flow. It seeds a
passed run directly after case approval so the smoke does not depend on an
external LLM executor being available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.async_api import Page, async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


async def main() -> int:
    args = _args()
    headers = {"X-Michelle-Admin-Token": args.admin_token} if args.admin_token else {}
    project_id = f"ui-smoke-{int(time.time())}"
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        await _check_stack(client, args.backend, args.frontend)
        await _create_project(client, args.backend, project_id, args.frontend)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=not args.headed)
                page = await browser.new_page()
                if args.admin_token:
                    await _install_admin_header(page, args.admin_token)

                await _drive_prd_to_drafted_case(page, args.frontend, project_id)
                case_id = await _latest_case_id(client, args.backend, project_id)
                await _approve_case_in_ui(page, args.frontend, project_id, case_id)
                run_id = await _seed_passed_run(project_id, case_id, args.frontend)
                replay_run_id = await _drive_asset_flow(
                    page,
                    client,
                    args.backend,
                    args.frontend,
                    project_id,
                    run_id,
                    deadline_seconds=args.timeout,
                )
                await browser.close()

            print("\n=== coverage asset UI smoke result ===")
            print(f"project_id={project_id}")
            print(f"case_id={case_id}")
            print(f"source_run_id={run_id}")
            print(f"replay_run_id={replay_run_id}")
            return 0
        finally:
            if not args.keep_project:
                await _delete_project(client, args.backend, project_id)


async def _check_stack(client: httpx.AsyncClient, backend: str, frontend: str) -> None:
    for name, url in (("backend", f"{backend}/healthz"), ("frontend", frontend)):
        r = await client.get(url)
        r.raise_for_status()
        print(f"{name} ok: {url}")


async def _create_project(
    client: httpx.AsyncClient,
    backend: str,
    project_id: str,
    frontend: str,
) -> None:
    r = await client.post(
        f"{backend}/api/projects/",
        json={
            "project_id": project_id,
            "name": project_id,
            "base_url": frontend,
            "description": "Temporary project created by coverage_asset_ui_smoke.py",
        },
    )
    r.raise_for_status()
    print(f"project created: {project_id}")


async def _install_admin_header(page: Page, token: str) -> None:
    async def route_api(route):
        headers = {**route.request.headers, "x-michelle-admin-token": token}
        await route.continue_(headers=headers)

    async def route_health(route):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"ok","auth_required":false}',
        )

    await page.route("**/api/**", route_api)
    await page.route("**/healthz", route_health)
    token_json = json.dumps(token)
    await page.add_init_script(
        script="""
        (() => {
          const token = __TOKEN__;
          const originalFetch = window.fetch.bind(window);
          window.fetch = (input, init = {}) => {
            const url = typeof input === "string" ? input : input.url;
            const path = url.startsWith("http") ? new URL(url).pathname : url;
            if (path.startsWith("/healthz")) {
              return Promise.resolve(new Response(JSON.stringify({ status: "ok", auth_required: false }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              }));
            }
            const headers = new Headers(init.headers || {});
            if (path.startsWith("/api")) {
              headers.set("X-Michelle-Admin-Token", token);
            }
            return originalFetch(input, { ...init, headers });
          };
        }})();
        """.replace("__TOKEN__", token_json),
    )


async def _drive_prd_to_drafted_case(page: Page, frontend: str, project_id: str) -> None:
    await page.goto(f"{frontend}/prd?project_id={project_id}")
    await expect(page.get_by_role("heading", name="PRD ingest")).to_be_visible()
    await page.wait_for_timeout(1000)
    await _set_input_value(
        page,
        'input[placeholder="Michelle PRD v0.5"]',
        "UI Smoke PRD",
    )
    await _set_input_value(
        page,
        "textarea",
        "# UI Smoke PRD\n\n"
        "## Dashboard availability\n\n"
        "The dashboard page must load successfully and show the Michelle product shell.\n",
    )
    await page.get_by_role("button", name="Upload + parse").click()
    await expect(page.get_by_text("uploaded", exact=True)).to_be_visible()
    await page.get_by_role("button", name=re.compile(r"^Analyze coverage")).click()
    await expect(page.get_by_role("button", name="Accept")).to_be_visible(timeout=15_000)
    await page.get_by_role("button", name="Accept").click()
    await expect(page.get_by_role("button", name="Draft case")).to_be_enabled(timeout=15_000)
    await page.get_by_role("button", name="Draft case").click()
    await expect(page.get_by_role("button", name="Drafted")).to_be_visible(timeout=15_000)
    print("ui coverage accepted and case drafted")


async def _set_input_value(page: Page, selector: str, value: str) -> None:
    try:
        await page.wait_for_selector(selector, state="visible", timeout=15_000)
    except Exception:
        print(await page.locator("body").inner_text(timeout=5_000))
        raise
    await page.eval_on_selector(
        selector,
        """(el, value) => {
          const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
          setter.call(el, value);
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }""",
        value,
    )


async def _latest_case_id(client: httpx.AsyncClient, backend: str, project_id: str) -> str:
    r = await client.get(f"{backend}/api/cases/", params={"project_id": project_id, "limit": 10})
    r.raise_for_status()
    rows = r.json()["data"]
    if not rows:
        raise RuntimeError("drafted case not found")
    return rows[0]["case_id"]


async def _approve_case_in_ui(
    page: Page,
    frontend: str,
    project_id: str,
    case_id: str,
) -> None:
    await page.goto(f"{frontend}/cases?project_id={project_id}")
    await expect(page.get_by_text(case_id)).to_be_visible(timeout=15_000)
    await page.get_by_role("button", name="approve", exact=True).click()
    try:
        await expect(page.get_by_role("button", name=re.compile("Run"))).to_be_visible(
            timeout=15_000
        )
    except Exception:
        print(await page.locator("body").inner_text(timeout=5_000))
        raise
    print(f"ui case approved: {case_id}")


async def _seed_passed_run(project_id: str, case_id: str, frontend: str) -> str:
    from app.db import async_session_maker
    from app.models import Run, StepEvent

    run_id = "run_ui_smoke_" + uuid4().hex[:8]
    async with async_session_maker() as session:
        session.add(
            Run(
                run_id=run_id,
                trace_id="trace_" + run_id,
                project_id=project_id,
                case_id=case_id,
                case_version=1,
                status="passed",
                execution_mode="agentic",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                duration_ms=1,
            )
        )
        session.add(
            StepEvent(
                run_id=run_id,
                step_index=0,
                phase="execute",
                event="agent.step.executed",
                intent="open Michelle frontend",
                tool_name="browser_navigate",
                tool_args={"url": frontend},
                tool_result={"ok": True},
                status="ok",
            )
        )
        await session.commit()
    print(f"passed run seeded: {run_id}")
    return run_id


async def _drive_asset_flow(
    page: Page,
    client: httpx.AsyncClient,
    backend: str,
    frontend: str,
    project_id: str,
    run_id: str,
    *,
    deadline_seconds: int,
) -> str:
    await page.goto(f"{frontend}/?project_id={project_id}")
    await expect(page.get_by_text("Regression assets")).to_be_visible(timeout=15_000)
    await expect(page.get_by_text(run_id[:8]).first).to_be_visible(timeout=15_000)
    await page.get_by_role("button", name="extract").click()
    await expect(page.get_by_role("button", name="approve").first).to_be_visible(timeout=15_000)
    await page.get_by_role("button", name="approve").first.click()
    await expect(page.get_by_role("button", name="replay").first).to_be_enabled(timeout=15_000)
    await page.get_by_role("button", name="replay").first.click()
    replay_run_id = await _wait_for_replay_run(
        client,
        backend,
        project_id,
        deadline_seconds=deadline_seconds,
    )
    await expect(page.get_by_role("button", name="repair").first).to_be_visible(timeout=15_000)
    await page.get_by_role("button", name="repair").first.click()
    await expect(page.get_by_text("action_plan")).to_be_visible()
    await page.get_by_role("button", name="save repair").click()
    print(f"ui asset replayed and repair editor saved: {replay_run_id}")
    return replay_run_id


async def _wait_for_replay_run(
    client: httpx.AsyncClient,
    backend: str,
    project_id: str,
    *,
    deadline_seconds: int,
) -> str:
    deadline = time.monotonic() + deadline_seconds
    latest_replay = ""
    while time.monotonic() < deadline:
        r = await client.get(
            f"{backend}/api/regression-assets/",
            params={"project_id": project_id},
        )
        r.raise_for_status()
        rows = r.json()["data"]
        if rows:
            latest_replay = rows[0].get("last_replay_run_id") or latest_replay
            if rows[0].get("last_status") == "passed" and latest_replay:
                return latest_replay
        await asyncio.sleep(2)
    raise TimeoutError(
        f"asset replay did not pass within {deadline_seconds}s; last run {latest_replay}"
    )


async def _delete_project(client: httpx.AsyncClient, backend: str, project_id: str) -> None:
    r = await client.delete(f"{backend}/api/projects/{project_id}")
    if r.status_code == 204:
        print(f"temporary project deleted: {project_id}")
    else:
        print(f"temporary project cleanup failed: {project_id} {r.status_code} {r.text}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
