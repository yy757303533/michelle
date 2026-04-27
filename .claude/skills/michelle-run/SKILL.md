---
name: michelle-run
description: Execute selected Michelle test cases by ID and report results. Use when the user says "run case X", "execute these cases", or wants to trigger a Michelle test run from the terminal instead of the Web UI.
allowed-tools: [Bash]
---

# /michelle-run — Execute Michelle test cases

## Args

`$ARGUMENTS` — comma-separated case IDs, e.g. `TC-20260427-001,TC-20260427-002`.
If empty, ask the user which cases to run, or list approved cases first.

## Steps

1. Verify Michelle backend is reachable: `curl -s http://localhost:8000/healthz`
   - If not, tell the user to run `make dev` in the michelle/ directory first.
2. POST to create a run:
   ```
   curl -s -X POST http://localhost:8000/api/runs \
     -H 'Content-Type: application/json' \
     -d '{"case_ids": [<ids>], "env": "default"}'
   ```
3. Capture the returned `run_id`. Stream progress by polling
   `GET /api/runs/<run_id>` every 5s until `status` is one of
   `passed`, `failed`, `flaky`, `aborted`.
4. Print a brief result summary: pass/fail counts, link to web report
   (`http://localhost:5173/runs/<run_id>`).
5. If anything failed, suggest running `/michelle-diagnose <run_id>`.

## Errors

- Backend down → instruct user to start `make dev`
- Case IDs not found → list available case IDs

## Notes

- This invokes the same code path as clicking "Run" in the Web UI.
- It is *not* a substitute for the Web UI — for batch review/edit, the UI is faster.
