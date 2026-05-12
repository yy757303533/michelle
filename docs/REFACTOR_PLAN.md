# Michelle Refactor Plan

This plan moves Michelle from case-first to coverage-first and asset-first.

## Product Spine

```text
PRD
  ↓
RequirementItem + CoverageItem
  ↓
Coverage review
  ↓
TestCase draft
  ↓
Case review
  ↓
Agentic first run
  ↓
RegressionAsset
  ↓
Replay
  ↓
Diagnosis feedback routing
```

## Keep

Keep and adapt:

- PRD parsing and chapter diff.
- Project settings and target credentials.
- TestCase review machinery.
- Run and StepEvent evidence model.
- Run timeline and artifact serving.
- LLM gateway and prompt registry.
- Diagnosis generation.
- Pattern store.
- REST as canonical API.

## Delete Or Demote

Delete or demote as first-class product paths:

- PRD-direct-to-case generation UI.
- `coverage_notes` as the only coverage representation.
- Queue as a standalone product concept if runs/assets can show execution state.
- Any new workflow where generated cases bypass coverage review.
- Any new workflow where stable regression always uses agentic execution.

Historical files and fixtures can remain as records, but new product docs and UI
should not present the old flow as the main path.

## Add Models

Add:

- `RequirementItem`
- `CoverageItem`
- `RegressionAsset`

Adapt:

- `TestCase.coverage_id`
- `Run.execution_mode`
- `Run.asset_id`
- `Diagnosis.asset_id`
- `Diagnosis.feedback_target`

## Add Services

Add:

- `test_design_planner.py`
- `case_drafter.py`
- `regression_asset_builder.py`
- `replay_runner.py`
- `feedback_router.py`

Adapt:

- `case_generator.py` should become compatibility or implementation detail for
  `case_drafter.py`.
- `run_orchestrator.py` should select agentic/replay/auto mode.
- `diagnoser.py` should keep generation but delegate confirmed feedback effects
  to `feedback_router.py`.

## Add API Routes

Add:

- `/api/coverage`
- `/api/regression-assets`

Adapt:

- `/api/prd/{prd_id}/generate` should become `/api/prd/{prd_id}/analyze`.
- `/api/runs` should accept `execution_mode`.
- `/api/diagnosis/{diag_id}/feedback` should accept `feedback_target`.

## Frontend Pages

Target IA:

- `/specs`: PRDs and chapter diffs.
- `/design`: requirements and coverage review.
- `/cases`: cases derived from accepted coverage.
- `/runs`: agentic and replay history.
- `/assets`: regression assets.
- `/diagnosis/:id`: failure diagnosis and feedback routing.

## Migration Order

1. Add models and migrations.
2. Add test design planner with mocked LLM tests.
3. Add coverage API and minimal design page.
4. Change case drafting to require accepted coverage.
5. Add `coverage_id` traceability to cases.
6. Extract draft asset from passed run.
7. Add asset review API and page.
8. Add replay runner.
9. Add `execution_mode=auto`.
10. Expand diagnosis feedback routing.
11. Remove old PRD-direct case generation from the UI.

## First Verification Milestone

A local smoke should prove:

1. Upload PRD.
2. Analyze one chapter into coverage.
3. Accept one coverage item.
4. Draft one case.
5. Approve the case.
6. Run agentically.
7. Extract draft asset from a passed run.
8. Approve the asset.
9. Replay the asset.
10. Diagnose a forced failure and route feedback.

This is the first complete version of the new loop.
