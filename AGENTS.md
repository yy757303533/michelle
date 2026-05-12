# AGENTS.md - Michelle project context for Codex and other coding agents

This file is the repo-level orientation for coding agents working in Michelle.
It complements `CLAUDE.md`, which is Claude Code-specific. Keep this file
tool-neutral and focused on codebase conventions.

## What Michelle Is

Michelle is an AI-native test design and regression intelligence platform:

PRD -> requirement/risk/coverage modeling -> coverage review -> case drafting ->
case review -> first agentic execution -> regression asset -> fast replay ->
diagnosis feedback.

The project has intentionally moved away from a case-first product story. Do
not build new primary flows where a PRD directly generates executable cases.
The new spine is coverage-first and asset-first:

- AI proposes requirements, risks, and coverage items.
- Humans review coverage before cases exist.
- Cases are drafted from accepted coverage.
- Successful agentic runs become reviewed regression assets.
- Approved assets replay quickly; agentic execution is for discovery, fallback,
  and repair.
- Diagnosis feedback routes to Pattern, RegressionAsset, TestCase, or
  CoverageItem.

The canonical platform API is REST under `/api/...`. Agent-facing integrations
should call the same REST handlers or shared service functions used by the Web
UI; do not duplicate business logic in agent glue.

## Important Boundaries

- Web UI, REST API, and agent surfaces should have feature parity.
- Test execution uses Playwright MCP through deterministic browser actions.
- Do not add per-step vision LLM calls unless ARIA/Playwright MCP cannot cover
  the target site.
- New case generation work should go through accepted coverage items, not raw
  PRD chapters.
- Regression replay should be preferred over agentic execution whenever an
  approved asset exists.
- LLM calls must go through `backend/app/llm/gateway.py` unless you are editing
  a provider implementation itself.
- Business events should use names from `backend/app/obs/events.py`.
- Prompt changes go in versioned files under `backend/app/llm/prompts/`; keep old
  prompt versions for reproducibility.

## Current Agent Surfaces

- `.claude/` contains Claude Code skills and subagent definitions for human
  Claude Code users. The backend does not read this directory at runtime.
- `backend/app/mcp/` is Michelle's own MCP server surface for MCP clients.
- `CLAUDE.md` is Claude Code orientation.
- `AGENTS.md` is the general coding-agent orientation.

Important: the backend's Codex provider currently invokes `codex exec` as an
isolated LLM subprocess with `--ephemeral --ignore-rules --skip-git-repo-check`
and `cwd=/private/tmp`. That runtime path intentionally does not read this file,
`.claude/`, or other repo agent rules. This file is for coding agents working on
the repository, not for case-execution LLM calls.

## Repo Layout

```text
backend/                   FastAPI, agent execution, LLM gateway
  app/
    api/                   REST routes
    services/              PRD parsing, test design, case drafting,
                            run orchestration, replay, diagnosis
    agent/                 Playwright MCP runners, trace parsing, hooks
    llm/                   provider gateway, CLI clients, prompt registry
    mcp/                   Michelle MCP server
    models/                SQLModel models
    obs/                   logging, tracing, event catalog
    storage/               local artifact storage
  tests/unit/              pytest unit tests
frontend/                  Vite, React, TypeScript, TanStack Router/Query
docs/                      PRD, ADRs, operations docs
.claude/                   Claude Code skills and agent definitions
```

## Development Commands

Run backend commands from `backend/` unless noted.

```bash
make dev
cd backend && uv run pytest tests/unit
cd backend && uv run ruff check .
cd frontend && pnpm lint
```

For narrow changes, run the smallest relevant tests first, then broaden when the
change touches shared behavior.

## Backend Conventions

- Python 3.12+.
- Use `uv` for dependency and command execution.
- Async-first FastAPI and SQLModel `AsyncSession`.
- Prefer service-layer functions shared by REST and agent/MCP surfaces.
- Use `from __future__ import annotations` in new Python modules.
- Keep public functions typed.
- Unit tests must mock LLM subprocesses and network calls unless explicitly
  gated as real integration tests.

## Frontend Conventions

- Vite + React + strict TypeScript.
- TanStack Router file routes live in `frontend/src/routes/`.
- TanStack Query owns server state.
- Do not hand-edit generated route tree files unless the local pattern requires
  it and you verify the result.
- Keep operational UI dense, clear, and task-focused.

## Execution Notes

- Claude execution path: `backend/app/agent/claude_runner.py` spawns `claude -p`
  with a per-run MCP config.
- Generic/Codex execution path: `backend/app/agent/generic_runner.py` owns the
  browser loop and calls the LLM gateway for JSON actions.
- Agentic execution should discover or repair paths. Stable regression should
  use reviewed RegressionAsset replay.
- Playwright MCP output must stay inside the per-run artifact directory so
  screenshots and traces remain available from `/api/runs/{id}/artifacts/...`.
- Run history and artifacts are forensic data. Do not delete or rewrite them
  unless the user explicitly asks or the product flow is deleting the owning
  case.

## Git And Safety

- The working tree may contain user changes. Do not revert unrelated changes.
- Prefer narrow commits with tests that cover the changed behavior.
- Do not use destructive git commands unless explicitly requested.
- If the user asks to push, push the current branch to the requested remote and
  branch after tests pass.
