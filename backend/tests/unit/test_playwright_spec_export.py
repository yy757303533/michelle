from __future__ import annotations

from app.models import RegressionAsset, TestCase
from app.services.playwright_spec_export import export_playwright_spec


def test_exports_navigation_and_raw_css_click() -> None:
    asset = RegressionAsset(
        asset_id="asset_test",
        project_id="demo",
        case_id="TC-20260512-001",
        source_run_id="run_passed",
        status="approved",
        action_plan=[
            {
                "intent": "open login",
                "tool_name": "browser_navigate",
                "tool_args": {"url": "https://example.test/login"},
            },
            {
                "intent": "submit",
                "tool_name": "browser_click",
                "tool_args": {"selector": "button[type=submit]"},
            },
        ],
    )
    case = TestCase(
        case_id="TC-20260512-001",
        project_id="demo",
        name="Login works",
        intent="User logs in",
    )

    spec = export_playwright_spec(asset, case)

    assert spec.filename == "login-works.spec.ts"
    assert "import { test, expect } from '@playwright/test';" in spec.content
    assert "test('Login works', async ({ page }) => {" in spec.content
    assert "await page.goto('https://example.test/login');" in spec.content
    assert "await page.locator('button[type=submit]').click();" in spec.content


def test_exports_semantic_role_and_text_locators() -> None:
    asset = RegressionAsset(
        asset_id="asset_test",
        project_id="demo",
        case_id="TC-20260512-001",
        source_run_id="run_passed",
        status="approved",
        action_plan=[
            {
                "intent": "submit",
                "tool_name": "browser_click",
                "tool_args": {"selector": "button[type=submit]"},
                "locator": {
                    "strategy": "role",
                    "role": "button",
                    "name": "Login",
                    "fallbacks": [],
                },
            },
            {
                "intent": "open forgot password",
                "tool_name": "browser_click",
                "tool_args": {"selector": "a.forgot"},
                "locator": {"strategy": "text", "value": "Forgot password"},
            },
        ],
    )

    spec = export_playwright_spec(asset)

    assert "await page.getByRole('button', { name: 'Login' }).click();" in spec.content
    assert "await page.getByText('Forgot password').click();" in spec.content
