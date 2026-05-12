# Michelle Pilot Guide

The pilot should validate the new thesis:

> Michelle improves test quality by reviewing coverage before cases, then
> improves regression speed by turning successful agentic runs into replayable
> assets.

## 1. Pilot Setup

```bash
cp .env.example .env
make setup
make postgres
make dev
```

Use PostgreSQL for any shared pilot. Configure:

- project base URL;
- login URL when applicable;
- test credentials or secret references;
- LLM provider for design and diagnosis;
- runner status for Playwright MCP.

## 2. Pilot Scope

Choose one small product area with:

- a real PRD or feature spec;
- 5-10 meaningful requirements;
- a browser-accessible staging environment;
- stable test data;
- one reviewer who understands expected behavior.

Avoid starting with broad regression. The first pilot should prove the loop:

```text
PRD -> coverage review -> case review -> agentic run -> asset -> replay -> diagnosis feedback
```

## 3. Acceptance Flow

1. Upload PRD.
2. Analyze selected chapters into requirements and coverage.
3. Reviewer accepts/rejects coverage.
4. Generate cases only from accepted coverage.
5. Reviewer approves cases.
6. Run approved cases agentically.
7. Extract draft assets from passed runs.
8. Reviewer approves useful assets.
9. Replay approved assets.
10. Diagnose failures and route feedback to pattern, asset, case, or coverage.

## 4. Pilot Metrics

Track these numbers:

| Metric | Target question |
|---|---|
| Coverage acceptance rate | Did AI propose useful test design? |
| Case approval rate | Did accepted coverage produce usable cases? |
| First agentic pass rate | Were reviewed cases executable? |
| Asset extraction rate | Did successful runs produce replayable assets? |
| Asset approval rate | Were extracted assets trustworthy? |
| Replay speedup | Is replay materially faster than agentic execution? |
| Replay pass rate | Are assets stable enough for regression? |
| Diagnosis confirmation rate | Are failure explanations useful? |
| Feedback routing distribution | Are failures improving the right object? |

## 5. Human Review Rules

Human review is mandatory for:

- accepting coverage;
- approving cases;
- approving regression assets;
- confirming diagnosis feedback.

Do not automatically approve durable assets from model output alone.

## 6. Diagnosis Trust Boundaries

AI diagnosis must be manually reviewed when:

- confidence is below 0.7;
- screenshot or trace evidence is missing;
- failure involves permissions, payments, destructive actions, or production data;
- replay failed after a recent product change;
- a pattern match appears after major UI redesign;
- the suggested fix mutates an approved asset or case.

## 7. Pilot Exit Criteria

The pilot is successful when:

- reviewers accept a meaningful share of generated coverage;
- approved coverage produces executable cases;
- at least one successful agentic run becomes an approved regression asset;
- replay is faster than agentic execution for the same flow;
- at least one failure diagnosis is confirmed and routed to the correct target;
- the team can explain what changed in coverage, case, asset, or pattern because
  of that feedback.

## 8. What Not To Measure First

Do not optimize for:

- number of generated cases;
- number of browser runs;
- fully autonomous approval;
- 100% pass rate;
- broad CI integration.

The first pilot validates quality of the loop, not scale.
