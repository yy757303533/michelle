# Interview Talk Track - Michelle

Use this as a speaking anchor, not a script.

## 90-Second Pitch

Michelle is an AI-native test design and regression intelligence platform.

The first version proved a PRD-to-case-to-run-to-diagnosis loop. The important
learning was that two problems dominate the product: raw generated cases are not
consistently good enough, and agentic browser execution is too slow to be the
default for every regression.

So the product direction changed. Michelle now treats AI case generation as a
downstream step, not the product center. The new loop is:

> PRD -> requirement and risk extraction -> reviewed coverage plan -> case
> drafts -> first agentic run -> reviewed regression asset -> fast replay ->
> diagnosis feedback.

AI proposes the test design. Humans approve the important boundaries. Successful
runs become stable replayable assets. Failed runs become feedback that improves
patterns, assets, cases, or coverage.

The thesis is simple: Michelle should not generate more tests; it should help
teams build better regression intelligence.

## 5-Minute Walkthrough

1. **Dashboard**: show provider status, runner health, asset/replay health, and
   pending review counts.
2. **PRD upload**: paste a real feature spec. Michelle splits chapters and tracks
   version diffs.
3. **Test Design**: show requirements, risks, and coverage items. This is the
   key product shift: review coverage before cases.
4. **Case drafts**: generate executable cases only from accepted coverage.
5. **First agentic run**: run one approved case and show the step timeline with
   screenshots, URLs, and assertions.
6. **Regression asset**: extract a draft asset from the passed run, then approve
   it.
7. **Replay**: run the approved asset quickly without asking the LLM to decide
   every browser step.
8. **Diagnosis feedback**: diagnose a failed run and route feedback to pattern,
   asset, case, or coverage.

## Why The Pivot Matters

The naive AI testing story is:

```text
PRD -> AI generates cases -> run all cases
```

That sounds attractive, but it breaks down:

- the model may generate vague, duplicate, or non-executable cases;
- running every case with an agent is too slow;
- pass/fail alone does not create durable regression value.

Michelle's revised story is:

```text
PRD -> reviewed coverage -> reviewed case -> verified path -> replayable asset
```

That turns uncertainty into process:

- coverage review controls test design quality;
- case review controls execution quality;
- asset review controls long-term regression stability;
- diagnosis feedback controls system learning.

## Questions And Answers

### 1. Why not directly generate test cases from the PRD?

Because case generation is only as good as the test design behind it. If the AI
misses a risk, duplicates a happy path, or invents a weak assertion, the case may
look plausible but fail to create quality signal.

Michelle generates requirements and coverage first. A human reviews the coverage
plan, then cases are drafted from accepted coverage. That makes the case a
derived artifact from a reviewed test obligation.

### 2. What is a coverage item?

A coverage item is a proposed test obligation tied to a PRD requirement and a
risk type. For example:

```text
Requirement: Users with wrong password cannot log in.
Risk: authentication / data / security.
Coverage: negative path - wrong password shows rejection and does not create a session.
```

The reviewer accepts or rejects that before a concrete case is drafted.

### 3. Why keep case review if coverage is already reviewed?

Coverage answers "should this risk be tested?" Case review answers "is this
specific executable procedure correct?"

They are different gates. Coverage review prevents low-value scenarios. Case
review prevents bad steps, missing preconditions, weak assertions, or test data
issues.

### 4. What is a regression asset?

A regression asset is a reviewed replayable action plan extracted from a passed
run. It includes:

- actions;
- locator candidates;
- assertions;
- source run evidence;
- case version;
- target project/environment context.

It is not a full DOM snapshot and not "all page elements." It is the minimum
stable path needed to replay a verified case.

### 5. Why not use the agent every time?

Agentic execution is useful for discovery and repair, but expensive for stable
regression. Once a path is known and approved, direct replay is faster and more
predictable.

Michelle uses agentic execution to find the path, then turns the verified path
into an asset. Future runs use the asset first and fall back to the agent only
when replay fails or no asset exists.

### 6. How does diagnosis fit after replay?

Replay failure can mean several things:

- product bug;
- locator drift;
- changed data;
- stale case;
- missing coverage;
- flaky environment.

Diagnosis reads the run evidence and proposes a category and fix. Human feedback
then routes the learning to the right place: pattern, asset, case, coverage, or
wrong diagnosis.

### 7. What makes this a loop?

Every major artifact can be improved by feedback:

- confirmed recurring failures become patterns;
- locator drift updates regression assets;
- bad steps update cases;
- uncovered risks update coverage;
- wrong diagnoses are stored as negative feedback.

The system gets more useful because reviewed human decisions become structured
assets.

### 8. What is the hardest technical part?

The hardest part is not calling an LLM. It is preserving traceability and review
boundaries:

```text
PRD evidence -> requirement -> coverage -> case -> run -> asset -> diagnosis
```

If that chain is clear, the platform can explain why a test exists, how it was
executed, what evidence it produced, and what should change after a failure.

### 9. What happens when the PRD changes?

Chapter diff marks related requirements, coverage, cases, and assets as stale
when their source changes. Approved human decisions are not silently overwritten.

The user should see:

- which coverage is still valid;
- which cases need review;
- which assets should be deprecated or repaired;
- which new risks have no coverage.

### 10. How do you measure success?

The most important metrics are:

- coverage acceptance rate;
- case approval rate;
- first agentic pass rate;
- asset approval rate;
- replay speedup over agentic execution;
- replay pass rate;
- diagnosis confirmation rate;
- number of failures routed back to assets, cases, coverage, or patterns.

Counting generated cases is not the key metric.

## Architecture Talking Points

- REST remains the canonical API.
- MCP and agent surfaces should call the same service functions or REST handlers.
- `test_design_planner` owns PRD -> requirements -> coverage.
- `case_drafter` owns accepted coverage -> cases.
- `run_orchestrator` keeps agentic execution.
- `regression_asset_builder` extracts assets from passed runs.
- `replay_runner` runs approved assets.
- `diagnoser` expands from pattern feedback to feedback routing.

## One-Line Positioning

> Michelle turns PRDs into reviewed test coverage, verified execution paths, and
> compounding regression intelligence.

## What To Avoid Saying

Avoid:

- "It automatically generates all tests."
- "It replaces QA review."
- "Every run is agentic."
- "The LLM knows whether the product is correct."

Say instead:

- "AI proposes; humans approve durable assets."
- "Agentic execution discovers paths; replay makes regression fast."
- "Diagnosis is trace-backed and human-confirmed."
