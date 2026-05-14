"""Real target E2E smoke for Michelle's execution + diagnosis loop.

Pre-req:
  - backend on :8000 and frontend on :5173 (`make dev`)
  - DEFAULT_TARGET_URL points at a reachable web app, or pass --target-url
  - at least one executable provider/CLI is configured for the backend
  - Playwright MCP can start; the script preflights /api/settings/selfcheck
    with include_mcp_probe=true before creating cases.

Usage from repo root:
  cd backend && uv run python ../scripts/day13_e2e_smoke.py

The script creates one temporary project, three manual cases, approves them,
starts one run per case, waits for terminal states, and triggers diagnosis for
one non-passed run. It exits non-zero unless at least 3 runs finish and at
least one non-passed run receives a diagnosis response.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from typing import Any

import httpx

TERMINAL = {"passed", "failed", "flaky", "aborted"}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    parser.add_argument("--target-url", default=os.getenv("DEFAULT_TARGET_URL", ""))
    parser.add_argument("--username", default=os.getenv("DEFAULT_TARGET_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("DEFAULT_TARGET_PASSWORD", ""))
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_TOKEN", ""))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Run cases one at a time. Useful for first-run MCP/npm warm-up.",
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Keep the temporary day13-e2e project for debugging.",
    )
    return parser.parse_args()


async def main() -> int:
    args = _args()
    if not args.target_url:
        print("DEFAULT_TARGET_URL is empty; set it or pass --target-url")
        return 2

    headers = {"X-Michelle-Admin-Token": args.admin_token} if args.admin_token else {}
    project_id = ""
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        await _check_stack(client, args.backend, args.frontend, args.target_url)
        await _preflight_mcp(client, args.backend)
        try:
            project_id = await _create_project(client, args)
            case_ids = await _create_and_approve_cases(client, args.backend, project_id)
            run_ids = (
                await _start_runs_serial(client, args.backend, case_ids)
                if args.serial
                else await _start_runs(client, args.backend, case_ids)
            )
            runs = await _wait_for_runs(
                client,
                args.backend,
                run_ids,
                deadline_seconds=args.timeout,
            )

            failed = [r for r in runs if r["status"] in {"failed", "flaky", "aborted"}]
            if not failed:
                print("No failed/flaky/aborted run found; intentional failure case did not fail")
                return 1

            diag = await _diagnose(client, args.backend, failed[0]["run_id"])
            print("\n=== e2e smoke result ===")
            for run in runs:
                print(f"{run['run_id']}  {run['case_id']}  {run['status']}")
            print(f"diagnosis run_id={failed[0]['run_id']} response_keys={sorted(diag.keys())}")
            return 0
        finally:
            if project_id and not args.keep_project:
                await _delete_project(client, args.backend, project_id)


async def _check_stack(
    client: httpx.AsyncClient,
    backend: str,
    frontend: str,
    target_url: str,
) -> None:
    for name, url in (
        ("backend", f"{backend}/healthz"),
        ("frontend", frontend),
        ("target", target_url),
    ):
        r = await client.get(url)
        r.raise_for_status()
        print(f"{name} ok: {url}")


async def _preflight_mcp(client: httpx.AsyncClient, backend: str) -> None:
    r = await client.get(f"{backend}/api/settings/selfcheck", params={"include_mcp_probe": "true"})
    r.raise_for_status()
    checks = r.json()["data"]["checks"]
    by_name = {c["name"]: c for c in checks}
    probe = by_name.get("playwright_mcp_probe") or {}
    if not probe.get("ok"):
        raise RuntimeError(f"Playwright MCP probe failed: {probe.get('detail')}")
    print(
        "playwright mcp ok:",
        probe.get("detail"),
        f"elapsed_ms={probe.get('elapsed_ms')}",
    )


async def _create_project(client: httpx.AsyncClient, args: argparse.Namespace) -> str:
    payload = {
        "name": f"day13-e2e-{int(time.time())}",
        "base_url": args.target_url,
        "description": "Temporary project created by scripts/day13_e2e_smoke.py",
        "default_username": args.username,
        "default_password": args.password,
    }
    r = await client.post(f"{args.backend}/api/projects/", json=payload)
    r.raise_for_status()
    project = r.json()["data"]
    print(f"project created: {project['project_id']}")
    return project["project_id"]


async def _create_and_approve_cases(
    client: httpx.AsyncClient,
    backend: str,
    project_id: str,
) -> list[str]:
    cases = [
        {
            "name": "Smoke: homepage loads",
            "intent": "Open the target homepage and verify the page becomes usable.",
            "steps": [{"intent": "Open the project base URL."}],
            "assertions": [{"description": "The page loads without a browser or network error."}],
        },
        {
            "name": "Smoke: inspect primary page content",
            "intent": "Inspect the target homepage and summarize visible navigational content.",
            "steps": [{"intent": "Open the project base URL and inspect the page snapshot."}],
            "assertions": [
                {"description": "At least one visible heading, link, or button is present."}
            ],
        },
        {
            "name": "Smoke: intentional missing marker",
            "intent": "Fail if a deliberately absent marker is not visible.",
            "steps": [
                {"intent": "Open the project base URL."},
                {
                    "intent": (
                        "Verify the exact text MICHELLE_E2E_INTENTIONAL_FAILURE_MARKER "
                        "is visible on the page."
                    )
                },
            ],
            "assertions": [
                {
                    "description": (
                        "The page contains the exact text MICHELLE_E2E_INTENTIONAL_FAILURE_MARKER."
                    )
                }
            ],
        },
    ]

    case_ids: list[str] = []
    for spec in cases:
        payload: dict[str, Any] = {
            "project_id": project_id,
            "module": "e2e-smoke",
            "priority": "P1",
            "auth_state": "public",
            "preconditions": [],
            "tags": ["e2e-smoke"],
            **spec,
        }
        r = await client.post(f"{backend}/api/cases/", json=payload)
        r.raise_for_status()
        case_id = r.json()["data"]["case_id"]
        approve = await client.post(
            f"{backend}/api/cases/{case_id}/review",
            json={"action": "approve"},
        )
        approve.raise_for_status()
        print(f"case approved: {case_id} {payload['name']}")
        case_ids.append(case_id)
    return case_ids


async def _start_runs(
    client: httpx.AsyncClient,
    backend: str,
    case_ids: list[str],
) -> list[str]:
    r = await client.post(
        f"{backend}/api/runs/",
        json={"case_ids": case_ids, "env": "e2e-smoke", "timeout_seconds": 300},
    )
    r.raise_for_status()
    run_ids = r.json()["data"]["run_ids"]
    print(f"runs started: {', '.join(run_ids)}")
    return run_ids


async def _start_runs_serial(
    client: httpx.AsyncClient,
    backend: str,
    case_ids: list[str],
) -> list[str]:
    run_ids: list[str] = []
    for case_id in case_ids:
        run_ids.extend(await _start_runs(client, backend, [case_id]))
    return run_ids


async def _wait_for_runs(
    client: httpx.AsyncClient,
    backend: str,
    run_ids: list[str],
    *,
    deadline_seconds: int,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + deadline_seconds
    latest: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        for run_id in run_ids:
            r = await client.get(f"{backend}/api/runs/{run_id}")
            r.raise_for_status()
            latest[run_id] = r.json()["data"]["run"]
        statuses = {run_id: run["status"] for run_id, run in latest.items()}
        print("run statuses:", statuses)
        if len(latest) == len(run_ids) and all(s in TERMINAL for s in statuses.values()):
            return [latest[run_id] for run_id in run_ids]
        await asyncio.sleep(5)
    raise TimeoutError(f"runs did not finish within {deadline_seconds}s")


async def _diagnose(client: httpx.AsyncClient, backend: str, run_id: str) -> dict[str, Any]:
    r = await client.post(
        f"{backend}/api/diagnosis/by-run/{run_id}/jobs",
        json={"include_dev_context": True, "overwrite_existing": False},
    )
    r.raise_for_status()
    job_id = r.json()["data"]["job_id"]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        status = await client.get(f"{backend}/api/diagnosis/jobs/{job_id}")
        status.raise_for_status()
        data = status.json()["data"]
        if data["status"] == "done":
            return _assert_finished_diagnosis_job(data)
        if data["status"] == "failed":
            raise RuntimeError(f"diagnosis job failed: {data.get('error')}")
        await asyncio.sleep(2)
    raise TimeoutError(f"diagnosis job {job_id} did not finish")


def _assert_finished_diagnosis_job(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") != "done":
        raise RuntimeError(
            f"diagnosis job {data.get('job_id') or '<unknown>'} ended with {data.get('status')}: {data.get('error') or ''}"
        )
    if not data.get("diag_id"):
        raise RuntimeError(
            f"diagnosis job {data.get('job_id') or '<unknown>'} is done but missing diag_id"
        )
    return data


async def _delete_project(client: httpx.AsyncClient, backend: str, project_id: str) -> None:
    r = await client.delete(f"{backend}/api/projects/{project_id}")
    if r.status_code == 204:
        print(f"temporary project deleted: {project_id}")
        return
    print(f"temporary project cleanup failed: {project_id} status={r.status_code} body={r.text}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
