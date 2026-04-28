# Interview talk track — Michelle

For me, the candidate. Bring this open in a side window during interviews.

> Rule of one: **never read this verbatim**. The interviewer can tell. Use
> it as an anchor while talking naturally.

---

## 90-second elevator pitch

(Memorize. Practice in front of a mirror. ~150 words.)

> Michelle is an AI-native web test platform. The compound-engineering loop is:
> someone uploads a PRD, an LLM generates UI test cases, a human reviews them,
> approved cases get one-click executed in a real browser, failures get
> AI-diagnosed automatically, and confirmed diagnoses fold back into a pattern
> library that makes the next failure cheaper to triage.
>
> The stack is Python FastAPI with SQLModel + Alembic, a Vite + React 19 frontend,
> and execution via `claude -p --mcp-config` driving Microsoft's `@playwright/mcp`.
> AI is at two layers — generation and diagnosis — execution itself is
> deterministic via ARIA tree.
>
> I built it in twelve days, dogfooding from day four — the platform's own PRD
> generated its own tests against the demo Web app. The proof is one screenshot:
> a deliberately-failed run, diagnosed as `data_issue` with 0.80 confidence,
> human-confirmed, sediment match `hits: 3`. The whole loop closes there.

---

## 5-minute walk-through (pair with `STORY.md` screenshots)

Open `docs/STORY.md` and click through. Hit these beats in order:

1. **Dashboard** (15s) — "this is status; the work is on the next pages"
2. **PRD ingest** (45s) — paste markdown → chapter split → diff vs prev → generate. "Day-4 dogfood: I fed Michelle's own PRD into Michelle. 12 cases came out, all anchored on real facts in the document."
3. **Cases** (45s) — "drafts enter as `pending`. Reviewer approves, rejects, or inline-edits. Edited fields are tracked and protected from the next regeneration."
4. **Run timeline** (60s) — open TC-20260427-0001's most recent run. "Every step has the tool name, the natural-language intent, the page URL after the action, and a screenshot thumbnail. Click for fullscreen."
5. **AI diagnosis** (60s) — show the failed-password run. **This is the killer screenshot.** "MiniMax-Text-01 read the trace + screenshot, returned `data_issue` with confidence 0.80. I clicked confirmed. Pattern row went into the library. Re-fetching the same run shows `hits: 3` — that's the sediment loop closed."
6. **Three surfaces, one capability** (30s) — "everything you just saw via the UI also works as REST, as a Claude Code slash command (`/michelle-run`), and as MCP tools that Cursor or Windsurf can call. Anything a human can do, an agent can too."
7. **Numbers** (30s) — 12 days · 12 commits on `main` · 152 unit tests passing · 10 LLM channels with auto-fallback · 1 real the demo login walked end-to-end at 65 seconds for $0 (subscription).

---

## 15 questions interviewers actually ask, with 20-30s answers

### 1. "Why didn't you just use Selenium / Playwright directly?"

Direct Playwright works for one engineer who knows the codebase. Michelle's
question is *who else can run these tests*. AI generates them, AI diagnoses
their failures, AI users (Cursor, Windsurf) can invoke them via MCP. The
underlying browser driver is still Playwright — via `@playwright/mcp` — but
the platform layer adds the AI generation/review/diagnosis surface that raw
Playwright doesn't have.

### 2. "Why `@playwright/mcp` and not a vision-LLM agent like Midscene?"

I prototyped both on day 2. The vision-LLM-per-step path takes ~1.8s per
step and burns ~6.5k tokens. `@playwright/mcp` uses ARIA tree, runs at
~100ms per step, costs zero per-step LLM. **The AI value is in deciding
*what* to test and *why* it failed — not in clicking the button.** I keep
MiniMax in the gateway as a vision fallback for sites with thin ARIA, but
the demo target didn't need it.

### 3. "What happens when the LLM is rate-limited?"

The `LLMGateway` routes one logical `chat()` call through a chain of
clients. RateLimit / Quota / Timeout errors trigger transparent fallback to
the next provider. Auth errors don't fall through (they're config bugs, not
capacity). I have 10 channels enrolled — Claude subscription is primary
(free), Flywheel proxy + 5 OpenAI-compatible Chinese providers + MiniMax
native protocol as backups. **Demo never blocks on a rate limit.** That
choice paid off when Day-11's diagnosis call hit a vision routing issue —
two-line patch routed to MiniMax instead, no other code knew.

### 4. "How does the AI diagnosis actually work?"

Three inputs to the prompt: the case, the failed step's intent + error, and
the trace tail (last 30 step events). Plus an attached screenshot near the
failure when available. Prompt asks for strict JSON: category (one of seven),
confidence 0-1, reasoning ≤ 3 sentences, fix suggestion ≤ 1 sentence,
evidence array. Parser is lenient (handles raw newlines in strings,
fence-stripping, fall back to extracting first `{...}` block). If the
category is invalid → defaults to `unknown` with confidence 0. Diagnosis is
a row in SQLite linked to the run; human feedback (`confirmed` / `wrong` /
`partially_correct`) is a separate column. Confirmed feedback triggers
`pattern_store.absorb_diagnosis`, which folds the failure signature into
the pattern library.

