# Michelle PRD v1.0

> Status: strategic rewrite
>
> Product direction: AI-native test design and regression intelligence.

## 1. Overview And Positioning

Michelle helps teams turn product requirements into reviewed test coverage,
verified execution paths, and compounding regression intelligence.

It is not positioned as "AI generates many cases and runs them all." That path
creates two structural problems:

- generated cases are often low quality unless reviewed against explicit risks;
- agent-driven browser execution is too slow for broad regression.

Michelle's answer is a coverage-first and asset-first workflow:

```text
PRD
  ↓
Requirement / risk / coverage modeling
  ↓
Coverage review
  ↓
Case draft generation
  ↓
Case review
  ↓
First agentic execution
  ↓
Successful run -> stable regression asset
Failed run -> diagnosis + feedback
  ↓
Fast replay and continuous learning
```

## 2. Target Users

| User | Needs | Michelle helps by |
|---|---|---|
| Test developer | Convert PRDs into high-quality test strategy and executable regression | Extracting risks, proposing coverage, drafting cases, and building replay assets |
| QA lead | Understand release risk and regression signal | Showing coverage gaps, stale assets, flaky areas, and failure clusters |
| Developer | Understand why a regression failed | Providing trace-backed diagnosis and recurring pattern matches |
| AI coding agent | Invoke testing capabilities programmatically | Exposing REST and MCP surfaces over the same service layer |

## 3. Core Product Principles

1. **Coverage before cases**: a case should be derived from an accepted coverage
   item, not directly hallucinated from a PRD chapter.
2. **Human review at asset boundaries**: coverage, cases, and regression assets
   all need explicit review before they become trusted.
3. **Agentic execution is discovery, not the default forever**: use it to find
   and repair paths; use replay for stable regression.
4. **Every failure must have a feedback destination**: pattern, asset, case,
   coverage, or wrong diagnosis.
5. **Evidence is durable**: runs, step events, screenshots, traces, and diagnosis
   are forensic data.

## 4. Scope

### P0

- PRD upload, parsing, versioning, and chapter diff.
- Requirement extraction from selected PRD chapters.
- Risk and coverage item generation.
- Coverage review workflow.
- Case drafting from accepted coverage.
- Case review workflow.
- Agentic first execution through Playwright MCP.
- Run timeline with step events and artifacts.
- Regression asset extraction from passed runs.
- Regression asset review.
- Replay execution for approved assets.
- AI diagnosis for failed agentic and replay runs.
- Feedback routing to pattern, asset, case, and coverage.

### P1

- Batch failure clustering and root-cause summary.
- Coverage map dashboard by PRD chapter, requirement, case, run, and asset.
- Asset drift detection and repair suggestions.
- Flaky trend management.
- Release regression pack recommendation.

### Out Of Scope For Now

- Multi-tenant billing.
- API testing as a separate product line.
- Defect tracker synchronization.
- Full CI/CD productization.
- Mobile app automation.

## 5. Domain Model

### Project

Represents a product or target system under test.

Important fields:

- `project_id`
- `name`
- `base_url`
- `login_url`
- default test credentials or secret references

### PRD

Versioned source document.

Important fields:

- `prd_id`
- `project_id`
- `raw_markdown`
- `content_hash`
- `chapters`
- `version`
- `prev_version_id`

### RequirementItem

An extracted product obligation.

Important fields:

- `requirement_id`
- `project_id`
- `prd_id`
- `chapter_index`
- `chapter_hash`
- `text`
- `type`: `behavior | rule | constraint | data | permission | integration`
- `evidence`
- `confidence`
- `status`: `active | stale | rejected`

### CoverageItem

A proposed test obligation tied to a requirement and risk.

Important fields:

- `coverage_id`
- `project_id`
- `prd_id`
- `requirement_id`
- `chapter_index`
- `risk_type`: `business | validation | permission | data | integration | regression`
- `coverage_type`: `happy | edge | negative | permission | data | regression`
- `title`
- `scenario`
- `rationale`
- `priority`: `P0 | P1 | P2`
- `review_status`: `proposed | accepted | rejected | stale`
- `linked_case_id`

### TestCase

Executable draft derived from accepted coverage.

Important fields:

- `case_id`
- `project_id`
- `coverage_id`
- `name`
- `intent`
- `preconditions`
- `steps`
- `assertions`
- `quality`
- `review_status`: `pending | approved | rejected | stale`
- `manual_edited_fields`
- `version`

