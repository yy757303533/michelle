# Stable Regression Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Michelle's stable regression path more deterministic by adding semantic locator metadata to regression assets, replaying with semantic fallback, and exporting approved assets as Playwright specs.

**Architecture:** Keep agentic execution unchanged. Add focused helper modules under `backend/app/services/regression_assets/` or, if avoiding package reshaping in the first pass, focused functions in `backend/app/services/regression_assets.py` with tests first. Store locator metadata inside existing JSON action plans to avoid a migration. Add a read-only API endpoint for Playwright spec export.

**Tech Stack:** FastAPI, SQLModel, pytest, Playwright TypeScript output, existing Playwright MCP replay service.

---

## File Map

- Modify: `backend/app/services/regression_assets.py`
  - Extract semantic locator metadata from successful `StepEvent` rows.
  - Resolve replay action arguments using locator metadata with raw fallback.
  - Call Playwright MCP through the existing `call_tool` interface.
- Create: `backend/app/services/playwright_spec_export.py`
  - Convert a `RegressionAsset` and optional `TestCase` into TypeScript Playwright spec text.
- Modify: `backend/app/api/regression_assets.py`
  - Add `GET /api/regression-assets/{asset_id}/playwright-spec`.
- Modify: `backend/tests/unit/test_regression_assets.py`
  - Add tests for extraction and replay fallback.
- Create: `backend/tests/unit/test_playwright_spec_export.py`
  - Add tests for spec generation.
- Optional modify: `frontend/src/routes/index.tsx`
  - Add a small `Export spec` action for approved assets.

## Task 1: Semantic Locator Extraction

**Files:**
- Modify: `backend/tests/unit/test_regression_assets.py`
- Modify: `backend/app/services/regression_assets.py`

- [ ] **Step 1: Write failing extraction test**

Add a test that seeds a passed run with browser actions containing `selector`, `element`, `text`, or `ref`, then calls `/api/regression-assets/from-run/{run_id}` and asserts each extracted action keeps `tool_name/tool_args` and includes a `locator` object.

Expected key assertion shape:

```python
assert asset["action_plan"][1]["locator"] == {
    "strategy": "css",
    "value": "button[type=submit]",
    "fallbacks": [
        {"strategy": "text", "value": "Login"},
    ],
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py::test_extracts_semantic_locator_metadata -q
```

Expected: FAIL because extracted actions do not include `locator`.

- [ ] **Step 3: Implement minimal extraction helper**

Add helper functions in `backend/app/services/regression_assets.py`:

```python
def _semantic_locator_from_step(step: StepEvent) -> dict[str, Any] | None:
    args = step.tool_args if isinstance(step.tool_args, dict) else {}
    result = step.tool_result if isinstance(step.tool_result, dict) else {}
    fallbacks: list[dict[str, Any]] = []
    if text := args.get("text"):
        fallbacks.append({"strategy": "text", "value": str(text)})
    if element := args.get("element"):
        fallbacks.append({"strategy": "text", "value": str(element)})
    if selector := args.get("selector"):
        return {"strategy": "css", "value": str(selector), "fallbacks": fallbacks}
    if ref := args.get("ref"):
        return {"strategy": "raw_mcp", "value": str(ref), "fallbacks": fallbacks}
    if label := result.get("label"):
        return {"strategy": "label", "value": str(label), "fallbacks": fallbacks}
    return fallbacks[0] if fallbacks else None
```

When building `action_plan`, include `"locator": locator` only when the helper returns a value.

- [ ] **Step 4: Run the focused test and existing asset tests**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py -q
```

Expected: PASS.

## Task 2: Semantic Replay With Raw Fallback

**Files:**
- Modify: `backend/tests/unit/test_regression_assets.py`
- Modify: `backend/app/services/regression_assets.py`

- [ ] **Step 1: Write failing replay test**

Add a test where an asset action has a semantic locator with fallbacks and a raw selector. Use a fake `call_tool` that fails the first semantic attempt and succeeds on raw fallback. Assert calls are attempted in order and the persisted `StepEvent.tool_result` includes the chosen fallback.

Expected call order:

```python
assert calls == [
    ("browser_click", {"element": "Login", "ref": "Login"}),
    ("browser_click", {"selector": "button[type=submit]"}),
]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py::test_replay_falls_back_from_semantic_locator_to_raw_args -q
```

Expected: FAIL because replay currently calls only original `tool_args`.

- [ ] **Step 3: Implement replay argument candidates**

Add helpers:

```python
def _replay_argument_candidates(action: dict[str, Any]) -> list[dict[str, Any]]:
    raw_args = action.get("tool_args") if isinstance(action.get("tool_args"), dict) else {}
    locator = action.get("locator") if isinstance(action.get("locator"), dict) else None
    candidates: list[dict[str, Any]] = []
    for loc in _locator_sequence(locator):
        candidate = _locator_to_mcp_args(action, loc)
        if candidate:
            candidates.append(candidate)
    if raw_args not in candidates:
        candidates.append(raw_args)
    return candidates
```

Keep the first version conservative:

```python
def _locator_to_mcp_args(action: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any] | None:
    strategy = locator.get("strategy")
    value = str(locator.get("value") or "")
    if not value:
        return None
    if strategy in {"text", "role", "label", "test_id"}:
        return {"element": value, "ref": value}
    if strategy == "css":
        return {"selector": value}
    return None
