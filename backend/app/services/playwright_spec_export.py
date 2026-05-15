"""Export reviewed regression assets as Playwright TypeScript specs."""

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


def _action_to_statement(action: dict[str, Any]) -> str | None:
    tool_name = str(action.get("tool_name") or "")
    args = action.get("tool_args") if isinstance(action.get("tool_args"), dict) else {}
    if tool_name == "browser_navigate":
        url = str(args.get("url") or "")
        return f"await page.goto('{_ts_string(url)}');" if url else None
    if tool_name == "browser_click":
        locator = _playwright_locator(action)
        return f"await {locator}.click();" if locator else None
    if tool_name in {"browser_type", "browser_fill"}:
        locator = _playwright_locator(action)
        text = str(args.get("text") or args.get("value") or "")
        return f"await {locator}.fill('{_ts_string(text)}');" if locator else None
    if tool_name == "browser_fill_form":
        return _fill_form_statement(args)
    return None


def _playwright_locator(action: dict[str, Any]) -> str | None:
    locator = action.get("locator") if isinstance(action.get("locator"), dict) else None
    if locator:
        semantic = _semantic_locator_expr(locator)
        if semantic:
            return semantic
    args = action.get("tool_args") if isinstance(action.get("tool_args"), dict) else {}
    if selector := args.get("selector"):
        return f"page.locator('{_ts_string(str(selector))}')"
    if text := args.get("text"):
        return f"page.getByText('{_ts_string(str(text))}')"
    if element := args.get("element"):
        return f"page.getByText('{_ts_string(str(element))}')"
    return None


def _semantic_locator_expr(locator: dict[str, Any]) -> str | None:
    strategy = str(locator.get("strategy") or "")
    value = str(locator.get("value") or locator.get("name") or "")
    if strategy == "role":
        role = str(locator.get("role") or "").strip()
        name = str(locator.get("name") or locator.get("value") or "").strip()
        if role and name:
            return f"page.getByRole('{_ts_string(role)}', {{ name: '{_ts_string(name)}' }})"
    if strategy == "label" and value:
        return f"page.getByLabel('{_ts_string(value)}')"
    if strategy == "test_id" and value:
        return f"page.getByTestId('{_ts_string(value)}')"
    if strategy == "text" and value:
        return f"page.getByText('{_ts_string(value)}')"
    if strategy == "css" and value:
        return f"page.locator('{_ts_string(value)}')"
    return None


def _fill_form_statement(args: dict[str, Any]) -> str | None:
    fields = args.get("fields")
    if not isinstance(fields, list) or not fields:
        return None
    statements: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        value = str(field.get("value") or "")
        if name:
            statements.append(f"await page.getByLabel('{_ts_string(name)}').fill('{_ts_string(value)}');")
    return "\n  ".join(statements) if statements else None


def _safe_title(raw: str | None) -> str:
    return (raw or "").strip() or "Regression asset"


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "regression-asset"


def _ts_string(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("'", "\\'")
