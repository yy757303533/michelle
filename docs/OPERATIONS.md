# Michelle Operations Guide

This guide describes the target operating model after the product shift to
coverage-first test design and regression assets.

## 1. Start The Platform

```bash
cp .env.example .env
make setup
make postgres
make dev
```

Open `http://localhost:5173`.

Backend and frontend defaults:

- FastAPI: `http://localhost:8000`
- Web UI: `http://localhost:5173`
- OpenAPI: `http://localhost:8000/docs`

Use PostgreSQL for shared or pilot environments. SQLite is only suitable for
local single-user work.

## 2. Configure A Project

Each project needs:

- `project_id`
- display name
- `base_url`
- optional `login_url`
- test credentials or secret references

The execution layer uses these fields for agentic first runs and replay runs.
Do not put production credentials into demo projects.

## 3. Configure Dev Context

Dev Context is optional but recommended for ZStack internal use. It lets
Michelle import PRDs from workspace files or GitLab through `zstack-dev-mcp`
without moving the Michelle repository into `zstack-workspace`.

Recommended local layout:

```text
/Users/yy/code/yal/michelle
/Users/yy/code/zstack-workspace
```

Configure Michelle with external paths:

```bash
MICHELLE_WORKSPACE_ROOT=/Users/yy/code/zstack-workspace
MICHELLE_ZDEV_MCP_COMMAND=node
MICHELLE_ZDEV_MCP_ARGS=/Users/yy/code/zstack-workspace/zstack-dev-mcp/dist/index.js
MICHELLE_ZDEV_MCP_CWD=/Users/yy/code/zstack-workspace/zstack-dev-mcp
MICHELLE_ZDEV_MCP_TIMEOUT_SECONDS=60
MICHELLE_DEV_CONTEXT_REPOS=zstack,zstack-ui-next,premium
```

Admins can also edit these values from Dashboard → Platform settings →
`dev_context`. Runtime values are stored in `runtime_settings` and override the
`.env` defaults without a backend restart.

Deployment requirements:

- `MICHELLE_WORKSPACE_ROOT` must point to a real workspace directory.
- `zstack-dev-mcp` must be installed and built before GitLab URL import is used.
- Credentials for GitLab/Jira/Confluence/Jenkins stay in the MCP/workspace
  environment, not in the browser UI.
- Do not copy or commit `zstack-workspace` or `zstack-dev-mcp` into the Michelle
  repository.

Check the configured integration:

```bash
curl http://localhost:8000/api/dev-context/status
```

This endpoint is protected by the normal Michelle auth middleware outside test
mode.

Optional server log collection is configured as JSON. Michelle only tails paths
listed here; it does not expose an arbitrary SSH shell.

```bash
MICHELLE_SERVER_LOGS_JSON='{"servers":[{"name":"staging-api","host":"10.0.0.1","user":"readonly","env":"staging","roles":["api"],"log_paths":["/var/log/zstack/management-server.log","/var/log/nginx/error.log"]}]}'
```

When a failed run is diagnosed with workspace context, Michelle collects:

- browser/test evidence from the run;
- workspace code candidates using `rg`;
- Jira keys, Confluence pages, GitLab CI job logs, and Jenkins build logs
  through `zstack-dev-mcp` when present in failure text;
- configured SSH log tails.

Workspace-aware diagnosis runs through a background DiagnosisJob. The UI starts
the job and polls `/api/diagnosis/jobs/{job_id}` until it reaches `done` or
`failed`.

Diagnosis job creation reuses an existing diagnosis for the same run when
`overwrite_existing=false`; this avoids duplicate LLM calls when the failed-run
hook already produced a diagnosis. Use the explicit regenerate action in the UI
when a fresh diagnosis is needed.

The Dashboard `dev_context` settings card has targeted probes for
`zstack-dev-mcp` and configured server logs. Use those probes after changing
workspace, MCP, or SSH log settings.

## 4. Main Workflow

### Step 1. Import PRD

Import or paste PRD content. Supported sources:

- Markdown paste or local `.md` file;
- workspace file under `MICHELLE_WORKSPACE_ROOT`;
- GitLab file URL through `zstack-dev-mcp`.

Michelle stores the full PRD, records `source_ref`, and splits the document into
chapters.

Expected result:

- PRD version is created.
- Chapter hashes are stored.
- Diff against previous version is available.

### Step 2. Analyze Requirements And Coverage

Run PRD analysis on selected chapters.

Michelle generates:

- requirement items;
- risk classifications;
- coverage items;
- PRD evidence and rationale.

This replaces the old "generate cases directly from PRD" workflow.

### Step 3. Review Coverage

The reviewer accepts, rejects, edits, or adds coverage items.

Only accepted coverage should be allowed to generate case drafts. This is the
quality gate that keeps low-value or hallucinated cases out of the execution
queue.

### Step 4. Draft And Review Cases

Generate case drafts from accepted coverage items.

Each case should show:

- linked PRD;
- linked requirement;
- linked coverage item;
- risk type;
- coverage type;
- assumptions;
- quality flags.

The reviewer approves or rejects the case. Approved cases can be executed.

### Step 5. First Agentic Execution

