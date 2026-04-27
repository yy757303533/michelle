# Day 2 Findings — claude CLI + `@playwright/mcp` viability

**Date**: 2026-04-27
**Outcome**: ✅ Core architecture viable. ZStack admin login fully driven via the agent stack.

## What we proved

The full stack works end-to-end:

```
Python orchestrator (run_claude_with_playwright)
  └─ subprocess: claude -p --mcp-config <our-config>
       └─ MCP stdio session with @playwright/mcp 0.0.70
            └─ Playwright Chromium (headless, isolated)
                 └─ ZStack AIOS (http://172.25.17.105:5000/)
```

Real run captured in `backend/artifacts/day2-smoke/`:

- **Duration**: 41 seconds wall-clock for a full login + screenshot flow
- **Turns**: 10 model turns
- **Tool calls**: 9 (1 Claude built-in `ToolSearch`, 8 `@playwright/mcp` tools)
- **Trace size**: stream-json 343 KB → parsed structured JSON 34 KB
- **Subscription cost**: $0 (Claude Max). Reported $0.42 if billed at API rates.
- **Cache hit ratio**: 304k cache_read vs 23 input tokens → ~99% cache hit on subsequent runs

## Key flags for `claude -p` in our orchestration

| Flag | Why we use it |
|------|---------------|
| `-p` | non-interactive print mode |
| `--mcp-config <path>` | load `@playwright/mcp` server |
| `--strict-mcp-config` | ignore user/global MCP configs (cleanliness, reproducibility) |
| `--permission-mode bypassPermissions` | required for non-interactive — without it Claude prompts for permission per tool call |
| `--output-format stream-json` | get one JSONL event per assistant/user/result message |
| `--verbose` | required to actually emit the stream events (without it, stream-json gives only the final result) |

## `@playwright/mcp` flags worth knowing

| Flag | Why |
|------|-----|
| `--browser chromium` | be explicit; default works but pinning is safer |
| `--headless` | for CI / server runs |
| `--isolated` | per-run fresh browser context, prevents session bleed (see "Gotcha: session persistence") |
| `--caps vision,pdf,devtools` | extra capability bundles, not needed for MVP |

## Tool name convention

`@playwright/mcp` tools come in as `mcp__playwright__browser_<verb>` in stream-json events. Our parser strips the prefix when surfacing to UI.

Confirmed tool names used in this run:

- `mcp__playwright__browser_navigate({url})`
- `mcp__playwright__browser_snapshot()` — returns ARIA tree + console summary
- `mcp__playwright__browser_type({element, ref, text})`
- `mcp__playwright__browser_click({element, ref})`
- `mcp__playwright__browser_wait_for({time?, text?, textGone?})`
- `mcp__playwright__browser_take_screenshot({filename?, type?})`

## tool_result structure (verified)

Every `@playwright/mcp` call's `tool_result` text body has predictable sections we can regex out:

```
### Ran Playwright code
```js
await page.goto('...');
```
### Page
- Page URL: http://...
- Page Title: ...
- Console: 0 errors, 2 warnings
### Snapshot
```yaml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    ...
```
```

Plus screenshot tool returns `[Screenshot of viewport](filename.png)` we can capture.

`trace_parser.py` walks the stream-json and extracts these into `StepEvent` objects with `page_url`, `page_title`, `console_errors`, `console_warnings`, `screenshot_path`.

## Final-message protocol

The model writes a `RESULT={json}` line at the very end of its final message. Our parser regexes this out and exposes it as `summary.parsed_result`. Used to convey structured pass/fail without extra round trips.

## Gotcha: session persistence across runs

Without `--isolated`, Chromium kept the ZStack session cookie between runs — the second invocation went straight to `/dashboard` without filling the login form. This is real. We default `--isolated` on in `mcp_config.build_playwright_mcp_config()`. For "verify already-logged-in user can do X" scenarios, callers can opt out.

## Gotcha: Claude uses ToolSearch first

The model's first move was `ToolSearch(select:mcp__playwright__browser_*)` — Claude's built-in tool to fetch deferred tool schemas before using them. This eats one turn and shows up as a non-playwright step in our trace. Two options:

- Accept the extra turn (current behavior — costs ~0.5s and a few hundred tokens)
- Pre-warm the tools list in the system prompt so Claude doesn't bother (Day 6 optimization if it matters)

We mark these in the trace with `is_playwright=False` so business reporting can filter them out.

## Gotcha: nested SessionEnd hook errors

The user's environment has a global `SessionEnd` hook (visible in stderr). It runs out-of-band and its failures are harmless to our flow. We log stderr to `claude.err.log` per run for forensics but don't treat its content as failure.

## Cost / rate-limit observations

Token-wise Day 2's smoke is dominated by **cache reads** (~300k cache read vs 23 fresh input). This means:

- Running 50 cases in a row will not 50x the token cost
- But Claude Max rate limit is per-time-window regardless — burning 50 in 5 minutes will still throttle
- Day 3 LLM Gateway must surface usage trends so we see throttling coming

## What we will NOT need

The DIY DOM-augmented vision agent (designed in earlier rounds) is unnecessary. ARIA tree from `browser_snapshot` resolves elements precisely — no vision LLM needed for execution. We keep MiniMax vision config in env for future fallback only.

## Risks confirmed (mitigation strategy unchanged)

- ZStack is React with passable but minimal ARIA labels (`textbox` without name/label). Claude reasons about adjacency to icons. Works on this site, but a less semantic site might struggle. Fallback strategy from PRD §12 stands.

## Day 3 reading list

When implementing the LLM Gateway, the parser's `RunSummary.input_tokens` /
`output_tokens` / `cache_read_tokens` fields plug straight into the
`llm.completion` event. Use them to build a "today's burn" panel.
