# Day 6 Findings — Run Orchestrator end-to-end

**Date**: 2026-04-27
**Outcome**: ✅ Full pipeline alive. **Michelle ran TC-20260427-0001 end-to-end via REST and successfully logged into the demo target.**

## What we proved

The complete loop works in production-like flow:

```
PRD upload (Day 4)
   ↓ AI generates 12 cases
TC-20260427-0001 (login <demo creds>) sits in `pending`
   ↓ POST /api/cases/<id>/review {"action":"approve"}
case is now `approved`
   ↓ POST /api/runs/ {"case_ids":["TC-..."]}
Run row created, status=pending, run_id returned
   ↓ background task fires
   ↓ render execute_v1 prompt with case fields
   ↓ spawn `claude -p --mcp-config <ours> --strict-mcp-config ...`
   ↓ claude session uses @playwright/mcp tools to drive Chromium
   ↓ 9 tool calls: ToolSearch, browser_navigate, snapshot, type×2, snapshot, click, wait_for, snapshot
   ↓ ParsedRun → 9 StepEvent rows persisted
   ↓ Run.status updated to passed
   ↓ report.html + result.json + trace.jsonl written to artifacts/<project>/<run_id>/
GET /api/runs/<id>/report.html serves the HTML
```

Sample artifacts captured at `docs/day6-sample/`:
- `prompt.txt` (2.2 KB) — the actual rendered execute_v1 prompt
- `trace.jsonl` (2.7 KB) — structured per-step events
- `result.json` (582 B) — summary
- `report.html` (5.6 KB) — self-contained HTML

## Numbers

| Metric | Value |
|---|---|
| Wall time (REST POST → terminal status) | ~60 s |
| Claude turns | 10 |
| @playwright/mcp tool calls | 8 |
| URL transition | `/login` → `/dashboard` ✅ |
| Tokens (input / output) | 24 / 1,636 (cache-warmed by earlier Day 2 run) |
| LLM cost | $0 (subscription) |
| Polling cadence (frontend) | 1.5 s while running, halts on terminal |

## What worked well

- **The orchestrator is genuinely thin**: ~250 lines of Python wraps Day 2's
  `claude_runner` and Day 5's `report_html`. Most of the work was already done.

- **Artifacts dir layout** (`artifacts/<project>/<run_id>/`) keeps prompt,
  raw claude stream, parsed trace, and rendered report side by side — easy
  forensics, easy demo.

- **Status inference** correctly picked `passed` from the model's
  `RESULT={"case_status":"passed"}` line. Step-level fallthrough wasn't
  needed but the code handles it (see test_run_orchestrator.py).

- **Frontend polling pattern**: `refetchInterval` returns `false` once the
  run hits a terminal state, so the page stops polling automatically. No
  WebSocket needed for MVP.

## Things observed

- **Frontend `/runs/<id>` page** is built but I didn't manually click through
  it during the smoke. REST endpoints prove the data is available; the page
  consumes them. Will visually verify on Day 7's full integration pass.

- **`ToolSearch` shows up as step 0 again** (consistent with Day 2 finding).
  We tag it `is_playwright=False` and the report counts only @playwright/mcp
  steps as user-facing. Filtering it out cosmetically is a Day 7 polish item.

- **The `error` column says "fail-row" matched** in the smoke regex — that's
  just CSS class name in the template, not a real failure.

## Cost trends across days

| Day | Run | LLM tokens consumed |
|-----|-----|---------------------|
| Day 2 | First successful login (cold) | input=14, output=505, cache_create=66k |
| Day 6 | Same case via REST (warm)     | input=24, output=1636, cache_read=N/A |

Cache stays warm across runs of the same case. Subscription cost remains $0.

## What this unlocks

- **Day 7 frontend integration**: all REST endpoints exist; pages just need to
  consume them. The `/runs/<id>` polling page is already wired.

- **Day 8 review workflow**: the Run trigger requires a case (any state, but
  UI only shows the button for `approved`). The state machine's other half
  is the manual_edited_fields protection — straightforward.

- **Day 9 multi-case batch**: POST already accepts `case_ids: [list]`. Each
  run gets its own `kick_off`, so 10 cases = 10 background tasks. Day 9 will
  add concurrency limits (likely 2-3 simultaneous to avoid Chromium thrash).

- **Day 11 diagnosis**: the trace.jsonl + screenshots side-by-side is exactly
  what the diagnose_v1 prompt expects. We just need a failing run to point
  at.

## Day 7 reading list

The `/runs/<id>` polling page is already comprehensive. Day 7's job is mostly
to wire the existing PRD-list / Cases-list views to the real backend endpoints
(they already are) and add a "runs list" page. Easy day.