Manual cases may exist with `coverage_id = null`, but the UI should mark them
as untraced until linked to coverage.

### Run

Execution attempt for a case or asset.

Important fields:

- `run_id`
- `project_id`
- `case_id`
- `asset_id`
- `execution_mode`: `agentic | replay | auto`
- `status`: `pending | running | passed | failed | flaky | aborted`
- `artifacts_dir`
- `trace_jsonl_path`
- `duration_ms`
- token usage for agentic runs

### StepEvent

One persisted browser action or assertion.

Important fields:

- `run_id`
- `step_index`
- `phase`
- `tool_name`
- `tool_args`
- `tool_result`
- `screenshot_after`
- `status`
- `error_message`

### RegressionAsset

Reviewed replayable path extracted from a passed run.

Important fields:

- `asset_id`
- `project_id`
- `case_id`
- `case_version`
- `source_run_id`
- `status`: `draft | approved | deprecated`
- `action_plan`
- `locator_candidates`
- `assertions`
- `last_replay_run_id`
- `last_status`
- `created_at`
- `updated_at`

### Diagnosis

AI-generated failure analysis.

Important fields:

- `diag_id`
- `run_id`
- `case_id`
- `asset_id`
- `category`: `real_bug | flaky | selector_drift | env_issue | data_issue | case_issue | coverage_gap | unknown`
- `confidence`
- `reasoning`
- `fix_suggestion`
- `human_feedback`
- `feedback_target`: `pattern | asset | case | coverage | wrong`

### Pattern

Confirmed recurring failure signature.

Important fields:

- `pattern_id`
- `pattern_type`
- `signature`
- `suggested_action`
- `hit_count`
- `last_seen_at`

## 6. Functional Requirements

### F1. PRD Ingest

- Upload or paste markdown PRDs.
- Parse H2/H3 chapters.
- Persist full markdown and chapter hashes.
- Diff against previous PRD version.
- Mark related requirements, coverage, cases, and assets stale when their
  source chapter changes or disappears.

### F2. Test Design Generation

- Analyze selected PRD chapters.
- Extract requirement items with evidence.
- Generate coverage items from requirements and risks.
- Store generation prompt version and model version.
- Allow regeneration per chapter without overwriting accepted human decisions.

### F3. Coverage Review

- List coverage by PRD, chapter, risk type, priority, and status.
- Accept, reject, edit, or add coverage items.
- Show PRD evidence and model rationale.
- Generate case drafts only from accepted coverage.

### F4. Case Drafting And Review

- Draft cases from accepted coverage items.
- Preserve traceability to coverage and requirement.
- Validate case schema and quality flags.
- Support approve, reject, edit, and stale states.
- Preserve manually edited fields across regeneration.

### F5. Agentic First Execution

- Execute approved cases through Playwright MCP.
- Persist step events, screenshots, traces, reports, and final assertions.
- Capture enough browser evidence to extract a replayable action plan.
- Keep agentic execution bounded by timeout, max turns, and failure early-stop.

### F6. Regression Asset Extraction

- Allow a passed run to produce a draft asset.
- Extract action plan, locator candidates, assertions, and source evidence.
- Require human approval before replay becomes the default.
- Deprecate assets when source case or coverage becomes stale.

### F7. Replay Execution

- Execute approved regression assets deterministically.
- Record replay runs using the same Run and StepEvent model.
- In `auto` mode, prefer approved asset replay and use agentic execution only
  when no asset exists or fallback is requested.
- On replay failure, trigger diagnosis and offer asset repair.

### F8. Diagnosis And Feedback Routing

- Diagnose failed, flaky, and aborted runs.
- Include coverage, case, asset, failed step, trace, and screenshot context.
- Require human feedback before mutating durable learning assets.
- Route confirmed feedback to:
  - Pattern;
  - RegressionAsset;
  - TestCase;
  - CoverageItem.

### F9. Agent-Native Surfaces

- REST remains the canonical API.
- MCP tools call the same REST handlers or shared service functions.
- Agent-facing code must not duplicate business logic.

## 7. API Shape

