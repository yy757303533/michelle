---
name: michelle-diagnose
description: Trigger AI diagnosis on a failed Michelle run and return root-cause analysis with fix suggestions. Use when a run has failed and the user wants to know why before re-running.
allowed-tools: [Bash]
---

# /michelle-diagnose — AI diagnosis for failed runs

## Args

`$ARGUMENTS` — a `run_id`. If empty, ask the user.

## Steps

1. Check the run exists and has failed steps:
   ```
   curl -s http://localhost:8000/api/runs/<run_id>
   ```
2. Trigger diagnosis (if not already produced):
   ```
   curl -s -X POST http://localhost:8000/api/diagnosis/<run_id>/generate
   ```
3. Print the diagnosis structured:
   - `category` (real_bug / flaky / selector_drift / vision_misjudge / env_issue / data_issue)
   - `confidence` (0.0–1.0)
   - `reasoning` (LLM explanation)
   - `fix_suggestion` (concrete next action)
4. Ask the user: "Was this diagnosis correct? (confirmed/wrong/partially)" and
   POST the feedback to `/api/diagnosis/<diag_id>/feedback`.

## Why feedback matters

Confirmed/rejected diagnoses train Michelle's prompt and pattern library.
Without feedback, the platform stops getting smarter.

## Notes

- Diagnosis itself is an LLM call — takes 5-30s depending on which model is healthy.
- If MiniMax-M2.7 (reasoning model) is configured, diagnoses are deeper but slower.
