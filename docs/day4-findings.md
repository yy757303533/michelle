# Day 4 Findings — PRD ingest + AI case generation

**Date**: 2026-04-27
**Outcome**: ✅ End-to-end PRD → cases pipeline alive. **Michelle generated 12 schema-valid test cases for itself.**

## What we proved

Real Claude (Opus 4.7) consumed three chapters of Michelle's own PRD and produced
schema-valid, executable test cases. Each case has steps, assertions, preconditions,
and AI-written `coverage_notes` explaining why each bucket was filled or skipped.

Sample (full file at `docs/day4-dogfood-sample.json`):

```
TC-20260427-0001 [P0] 使用确认账号 admin/password 成功登录 the demo Web app
  steps: 5 (open URL → type admin → type password → click 登录 → wait for dashboard)
  assertions: 3 (URL changed, user badge visible, post-login menu present)
```

Notice the LLM correctly read `A3 Staging 目标` from the confirmed-items table,
extracted the URL `http://localhost:5000/` and credentials `admin / password`,
and produced an executable case anchored on those facts.

## Numbers

| Metric | Value |
|---|---|
| PRD source | `docs/prd.md` (1198 lines, Michelle's own PRD) |
| Chapters detected | 60 |
| Chapters fed to LLM | 3 (the first three H2 sections) |
| Cases produced | 12 |
| Avg cases/chapter | 4 |
| Avg steps/case | 5 |
| Avg assertions/case | 2.7 |
| Schema validation | 12/12 passed |
| LLM model used | `claude-opus-4-7[1m]` (via subscription, $0) |

## What worked well

- **Coverage-bucket prompt control**: telling the model "happy / edge / error / security"
  and asking for `coverage_notes` consistently produced a sane mix and a written
  rationale per chapter. The "background-only" chapter (§2) was correctly identified
  by the LLM as not directly testable, and the model pivoted to "validate Michelle's
  own promised behaviour" cases — a defensible interpretation we didn't pre-program.

- **Chapter-level granularity**: each chapter generation is one LLM call. Failures
  in one don't kill the others.

- **Schema validation salvages partial outputs**: the case_generator catches
  `ValidationError`, salvages individual valid cases, skips the broken ones. We
  saw 0 broken cases on this run, but the safety net is there.

## Things observed but not yet fixed

- **Background chapters** (like §2 problem statement) get cases that are valid
  but tangentially related to the chapter's text. The LLM essentially generates
  cases based on the *overall product context* when the chapter has no
  testable behaviour. This is fine for MVP — humans review and reject anything
  off. Day 8's review workflow handles it.

- **`module` defaults to chapter title**, which can be a long Chinese phrase
  ("0. 已确认事项"). UX-wise we'll want a short module slug. For now `module`
  is informational.

- **`generated_from` field** carries `chapter:<normalized_title>#<position>`.
  When the PRD is re-uploaded, we can find the cases whose source chapter
  changed and mark them `stale`. Day 8 wires this up.

## Cost / latency observations

- 3 chapters × ~30 seconds/call ≈ 90 seconds total wall time
- Subscription mode: $0 actual; reported per-call cost ranged $0.10-$0.30
- Cache reads were minimal (each chapter is unique input)
- No rate-limit hit even running 3 calls in immediate succession

## What this unlocks for the demo

- **Dogfood story**: "I fed Michelle's own PRD to Michelle and got 12 cases —
  one of which was 'verify admin/password logs into the demo target', exactly what
  I need to test the platform." This is a complete narrative loop.

- **Real seed for Day 6**: when execution lands, we have 12 real cases waiting
  for their first run. The TC-20260427-0001 login case is the obvious one to
  run first end-to-end.

## Day 5 reading list

Vendoring webtest-mcp's HTML report generator — the `save_test_results` function
in `vendor/webtest-mcp/src/webtest_mcp/server.py` is the chunk to lift. Its
input shape (per-case results with screenshots) maps directly onto our `Run`
+ `StepEvent` + `artifacts/` triple.