### 5. "How does the sediment loop actually save time?"

When a new run fails, Michelle scores its failure signature against
accumulated patterns via Jaccard on extracted keywords (tool name, intent
words, error words). If a pattern matches above the threshold, the run's
diagnosis page surfaces "we've seen this before" with the suggested action
from the last confirmed fix. Result: **the human reviews 5 lines of context
instead of re-reading the trace from scratch.** Faster review → faster
ship → loop tightens. I'm not pretending it eliminates triage; I'm cutting
the recurring-failure case from "minutes per run" to "seconds per match."

### 6. "What stops manually-edited fields from getting overwritten on regen?"

`TestCase.manual_edited_fields` is a list of field names the human has
PATCHed. The `case_versioning.plan_regeneration` service runs before any
LLM call: if the chapter has any approved case, the action is
`skip_all_approved` (no LLM call, ever). If the chapter is unchanged versus
the previous PRD, action is `skip_unchanged`. Only added/modified chapters
without approved cases get regenerated. **Three invariants the test suite
asserts**: approved cases never auto-overwritten, manual edits preserved
across versions, removed chapters' pending cases marked `stale` (not
deleted) for human decision.

### 7. "What does scale look like — 1000 cases, 100 concurrent runs?"

Today: SQLite + local FS + asyncio.Semaphore(2) for browser concurrency. It
runs 10-20 cases comfortably on a laptop. Phase 2 path is in `db.py` and
`storage/`: swap to Postgres + MinIO + Redis Queue without touching the
service layer because everything goes through the SQLModel and `run_dir()`
abstractions. The semaphore moves to a Redis-backed lock; per-worker
concurrency stays the same. The honest answer: I built for the demo, not
for 100 concurrent runs. But the abstractions are in place.

### 8. "What would you do differently?"

(See `lessons-learned.md` for the long version.) Short version: drop the
unused version-chain fields, replace Jaccard pattern matching with
embeddings (gateway already has the providers), and build a `model_calls`
audit table from day one so "which prompt version is cheapest on average"
is one SQL query. Also: never use a CLI flag whose semantics I don't fully
understand — I burned 30 minutes on day 3 because `claude --bare` skips
the OAuth read.

### 9. "How do you know your AI diagnosis is actually correct?"

Two-part answer. First, **the human-feedback button is the ground truth**.
Confirmed/wrong/partial classifications are stored on the Diagnosis row, so
I can compute precision per category over time. Second, the **pattern
library** is the practical signal — if a category keeps getting confirmed
on similar failures, it's working; if humans keep marking the same pattern
as wrong, the matcher is over-firing. v2 plan: a `golden/` regression set
of historical failures with known-correct categories, run automatically
before every prompt-version bump, with a precision-recall threshold.

### 10. "What's your proudest piece of code?"

`backend/app/services/case_versioning.py` and the `plan_regeneration` function.
It encodes the three review invariants (approved untouched, unchanged
skipped, removed marked stale) in 60 lines, with one dataclass returned per
chapter explaining *why* the action was chosen. The 16 unit tests in
`test_review_workflow.py` cover the matrix. It's the file most likely to
get extended in v2 (per-field sediment, AI-assisted edit-merge) because the
shape is right.

### 11. "How do you handle test data and environments?"

Today: cases are tied to a project, projects have a `base_url` and default
credentials. The orchestrator passes those to `execute_v1.txt` so the AI
agent has them in context. `--isolated` on `@playwright/mcp` is on by
default so cookies don't bleed across runs (verified Day 2 — without it,
the second login run skipped the form because Chromium remembered the
session). For real environments I'd add a `Secret` model with per-project
key-value pairs surfaced as env vars to the orchestrator subprocess; today
they're just project columns.

### 12. "Why subscription instead of API key?"

I had Claude Max on day one. The interview-project version costs zero
to run as long as I stay under the rate limit. The gateway abstraction
means production swap to API key is one client class swap. Two-line change
in `app/llm/claude_cli.py` — instead of subprocessing `claude -p`, hit
`https://api.anthropic.com`. I deliberately didn't write that yet because
it would only get used when this project goes from portfolio to production.

### 13. "What's the security model?"

Honest answer: this is a portfolio MVP, single-user, no auth. The artifacts
endpoint is path-traversal safe (`test_run_artifacts.py::test_path_traversal_blocked`).
Secrets stay out of git via `.env` + gitignore. The MCP config Claude spawns
is per-run and gets a fresh tempdir. Production hardening I'd add: project-scoped
permissions, per-user API tokens, audit log on every PATCH/POST, never log
prompt content above DEBUG.

### 14. "How long would it take to onboard a new project?"

End-to-end: about 30 seconds.
- POST `/api/projects` with project_id + base_url + admin creds (10s)
- POST `/api/prd/upload` with the markdown (5s)
- POST `/api/prd/{id}/generate` to fan out cases (1-3 minutes for the LLM)
- Review the cases (depends on PRD complexity)
- Approve and run

