# Lessons learned — Michelle, days 1-11

Honest retrospective. What I'd keep, what I'd change, what I'd cut.

---

## Things I got right

### Architecture pillars survived contact with reality

All four PRD pillars (agent-native parity, `@playwright/mcp` over DIY vision,
provider-agnostic LLM gateway, three layers of observability) are still in
the code at v0.1.0. None of them got dropped under deadline pressure. That's
because each was tied to a small, testable boundary on day 1 instead of a
late-stage refactor.

### Provider-agnostic gateway paid off twice

- Day 6: when Claude CLI's `-p` mode rejected images (no
  `CLAUDE_CODE_SESSION_ACCESS_TOKEN`), routing the diagnosis call to
  `MiniMax-Text-01` was a **two-line change**. No other code knew or cared.
- Day 11: when MiniMax actually produced a clean diagnosis, the human-feedback
  loop and pattern library worked the first time because the data shape
  (`LLMResult`) was identical regardless of which provider answered.

The cost of the abstraction was ~100 lines of `BaseChatClient` + 7 typed
errors. Worth it.

### Day 2 as a feasibility gate, not a sprint

I spent Day 2 entirely on "does claude CLI + `@playwright/mcp` actually drive
the demo?" instead of building features. The win wasn't the working login —
it was the trace-parser and the gotchas (`--isolated`, `--verbose`,
`ToolSearch` first turn, session-persistence) captured in
`docs/day2-findings.md`. Day 6's Run Orchestrator was 250 lines of glue
because every hard problem was already solved.

### Dogfood, not synthetic test data

Feeding Michelle's own PRD into Michelle on Day 4 produced 12 cases anchored
on real facts ("admin/password against localhost:5000"). When Day 6
landed the orchestrator, that login case ran end-to-end without any
synthetic seeding. The single screenshot of the failed-then-diagnosed run
in `docs/day12-demo/diagnosis.png` is the demo. I never had to mock anything.

---

## Things I got wrong (then fixed)

### "Auto-fix the schema later" — no

On Day 4 the LLM occasionally returned `"priority": "P0"` *or*
`"priority": "high"`. Pydantic accepted both because the field was `str`. By
Day 8, the review UI couldn't sort by priority. Fix: validate against an
enum at the schema layer, not at the UI. **Lesson: shape the data on the
way in, never on the way out.**

### Trying to share a TanStack Router parent between `runs.tsx` and `runs.$id.tsx`

On Day 7, I didn't realise file-based routing makes `runs.$id.tsx` a *child*
of `runs.tsx` if both exist. My runs list page didn't render an `<Outlet />`
so the detail route silently fell back to the list. Spent 20 minutes
debugging before realising the convention. Day 7 commit message has the fix.
**Lesson: when the framework offers two file shapes (`runs.tsx` vs
`runs.index.tsx`), the former is a layout and the latter is the index.
Always.**

### Premature `--bare` flag on Claude CLI

Day 3, I added `--bare` to the LLM gateway because the docs implied
"cleaner". In production, `--bare` skips the OAuth/keychain reads that the
Claude Max subscription needs to authenticate. Result: every gateway call
returned `Not logged in. Please run /login`. Caught only in the integration
smoke. **Lesson: never use a flag whose purpose I don't fully understand,
even if the docs make it sound nice.**

### `event=` is reserved

structlog reserves `event` as the log message kwarg. My hooks framework
called `_log.debug("hook.registered", event=event_name)` and the app
**failed to start** with `TypeError: _nop() got multiple values for argument
'event'`. Renamed to `hook_event=`. **Lesson: respect logger keyword
conventions. Use `hook_event` / `case_event` / etc.**

---

## Things I'd cut from v2

### `manual_edited_fields` is good. The "version chain" isn't.

Each TestCase has `version` and `prev_version_id`. I never wrote the code
to actually walk the chain or restore an old version. The fields exist
because I planned to. They're cargo. v2: drop the chain, keep
`manual_edited_fields`, and use a separate `case_versions` table only if
edit history actually gets queried.

### The `AGENT_*` events are noisier than they're worth

Every `agent.step.executed` event has 9 fields. For a 30-step run that's
270 fields × ~50 runs/day = a lot of structured noise nobody reads. v2:
collapse step events into one summary event per run with a side-table for
detail; only emit per-step in DEBUG mode.

### A pattern library matched on Jaccard keywords is fragile

The Day 11 `pattern_store` finds matches via keyword Jaccard ≥ 0.5. It works
on the dogfood case because the failure language is consistent. Real data
will rotate vocabulary. v2: use embeddings (the LLM gateway already has the
provider) for matcher, with Jaccard as a cheap pre-filter.

---

## Things I'd build first if starting over

### A `golden/` regression set on day 1

I planned this for Day 11 and ran out of time. The shape is obvious: a
small fixed-input fixture (1 PRD chapter + 1 case + 1 expected diagnosis
shape) that gets re-run whenever a prompt version bumps. Without it,
prompt changes are flying blind. Cost: ~half a day. Value: every prompt
version after.

### A budget-tracking event from day 1

LLM Gateway logs token counts per call, but I only added it as a log line.
A `today's burn` panel on the dashboard would have caught the Day 11 retry
path that hit MiniMax 4× per failed diagnosis. Cost: 30 lines of SQL +
20 lines of frontend. Value: catches runaway loops in 5 seconds.

### A `model_calls` audit table

Right now LLM-call telemetry lives in Logfire (when configured) and structlog
JSON output. That's fine for forensics but unqueryable in-app. v2: persist
every gateway call in a `model_calls` table with prompt_version,
input/output tokens, latency, cost, run_id reference. Then "which prompt
version is cheapest on average" becomes one SQL query.

---

## What surprised me

### `@playwright/mcp` works better than I expected

I budgeted 2-3 days for fallback handling when ARIA didn't cover an element.
On the demo Web app specifically, ARIA was good enough that I never wrote the
fallback. The vision-LLM-as-execution-engine plan was over-engineered for
this site. (It might still be needed for canvas-heavy UIs.)

### Claude takes a `ToolSearch` first turn even when it doesn't have to

The first thing Claude does in any `--mcp-config` session is fetch the MCP
tool schemas via its built-in ToolSearch tool. This is one extra turn,
~500ms, and a few hundred tokens — every time. Worth pre-warming via
system prompt in v2.

### The dogfood loop is a story machine

Every time something didn't work in Michelle, I had a real failure to
diagnose, a real PRD chapter to test against, a real screenshot to show.
The project's own PRD is its primary test fixture. I'd recommend this
pattern for any internal tool.

---

## What I'd say to my Day-1 self

> Don't write the vision agent. The LLM doesn't need to drive every step;
> it needs to *write the test* and *explain the failure*. Everything in the
> middle is plumbing — make the plumbing boring on purpose.

> Build the gateway first, the prompts second. The minute you have one
> working LLM client behind an interface, you've removed the biggest source
> of demo-time anxiety: "what if the model rate-limits during the
> presentation?" With fallback, you don't care.

> Take the screenshot. Always take the screenshot. Every step. Disk is
> cheap, screenshots compress well, and the trace viewer becomes 10x more
> persuasive when there's a picture next to every step row.

> Dogfood from Day 4, not Day 11. Putting your own PRD through your own
> pipeline forces every rough edge into view *while you can still fix it*.
