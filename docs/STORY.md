# Michelle Target Walkthrough

This walkthrough describes the new product spine: PRD to reviewed coverage,
reviewed coverage to case drafts, first execution to regression assets, and
failures back into system memory.

## 1. Dashboard

The dashboard answers operational questions:

- Is the backend healthy?
- Which LLM provider is selected for design, execution, and diagnosis?
- Are Playwright MCP and the runner dependencies ready?
- How many coverage items, cases, runs, and assets need review?
- Are recent replay runs failing because the product changed or because an
  asset drifted?

The dashboard is not the core workflow. It is the control room.

## 2. PRD Upload

The user uploads or pastes a PRD. Michelle still splits it into chapters and
tracks versions, hashes, and chapter diffs. The difference is what happens next:

```text
old: PRD chapter -> generated cases
new: PRD chapter -> requirements + risks + coverage items
```

The PRD page should make the version boundary visible:

- added, modified, moved, unchanged, and removed chapters;
- which chapters have accepted coverage;
- which accepted coverage is stale after a PRD change;
- which chapters still need analysis.

## 3. Test Design

The Test Design page is the new center of gravity.

Michelle reads selected PRD chapters and proposes:

- requirement items: explicit product behaviors, rules, constraints, data
  expectations, and permission rules;
- risk types: business risk, data risk, permission risk, validation risk,
  integration risk, and regression risk;
- coverage items: concrete test obligations such as happy path, edge case,
  negative path, permission check, data condition, or regression guard.

The reviewer accepts, rejects, edits, or adds coverage items before any case is
created. This is how Michelle improves case quality: it reviews the test design
before drafting executable steps.

## 4. Case Drafts

Accepted coverage items can generate case drafts.

Each case keeps traceability:

- source PRD and chapter;
- linked requirement item;
- linked coverage item;
- risk type and coverage type;
- PRD evidence;
- assumptions that require human review.

The case review workflow remains strict. A case must be reviewed before it can
be used for execution. If a reviewer edits a case, those fields remain protected
from automated regeneration.

## 5. First Agentic Run

New or changed cases use agentic execution first.

The goal of this run is not just pass/fail. It discovers and records the path:

- browser actions;
- locator candidates;
- URL and title after each step;
- screenshots and trace evidence;
- assertion results;
- errors and console signals.

The run timeline remains the forensic record. A successful first run is the raw
material for a stable regression asset.

## 6. Regression Asset Review

After a case passes, Michelle can extract a draft regression asset from the run:

- action plan;
- locator candidates per action;
- assertions;
- source run evidence;
- case version;
- target project and environment.

A human approves the asset before it becomes the default replay path. This gate
matters because approved assets affect future regression speed and signal.

## 7. Fast Replay

Once an approved asset exists, Michelle should not ask an LLM to drive the same
path step by step on every regression run.

Replay mode runs the stored action plan directly. It is faster, cheaper, and
more predictable than agentic execution.

`auto` execution mode chooses:

- approved asset exists -> replay;
- no asset exists -> agentic first run;
- replay fails -> diagnosis, then optional agentic fallback for repair.

## 8. Diagnosis And Feedback

When a run fails, Michelle diagnoses the failure from:

- case and coverage context;
- failed step;
- trace tail;
- screenshots when available;
- historical patterns.

Human feedback no longer means only "confirm pattern." It routes the learning:

- **Pattern**: this is a recurring failure signature.
- **Asset**: locator or action plan needs repair.
- **Case**: steps, preconditions, or assertions are wrong.
- **Coverage**: the PRD has an uncovered risk or missing scenario.
- **Wrong**: the diagnosis should not be used.

## 9. The Loop

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
Passed -> regression asset review -> fast replay
Failed -> diagnosis -> feedback routing
  ↓
Feedback improves patterns, assets, cases, and coverage
```

That is Michelle's new product story: not AI-generated tests, but compounding
regression intelligence.