Target REST surface:

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/prd/upload` | Upload PRD |
| POST | `/api/prd/{prd_id}/analyze` | Generate requirements and coverage |
| GET | `/api/coverage` | List coverage items |
| PATCH | `/api/coverage/{coverage_id}` | Edit coverage |
| POST | `/api/coverage/{coverage_id}/review` | Accept or reject coverage |
| POST | `/api/coverage/{coverage_id}/draft-case` | Generate case from accepted coverage |
| GET | `/api/cases` | List cases |
| PATCH | `/api/cases/{case_id}` | Edit case |
| POST | `/api/cases/{case_id}/review` | Approve, reject, or reset case |
| POST | `/api/runs` | Run cases or assets with `agentic`, `replay`, or `auto` mode |
| GET | `/api/runs/{run_id}` | Run details and step events |
| POST | `/api/runs/{run_id}/extract-asset` | Create draft asset from passed run |
| GET | `/api/regression-assets` | List assets |
| POST | `/api/regression-assets/{asset_id}/review` | Approve or deprecate asset |
| POST | `/api/regression-assets/{asset_id}/replay` | Replay an approved asset |
| POST | `/api/diagnosis/by-run/{run_id}/generate` | Diagnose failed run |
| POST | `/api/diagnosis/{diag_id}/feedback` | Route human feedback |

## 8. UI IA

Target pages:

| Page | Purpose |
|---|---|
| `/` | Dashboard and operational health |
| `/specs` | PRD upload, versions, chapter diff |
| `/design` | Requirement and coverage review |
| `/cases` | Case drafts and case review |
| `/runs` | Agentic and replay run history |
| `/runs/:id` | Timeline, screenshots, trace, diagnosis entry |
| `/assets` | Regression asset review and replay |
| `/diagnosis/:id` | Diagnosis, pattern match, feedback routing |
| `/settings` | Providers, runner, retention, project settings |

## 9. Observability Events

Event catalog should include:

| Event | Meaning |
|---|---|
| `prd.uploaded` | PRD uploaded |
| `prd.chapter.diffed` | Chapter diff computed |
| `design.requirement.generated` | Requirement item generated |
| `design.coverage.generated` | Coverage item generated |
| `review.coverage.action` | Coverage accepted/rejected/edited |
| `llm.case.drafted` | Case generated from coverage |
| `review.case.action` | Case review state changed |
| `run.created` | Run created |
| `agent.step.executed` | Agentic browser step persisted |
| `replay.step.executed` | Replay browser step persisted |
| `asset.extracted` | Draft regression asset extracted |
| `review.asset.action` | Asset approved/deprecated |
| `diagnosis.generated` | Diagnosis generated |
| `diagnosis.feedback` | Human feedback recorded |
| `pattern.matched` | Historical pattern matched |
| `pattern.absorbed` | Confirmed failure absorbed |

## 10. Quality Metrics

| Metric | Why it matters |
|---|---|
| Coverage acceptance rate | Measures usefulness of generated test design |
| Case approval rate | Measures case drafting quality |
| First agentic pass rate | Measures executability of reviewed cases |
| Asset approval rate | Measures quality of extracted replay paths |
| Replay pass rate | Measures stability of regression assets |
| Replay speedup vs agentic | Measures runtime improvement |
| Diagnosis confirmation rate | Measures diagnostic usefulness |
| Pattern match precision | Measures sediment quality |
| Coverage gap feedback count | Measures learning from failures |

## 11. Refactor Plan

1. Add `RequirementItem`, `CoverageItem`, and `RegressionAsset` models.
2. Replace PRD case generation entry point with PRD analysis.
3. Add coverage review APIs and UI.
4. Change case generation to require accepted coverage.
5. Add `coverage_id` to `TestCase`.
6. Extract draft assets from passed runs.
7. Add asset review APIs and UI.
8. Add replay runner and `execution_mode`.
9. Expand diagnosis feedback routing.
10. Add coverage map and batch failure clustering after the new spine works.

## 12. Non-Goals

- Do not preserve the old PRD-direct-to-case product entry point as a first-class
  flow.
- Do not make agentic execution the permanent default for stable regression.
- Do not automatically mutate approved assets without human review.
- Do not duplicate REST business logic in agent or MCP glue.

## 13. Success Definition

Michelle v1 is successful when a user can:

1. upload a PRD;
2. review AI-generated requirements and coverage;
3. generate cases from accepted coverage only;
4. run an approved case agentically once;
5. extract and approve a regression asset from the passed run;
6. replay that asset faster than agentic execution;
7. diagnose a replay or agentic failure;
8. route confirmed feedback back to the right durable object.

That is the new product loop.