```

In `_execute_replay_plan`, try candidates for the action until one succeeds or all fail. Persist attempted candidates and chosen candidate in `tool_result`.

- [ ] **Step 4: Run replay tests**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py -q
```

Expected: PASS.

## Task 3: Playwright Spec Export Service

**Files:**
- Create: `backend/app/services/playwright_spec_export.py`
- Create: `backend/tests/unit/test_playwright_spec_export.py`

- [ ] **Step 1: Write failing spec export tests**

Create tests for:

- `browser_navigate` becomes `await page.goto("...")`
- semantic click becomes `await page.getByText("Login").click()` or `await page.getByRole("button", { name: "Login" }).click()`
- raw css click becomes `await page.locator("button[type=submit]").click()`

Expected import/header:

```typescript
import { test, expect } from '@playwright/test';

test('Login works', async ({ page }) => {
  await page.goto('https://example.test/login');
  await page.locator('button[type=submit]').click();
});
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd backend && uv run pytest tests/unit/test_playwright_spec_export.py -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement exporter**

Create:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models import RegressionAsset, TestCase


@dataclass(frozen=True)
class PlaywrightSpec:
    filename: str
    content: str


def export_playwright_spec(asset: RegressionAsset, case: TestCase | None = None) -> PlaywrightSpec:
    title = _safe_title(case.name if case else asset.case_id)
    filename = f"{_slug(title or asset.case_id)}.spec.ts"
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        f"test('{_ts_string(title or asset.case_id)}', async ({{ page }}) => {{",
    ]
    for action in asset.action_plan or []:
        statement = _action_to_statement(action)
        if statement:
            lines.append(f"  {statement}")
    lines.append("});")
    lines.append("")
    return PlaywrightSpec(filename=filename, content="\n".join(lines))
```

Implement `_action_to_statement` for `browser_navigate`, `browser_click`, and simple fill actions. Escape strings with a small helper that replaces backslash and single quote.

- [ ] **Step 4: Run export tests**

Run:

```bash
cd backend && uv run pytest tests/unit/test_playwright_spec_export.py -q
```

Expected: PASS.

## Task 4: API Endpoint For Spec Export

**Files:**
- Modify: `backend/app/api/regression_assets.py`
- Modify: `backend/tests/unit/test_regression_assets.py`

- [ ] **Step 1: Write failing API test**

Add a test that seeds an approved asset and calls:

```python
response = await app_client.get("/api/regression-assets/asset_test/playwright-spec")
```

Assert:

```python
assert response.status_code == 200
assert response.json()["data"]["filename"].endswith(".spec.ts")
assert "import { test, expect } from '@playwright/test';" in response.json()["data"]["content"]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py::test_export_playwright_spec_api -q
```

Expected: FAIL with 404.

- [ ] **Step 3: Add endpoint**

In `backend/app/api/regression_assets.py`, import `TestCase` and `export_playwright_spec`, then add:

```python
@router.get("/{asset_id}/playwright-spec")
async def export_asset_playwright_spec(
    asset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    asset = await session.get(RegressionAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    await require_project_role(
        getattr(request.state, "user", None), asset.project_id, "viewer", session
    )
    case = await session.get(TestCase, asset.case_id)
    spec = export_playwright_spec(asset, case)
    return {"data": {"filename": spec.filename, "content": spec.content}}
```

- [ ] **Step 4: Run endpoint and exporter tests**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py tests/unit/test_playwright_spec_export.py -q
```

Expected: PASS.

## Task 5: Optional UI Export Action

**Files:**
- Modify: `frontend/src/routes/index.tsx`

- [ ] **Step 1: Add minimal UI state**

Add state to hold exported spec:

```tsx
const [exportedSpec, setExportedSpec] = useState<{ filename: string; content: string } | null>(null);
```

- [ ] **Step 2: Add mutation/query helper**

Fetch:

```tsx
const r = await apiFetch(`/api/regression-assets/${assetId}/playwright-spec`);
```

Set `exportedSpec` from `json.data`.

- [ ] **Step 3: Add approved-asset button**

For approved assets, add an `Export spec` button near `replay`. The button opens a modal or inline panel with filename and read-only code.

- [ ] **Step 4: Run frontend lint**

Run:

```bash
cd frontend && pnpm lint
```

Expected: PASS.

## Task 6: Final Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend && uv run pytest tests/unit/test_regression_assets.py tests/unit/test_playwright_spec_export.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ruff**

Run:

```bash
cd backend && uv run ruff check .
```

Expected: PASS.

- [ ] **Step 3: Review changed docs and code**

Run:

```bash
git diff -- backend/app/services/regression_assets.py backend/app/services/playwright_spec_export.py backend/app/api/regression_assets.py backend/tests/unit/test_regression_assets.py backend/tests/unit/test_playwright_spec_export.py docs/superpowers/specs/2026-05-15-stable-regression-execution-design.md docs/superpowers/plans/2026-05-15-stable-regression-execution.md
```

Expected: Diff matches this design and does not modify unrelated files.

## Execution Recommendation

Implement Tasks 1 through 4 first. That gives a complete backend value slice without UI churn. Task 5 can follow once the API shape is confirmed. Task 6 is required before claiming completion.
