# Michelle

[![ci](https://github.com/yy757303533/michelle/actions/workflows/ci.yml/badge.svg)](https://github.com/yy757303533/michelle/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/backend%20unit-225%20passing-emerald)
![python](https://img.shields.io/badge/python-3.12+-blue)
![node](https://img.shields.io/badge/node-22+-blue)

> **AI-native Web 测试平台** · PRD → AI 生成用例 → 人工 review → 一键执行 → AI 诊断 → 沉淀闭环
>
> *not a tool, an agent that gets smarter the more it runs.*

📖 [5-min walkthrough](docs/STORY.md) · 🎬 [demo video](docs/day12-demo/walkthrough.webm) · 📋 [PRD](docs/prd.md) · 🏗 [ADRs](docs/adr/) · 🔬 [lessons learned](docs/lessons-learned.md) · 🎤 [interview talk track](docs/INTERVIEW.md)

---

## What it does in one screenshot

![AI diagnosis with sediment match](docs/day12-demo/diagnosis.png)

Michelle runs cases generated from PRDs, records each browser step, and can
diagnose failed runs from the trace and step error. When a human confirms a
diagnosis, the failure signature is folded into the pattern library; the next
similar failure can be matched without another LLM call.

That's the compound-engineering loop. Every confirmed failure makes the next
one cheaper.

---

## Pillars

1. **Agent-native parity**. Anything the Web UI can do, an agent can too — REST API, Claude Code Skill, Michelle's own MCP server.
2. **AI at the right layers**. Generation (PRD → cases), execution planning (Michelle's generic JSON-action loop), and diagnosis (failure → root cause). Browser effects are still deterministic via [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp).
3. **simple LLM routing**. The product surface supports `claude-cli` and `codex-cli`: generation, execution, and diagnosis each have an explicit route.
4. **Three layers of observability**. OpenTelemetry (infra) + Event Catalog (business) + AI diagnosis (read-the-trace-for-you, on top).

---

## Tour

| Page | Screenshot |
|---|---|
| **Dashboard** — live counts + LLM routing/status | [![dashboard](docs/day12-demo/dashboard.png)](docs/day12-demo/dashboard.png) |
| **PRD ingest** — paste markdown, see chapter diff vs prev version, generate per-chapter | [![prd](docs/day12-demo/prd.png)](docs/day12-demo/prd.png) |
| **Cases** — review queue, batch approve/reject, inline edit (tracked in `manual_edited_fields`) | [![cases](docs/day12-demo/cases.png)](docs/day12-demo/cases.png) |
| **Runs** — newest-first, filterable by status | [![runs](docs/day12-demo/runs.png)](docs/day12-demo/runs.png) |
| **Run timeline** — every tool call + thumbnail screenshot + lightbox + URL trail | [![run-detail](docs/day12-demo/run-detail.png)](docs/day12-demo/run-detail.png) |
| **AI diagnosis** — category / confidence / reasoning / fix + sediment matches | [![diagnosis](docs/day12-demo/diagnosis.png)](docs/day12-demo/diagnosis.png) |

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12+ · FastAPI · SQLModel + Alembic · structlog · OpenTelemetry · pytest | LLM ecosystem is best in Python; async-first |
| Frontend | Vite + React 19 + TypeScript · TanStack Router/Query · Tailwind · shadcn-ui | SPA matches separated backend; no Next.js bloat needed |
| Execution | Michelle generic loop + `@playwright/mcp` (ARIA tree), with Claude CLI loop as compatibility fallback | Provider-portable execution, deterministic browser actions, traceable step timeline |
| Storage | PostgreSQL + local FS artifacts | Internal pilot defaults to Postgres; artifacts stay on local/shared disk |
| LLM Gateway | `claude-cli` + `codex-cli` | Simple internal rollout surface |

## LLM Providers

| pri | provider | type | enable |
|---:|---|---|---|
| 10 | **claude-cli** | subscription CLI(主) | `claude` 已登录 |
| 15 | **codex-cli** | subscription CLI | `codex` + `CODEX_ENABLED=true` |

```python
result = await gateway.chat(
    "your prompt",
    prompt_version="case_gen_v1",
    prefer="codex-cli",  # optional
    json_mode=True,      # optional
)
```

Generation, diagnosis, and the generic executor loop use this gateway. The UI
exposes three routes: Generate cases, Execute cases, Diagnose failures.

## Execution Loop

Michelle owns the browser agent loop unless Execute cases is set to
`claude-cli`. With `codex-cli`, Michelle asks Codex for one strict JSON action
per turn, calls Playwright MCP directly, persists each tool result into the
run timeline, and only accepts a final result when observed evidence backs the
assertions. With `claude-cli`, Michelle delegates execution to the Claude CLI
browser loop.

---

## Agent-native triple surface

The same `execute_case` capability is reachable three ways:

| Surface | For | Example |
|---|---|---|
| **REST** `/api/...` | Web UI / any HTTP client | `curl -X POST /api/runs '{"case_ids":["TC-..."]}'` |
| **Claude Code Skills** `.claude/skills/` | Claude Code terminal users | `/michelle-run TC-20260427-0001` |
| **MCP server** `backend/app/mcp/` | other AI clients (Cursor / Windsurf / custom agents) | `michelle.execute_case(case_id="TC-...")` |

Anything a human can do, an agent can too.

---

## Quick start

```bash
# 1. Install deps + verify CLI tools (claude / npx / playwright chromium / docker not needed)
make setup

# 2. Copy env template and point DATABASE_URL at Postgres
cp .env.example .env

# 3. Start local Postgres, then bring up backend (:8000) + frontend (:5173)
make postgres
make dev
```

Open `http://localhost:5173`. Upload a PRD on the PRD page, generate cases,
approve, ▶ Run. When something fails, the AI diagnose button on the run
page wires the rest.

The default `DATABASE_URL` is PostgreSQL:
`postgresql+asyncpg://michelle:michelle@127.0.0.1:5432/michelle`.
For shared/internal pilot environments, set `APP_ENV=shared`, change
`DEFAULT_ADMIN_PASSWORD`, configure `ADMIN_TOKEN`, and point `DATABASE_URL`
at the shared Postgres instance. SQLite remains available only as a local
single-user fallback.

End-to-end smoke scripts (real LLM, real browser, real target Web app):

```bash
cd backend
uv run python ../scripts/day2_smoke.py            # executor loop + @playwright/mcp drives a real login
uv run python ../scripts/day4_dogfood.py          # generate cases from Michelle's own PRD
uv run python ../scripts/day7_visual_smoke.py     # screenshot every page
uv run python ../scripts/day12_demo_capture.py    # full walkthrough video
uv run python ../scripts/day13_e2e_smoke.py       # real target E2E + diagnosis smoke
```

---

## Project layout

```
backend/
  app/
    api/            REST: prd, cases, runs, projects, diagnosis, llm
    services/       prd_parser, prd_diff, case_generator, case_versioning,
                    run_orchestrator, report_html, diagnoser, pattern_store
    agent/          generic_runner, claude_runner, executor, mcp_stdio,
                    mcp_config, trace_parser, hooks
    llm/            provider-agnostic gateway + 10 clients + versioned prompts/
    mcp/            Michelle's own MCP server — 6 tools
    obs/            structlog + OTel + Event Catalog
    models/         Project / PRD / TestCase / Run / StepEvent / Diagnosis / Pattern
    storage/        local FS (MVP) — interface ready for MinIO
  alembic/          schema migrations (initial revision covers all 7 tables)
  tests/            225 unit tests, 1 integration smoke (gated by env)
frontend/           Vite + React 19 + TS + TanStack Router/Query + Tailwind
.claude/
  skills/           michelle-run / michelle-diagnose / michelle-suggest
  agents/           michelle-diagnoser / michelle-reviewer
vendor/
  webtest-mcp/      forked from author's prior project — Excel schema + HTML report
docs/
  prd.md            1198-line PRD (Michelle's own, dogfooded)
  STORY.md          5-minute interview walkthrough
  lessons-learned.md   honest retrospective
  adr/              5 architecture decision records
  day*-findings.md  daily implementation diaries
  day7-screens/     visual smoke screenshots
  day10-screens/    trace viewer + lightbox demo
  day11-sample/     real diagnosis JSON
  day12-demo/       full-flow screenshots + walkthrough.webm
scripts/            re-runnable end-to-end smokes
CLAUDE.md           orientation for any future Claude Code session
```

---

## Progress

| Day | What | Tests |
|---|---|---|
| 1 | Project skeleton + agent-native surface | 6 |
| 2 | claude + `@playwright/mcp` drives a real login | 10 |
| 3 | LLM Gateway with auto-fallback | 34 |
| 4 | PRD ingest + AI case generation + dogfood (12 cases) | 59 |
| 5 | Alembic + HTML report generator | 74 |
| 6 | Run Orchestrator end-to-end + CLI provider routing | 104 |
| 7 | Frontend integration polish + visual smoke | 104 |
| 8 | Review workflow (manual edit, bulk, stale, version protection) | 120 |
| 9 | Batch concurrency + retry + heuristic classification | 128 |
| 10 | Trace Viewer with screenshot timeline + lightbox | 135 |
| 11 | **AI diagnosis + sediment loop** ← compound engineering closes here | 152 |
| 12 | Demo capture + README + STORY + lessons-learned | 152 |
| Current | Review workflow, runtime settings, runner status, artifacts hardening, email notifications, queue/cancel/trends/admin token | 225 |

---

## Acknowledgements

- **`@playwright/mcp`** — Microsoft's Playwright MCP server (execution engine)
- **[webtest-mcp-server](https://github.com/yy757303533/webtest-mcp-server)** — author's prior project, forked into `vendor/`
- **Compound Engineering** — Every team (Dan Shipper, Kieran Klaassen) for the framing
- **OpenTelemetry / Logfire** — observability standards

## License

TBD.