The 30-second part assumes the PRD is already written. The dogfood case did
this end-to-end in under 90 seconds for `michelle` itself (12 cases, 3
chapters).

### 15. "Compare this to Tessera / Mabl / Testim / commercial AI testing tools."

Three things they don't do that Michelle does:
- **Compound sediment**. Mabl auto-heals selectors but doesn't accumulate
  human-confirmed failure categories. Michelle's pattern library
  ("we've seen this before") is the differentiator.
- **Three surfaces — REST + Skill + MCP**. Most commercial tools are
  Web-UI-only (you can't have an external Cursor agent invoke them).
- **Provider-agnostic LLM**. Commercial tools lock you to one model
  vendor. The 10-channel gateway means I can A/B Claude vs GPT vs Doubao
  on the same diagnosis prompt with one config flip.

What they do better: real polish, integrations, multi-tenant, customer
support. Michelle is a sharp single-purpose tool, not a product yet.

---

## Likely follow-ups (be ready)

- **"Show me where you'd add caching."** → `app/llm/gateway.py` with
  `prompt_version + hashed_prompt` as cache key; redis later, in-process
  LRU now. Cases generated from identical PRD chapters could be cached
  across re-uploads.
- **"What if the LLM hallucinates a step that isn't in the PRD?"** →
  `case_generator.GeneratedBatch` validates with Pydantic; partial-failure
  salvage (Day 4 finding) keeps good cases and drops malformed ones. The
  human review gate catches semantic hallucinations.
- **"How do you debug a flaky case?"** → `/api/runs/?case_id=TC-…` lists
  all runs, sort by status. If 8/10 passed, the heuristic classifier tags
  it `flaky`. Day 9 already retries-once on transient errors and marks
  passing-on-retry as `flaky`.
- **"What if the agent gets stuck in a loop?"** → `claude_runner` has a
  hard timeout (default 300s, configurable per case). Subprocess kill kicks
  in. The retry policy doesn't re-loop on the same error category.
- **"Can it test sites that need 2FA / OAuth?"** → Yes, but the flow has to
  be expressed as steps in the case. The agent reads the steps verbatim. I
  haven't implemented secret-injection for OTPs; that's a Phase 2 add.

---

## "Live coding" / "open the editor" questions

If asked to add a feature live, point at:
- **`app/llm/base.py`** — the `BaseChatClient` abstract class. "If I had
  to add Doubao right now, here's the file. 30 lines."
- **`app/services/diagnoser.py::_parse_diagnosis_json`** — show the
  defensive parsing. "LLMs return malformed JSON 5% of the time. This is
  why."
- **`app/services/case_versioning.py::plan_regeneration`** — the review
  invariants enforced in code.
- **`backend/tests/unit/test_diagnoser.py`** — show how the test mocks the
  gateway. "The diagnoser doesn't care which provider answers; the test
  doesn't care either."

---

## "Why isn't there X?"

- **No multi-tenant?** Out of MVP scope. Single-user dogfood platform.
- **No webhook / notifications?** Day-12 lessons-learned mentions this; it's
  a 2-hour add I deferred.
- **No CI/CD integration?** The `/api/runs` POST is the integration point —
  any CI can `curl -X POST` it. I didn't write a GitHub Action wrapper because
  it's 20 lines once needed.
- **No auth?** Same — Phase-2 hardening; production-ready system would have
  a per-project API token model.

---

## One-page cheat sheet

```
WHO    : me — built it solo in 12 days
WHAT   : AI-native web test platform · PRD → AI cases → review → run → diagnose → sediment
WHY    : compound engineering — every confirmed failure makes the next cheaper

PILLARS
  1. agent-native parity (REST + Skill + MCP)
  2. AI at the right layers (generate + diagnose, not execute)
  3. provider-agnostic LLM gateway (10 channels, auto-fallback)
  4. 3 layers of observability

NUMBERS
  12 days · 12 commits · 152 unit tests passing · 10 LLM channels
  65s end-to-end the demo login · 0.80 conf data_issue diagnosis · $0 (subscription)

KILLER SCREENSHOT
  docs/day12-demo/diagnosis.png · single image tells the whole story

CODE TO POINT AT
  services/diagnoser.py + pattern_store.py  (Day 11 sediment loop)
  llm/gateway.py + base.py                  (provider-agnostic)
  services/case_versioning.py               (3 review invariants)
  agent/claude_runner.py                    (Day 2 feasibility)

DOCS TO POINT AT
  STORY.md            5-min walkthrough
  lessons-learned.md  honest retrospective
  prd.md              1198-line dogfooded PRD
  adr/0001-0005       architecture decisions

DEMO ORDER
  1. dashboard (15s)  → 2. PRD (45s) → 3. cases (45s)
  4. run timeline (60s) → 5. diagnosis (60s) → 6. agent surfaces (30s)
  7. numbers (30s)
```

Print this. Tape it next to the screen.
