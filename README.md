# Michelle

[![ci](https://github.com/yy757303533/michelle/actions/workflows/ci.yml/badge.svg)](https://github.com/yy757303533/michelle/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.12+-blue)
![node](https://img.shields.io/badge/node-22+-blue)

> **AI-native test design and regression intelligence platform**
>
> PRD -> risk and coverage model -> reviewed case drafts -> first agentic run ->
> stable regression asset -> fast replay -> diagnosis feedback loop.

Michelle is moving away from "generate many test cases and run them slowly" and
toward a more durable testing workflow:

> **AI proposes the test design. Humans approve the important assets. The system
> compounds verified execution paths and confirmed failures into regression
> intelligence.**

## Product Thesis

Traditional AI testing tools over-index on case generation. That creates two
hard problems:

- generated cases are uneven, repetitive, or not executable;
- agent-driven browser runs are too slow for broad regression.

Michelle treats those as product constraints, not edge cases. The platform uses
AI where it has leverage:

1. **Test design**: read a PRD, extract requirements, identify risks, and propose
   a coverage plan that a reviewer can accept or reject.
2. **Case drafting**: generate executable case drafts only from accepted coverage
   items, with traceability back to PRD evidence.
3. **First-run exploration**: use an agentic browser run to discover and verify a
   working path through the UI.
4. **Regression asset sediment**: turn successful runs into reviewed action
   plans with locator candidates and assertions.
5. **Fast replay**: run approved regression assets deterministically whenever
   possible; fall back to agentic execution only when the asset fails.
6. **Failure learning**: diagnose failed runs, then route human-confirmed feedback
   back to patterns, assets, cases, or coverage gaps.

## Target Workflow

```text
PRD
  ↓
Requirement / risk / coverage modeling
  ↓
Coverage plan review
  ↓
Case draft generation
  ↓
Case review
  ↓
First agentic execution
  ↓
Passed run -> draft regression asset -> asset review
Failed run -> AI diagnosis -> human feedback
  ↓
Fast regression replay
  ↓
Replay failure -> diagnosis + optional agentic fallback
  ↓
Feedback updates:
  - Pattern library
  - Regression asset
  - Test case
  - Coverage plan
```

## Core Concepts

| Concept | Role |
|---|---|
| **PRD** | Source document and versioned requirement input. |
| **Requirement Item** | A behavior, rule, constraint, permission, or data expectation extracted from the PRD. |
| **Coverage Item** | A proposed test obligation tied to a requirement and risk type. This is the first human review gate. |
| **Test Case** | An executable draft derived from accepted coverage. This is still reviewed before execution. |
| **Run** | Evidence from either agentic execution or deterministic replay. |
| **Step Event** | Browser action, assertion, URL, screenshot, and error evidence captured during a run. |
| **Regression Asset** | A reviewed, replayable action plan extracted from a successful run. |
| **Diagnosis** | AI analysis of a failed run. Human feedback decides where the lesson lands. |
| **Pattern** | A confirmed recurring failure signature used to identify similar failures faster. |

## Architecture Direction

The existing codebase already has useful foundations: PRD parsing and diffing,
case review, run orchestration, step timelines, diagnosis, pattern storage,
REST APIs, and a React UI. The refactor changes the product spine:

- `case_generator` becomes a downstream case drafter, not the first PRD output.
- New `test_design_planner` owns requirement and coverage generation.
- New coverage APIs become the entry point before cases are created.
- New `regression_asset_builder` extracts action plans from successful runs.
- New replay execution mode uses approved assets before falling back to the
  slower agentic loop.
- Diagnosis feedback expands from "confirm pattern" to "update pattern, asset,
  case, or coverage."

## Target Backend Layout

```text
backend/app/
  api/
    prd.py                 PRD upload, versioning, analysis kickoff
    coverage.py            coverage item list/review/draft-case
    cases.py               case review and editing
    runs.py                agentic/replay/auto execution
    regression_assets.py   asset review, replay, deprecation
    diagnosis.py           diagnosis and feedback routing
  services/
    prd_parser.py
    prd_diff.py
    test_design_planner.py
    case_drafter.py
    run_orchestrator.py
    regression_asset_builder.py
    replay_runner.py
    diagnoser.py
    pattern_store.py
  models/
    project.py
    requirement.py
    coverage.py
    case.py
    run.py
    regression_asset.py
    diagnosis.py
    pattern.py
```

## Execution Strategy

Michelle should not use an LLM for every browser step forever.

- **Agentic first run**: useful for new flows, changed UI, and asset repair.
- **Approved regression asset replay**: default for stable regression.
- **Auto mode**: choose replay when an approved asset exists; otherwise run
  agentic. If replay fails, diagnose and optionally fall back to agentic.

This gives the platform a credible answer to runtime cost: pay the slow agentic
cost once to discover a path, then replay the verified asset quickly.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, FastAPI, SQLModel, Alembic, pytest |
| Frontend | Vite, React, TypeScript, TanStack Router/Query |
| Browser | Playwright MCP for deterministic browser actions |
| LLM access | `claude-cli` and `codex-cli` through `backend/app/llm/gateway.py` |
| Storage | PostgreSQL plus local artifact storage |

## Development

```bash
make setup
make postgres
make dev
```

Open `http://localhost:5173`.

Useful checks:

```bash
cd backend && uv run pytest tests/unit
cd backend && uv run ruff check .
cd frontend && pnpm lint
```

## Documentation

- [Product PRD](docs/prd.md)
- [Target walkthrough](docs/STORY.md)
- [Operations guide](docs/OPERATIONS.md)
- [Pilot guide](docs/PILOT.md)
- [Refactor plan](docs/REFACTOR_PLAN.md)
- [Interview talk track](docs/INTERVIEW.md)
- [Architecture decisions](docs/adr/)

## Positioning

Short version:

> Michelle turns PRDs into reviewed test coverage, verified execution paths, and
> compounding regression intelligence.

Long version:

> Michelle is an AI-native test design and regression intelligence platform. It
> helps teams identify product risks from PRDs, review coverage before generating
> cases, verify new UI flows through agentic execution, sediment successful runs
> into stable replayable assets, and use confirmed diagnosis feedback to improve
> future coverage, cases, assets, and failure triage.