Use agentic execution for approved cases without an approved regression asset.

Purpose:

- verify that the case is executable;
- capture browser evidence;
- collect locator candidates;
- generate a timeline that can be turned into an asset.

### Step 6. Extract Regression Asset

For a passed run, extract a draft regression asset.

The asset should include:

- action plan;
- locator candidates;
- assertions;
- source run;
- case version;
- screenshots and trace references.

The asset remains draft until approved.

### Step 7. Replay Approved Asset

Approved assets are the preferred path for regression.

Execution modes:

| Mode | Behavior |
|---|---|
| `agentic` | Use LLM-guided browser execution. |
| `replay` | Use approved regression asset directly. |
| `auto` | Replay if an approved asset exists; otherwise use agentic execution. |

### Step 8. Diagnose Failure

Failed agentic or replay runs can be diagnosed.

The diagnosis page should include:

- failure category;
- confidence;
- trace-backed reasoning;
- fix suggestion;
- historical pattern matches;
- feedback routing options.

### Step 9. Route Feedback

Human feedback decides where the learning lands:

| Feedback target | Use when |
|---|---|
| Pattern | The failure is recurring and should be recognized next time. |
| Asset | The action plan or locator strategy drifted. |
| Case | The case steps, preconditions, or assertions are wrong. |
| Coverage | The failure reveals a missing risk or untested scenario. |
| Wrong | The diagnosis should not be trusted. |

## 5. Recommended REST Flow

```bash
# Import pasted markdown PRD
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","name":"Demo PRD","source":{"type":"markdown","markdown":"# Demo\n\n## Login\nUsers can log in."}}'

# Import PRD from workspace file
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","source":{"type":"workspace","repo":"zstack","file_path":"docs/prd/demo.md","ref":"master"}}'

# Import PRD from GitLab through zstack-dev-mcp
curl -X POST http://localhost:8000/api/prd/import \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"demo","source":{"type":"gitlab_mcp","url":"http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/demo.md"}}'

# Analyze PRD into requirements and coverage
curl -X POST http://localhost:8000/api/prd/<prd_id>/analyze \
  -H 'Content-Type: application/json' \
  -d '{"chapter_indices":[0,1]}'

# Review coverage
curl -X POST http://localhost:8000/api/coverage/<coverage_id>/review \
  -H 'Content-Type: application/json' \
  -d '{"action":"accept"}'

# Draft case from accepted coverage
curl -X POST http://localhost:8000/api/coverage/<coverage_id>/draft-case \
  -H 'Content-Type: application/json' \
  -d '{}'

# Approve case
curl -X POST http://localhost:8000/api/cases/<case_id>/review \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve"}'

# Run in auto mode
curl -X POST http://localhost:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"case_ids":["<case_id>"],"execution_mode":"auto"}'

# Extract asset from a passed run
curl -X POST http://localhost:8000/api/runs/<run_id>/extract-asset \
  -H 'Content-Type: application/json' \
  -d '{}'

# Approve asset
curl -X POST http://localhost:8000/api/regression-assets/<asset_id>/review \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve"}'

# Replay asset
curl -X POST http://localhost:8000/api/regression-assets/<asset_id>/replay \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## 6. Operational Checks

Before a pilot run, verify:

- backend health;
- database connection;
- Dev Context status when workspace/GitLab import is used;
- selected LLM providers;
- Playwright MCP availability;
- runner status;
- artifact directory writability;
- retention policy;
- project base URL and credentials.

## 7. Artifact Policy

Artifacts are evidence. Do not delete them casually.

Expected per-run artifacts:

- redacted prompt;
- step trace JSONL;
- screenshots;
- HTML report;
- final result JSON;
- runner stderr tail when available.

Retention should distinguish:

- passed replay runs: short retention;
- failed runs: longer retention;
- runs used as asset sources: retain while the asset is active.

## 8. Failure Handling

| Symptom | First check |
|---|---|
| GitLab PRD import fails | `MICHELLE_ZDEV_MCP_*` config, zstack-dev-mcp build, GitLab token |
| Workspace PRD import fails | `MICHELLE_WORKSPACE_ROOT`, repo path, file path, branch/ref |
| PRD analysis fails | LLM provider status, prompt output schema, chapter size |
| Coverage is low quality | Prompt version, PRD evidence, requirement extraction |
| Case draft is vague | Coverage item scenario and rationale |
| Agentic run is slow | Max turns, timeout, repeated observations, missing login config |
| Replay fails | Locator drift, page state, stale asset, changed case version |
| Diagnosis is unhelpful | Trace completeness, screenshot availability, prompt version |
| Workspace diagnosis has no code files | `MICHELLE_DEV_CONTEXT_REPOS`, workspace checkout, failure keywords |
| Server logs are missing | `MICHELLE_SERVER_LOGS_JSON`, SSH key access, whitelisted log paths |

## 9. Migration Rule

The old PRD-direct-to-case flow should not remain a first-class product path.
During refactor, legacy endpoints may exist for compatibility, but new UI and
documentation should drive users through:

```text
PRD -> coverage -> case -> first run -> asset -> replay -> diagnosis feedback
```
