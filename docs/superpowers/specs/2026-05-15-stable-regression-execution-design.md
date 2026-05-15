# Stable Regression Execution Design

## Goal

Make Michelle's execution model explicit and more stable: keep agentic Playwright MCP execution for first runs, repair, and diagnosis; turn passed runs into reviewed regression assets; replay approved assets deterministically; and allow critical paths to be exported as Playwright specs for CI.

## Current State

普通用例执行已经是 agentic Playwright MCP，而不是纯视觉执行。`POST /api/runs/` 创建 `Run(status=pending)`，`run_orchestrator` 渲染 execute prompt，然后选择 `generic_openai` 或 `claude_cli` runner。两条 runner 都通过 `@playwright/mcp` 操作浏览器，并把 browser/model/assertion 事件持久化为 `StepEvent`。

回放路径已经存在。passed run 可以提取 `RegressionAsset.action_plan`，approved asset 通过 `/api/regression-assets/{asset_id}/replay` 直接调用 Playwright MCP 工具，不再让 LLM 每次重新规划。当前不足是 action plan 主要保存原始 MCP `tool_args`，对 selector/ref 漂移的容错和 CI 级导出能力还不够。

## Design

### 1. Execution Policy

Michelle 保留两层执行：

- **Agentic first run**: 用于第一次跑通、探索页面、失败修复和诊断。LLM 决策，Playwright MCP 执行。
- **Reviewed replay**: passed run 提取为 draft asset，人工 approve 后进入稳定回归。replay 只执行已审阅动作，不重新规划。
- **Playwright spec export**: 对关键 approved asset 生成可读、可提交到业务仓库 CI 的 `.spec.ts`，作为最高稳定级别。

视觉能力只作为补充证据或 fallback，不作为默认执行方式。默认策略仍是 ARIA/role/text/test id/selector 优先。

### 2. Semantic Locator Metadata

新增一个轻量的 locator 规范，仍保存在 `RegressionAsset.action_plan` 的每个 action 内，避免新增表：

```json
{
  "intent": "submit credentials",
  "tool_name": "browser_click",
  "tool_args": {"selector": "button[type=submit]"},
  "locator": {
    "strategy": "css",
    "value": "button[type=submit]",
    "fallbacks": [
      {"strategy": "role", "role": "button", "name": "Login"},
      {"strategy": "text", "value": "Login"},
      {"strategy": "test_id", "value": "login-submit"}
    ]
  }
}
```

Supported strategies:

- `role`: role plus accessible name. Preferred when present.
- `label`: form label text. Preferred for inputs.
- `test_id`: stable application-provided test id.
- `text`: visible text fallback.
- `css`: current selector fallback.
- `raw_mcp`: existing `tool_name/tool_args` for compatibility.

Extraction is best-effort. Existing assets without `locator` continue to replay with raw `tool_args`.

### 3. Replay Resolution

Replay should resolve action arguments before calling MCP:

1. If action has a semantic locator, try strategies in this order: `role`, `label`, `test_id`, `text`, `css`.
2. If a strategy cannot be expressed with available MCP tools, skip it and record that in the step result.
3. If all semantic strategies fail, fall back to original `tool_args`.
4. Persist the chosen strategy and attempted fallbacks in `StepEvent.tool_result`.

This keeps replay deterministic while making drift easier to diagnose.

### 4. Playwright Spec Export

Add a backend service that converts an approved asset into a Playwright TypeScript spec. The first version should support:

- `browser_navigate` -> `await page.goto(...)`
- `browser_click` -> semantic locator click when locator metadata exists, otherwise `page.locator(selector).click()`
- `browser_fill_form` / text entry actions -> `getByLabel`, `getByTestId`, or `locator(...).fill(...)`
- asset assertions -> comments or basic `expect(...)` statements when safely mappable

The export is read-only. It does not write into the user's target repo. API returns text plus a suggested filename.

### 5. UI and API

Minimal API additions:

- `GET /api/regression-assets/{asset_id}/playwright-spec`
  - Requires viewer access.
  - Requires asset to exist.
  - Returns `{filename, content}`.

Minimal UI addition:

- On asset cards, show `Export spec` for approved assets.
- Use a modal or drawer with read-only code block and filename.

### 6. Testing Strategy

Backend tests cover:

- Asset extraction enriches action plans without breaking existing fields.
- Replay uses semantic locator when present and falls back to raw tool args.
- Spec export produces stable TypeScript for a representative action plan.
- API rejects missing assets and serves approved asset spec content.

Frontend tests are optional for the first backend slice. If UI is touched, run `pnpm lint`.

## Non-Goals

- No pure-vision default execution.
- No automatic commits into external repositories.
- No database migration for locator metadata in the first version.
- No full Playwright codegen parity with every MCP tool in the first version.

## Success Criteria

- Existing asset replay tests still pass.
- New semantic replay tests pass.
- Approved asset can be exported to a readable Playwright `.spec.ts`.
- Legacy assets without locator metadata replay exactly as before.
