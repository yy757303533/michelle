"""Prompt registry tests."""

from __future__ import annotations

import pytest

from app.llm.prompts.registry import PromptNotFoundError, load_prompt, prompt_id, render


def test_load_prompt_v1_files_exist():
    # Runtime LLM prompts still ship with the project. Coverage-based case
    # drafting is deterministic service code, not a PRD-to-case prompt.
    assert "diagnosis" in load_prompt("diagnose", "v1").lower()
    assert "browser test agent" in load_prompt("execute", "v1").lower()


def test_retired_case_gen_prompt_is_not_registered():
    with pytest.raises(PromptNotFoundError):
        load_prompt("case_gen", "v1")


def test_execute_prompt_handles_same_url_spa_state_changes():
    prompt = load_prompt("execute", "v1").lower()

    assert "url changes alone" in prompt
    assert "same url" in prompt
    assert "stepper" in prompt
    assert "visible page state changed" in prompt
    assert "absence of a network request is not" in prompt
    assert "verification-code step" in prompt
    assert "email_create_temp_inbox" in prompt
    assert "email_wait_for_code" in prompt
    assert "use random emails" in prompt
    assert "screenshots are for evidence" in prompt
    assert "web-font loading" in prompt
    assert "prefer snapshots for routine state checks" in prompt
    assert "compact sequence" in prompt
    assert "configured login url" in prompt
    assert "do not open base url only to discover" in prompt


def test_render_substitutes_placeholders():
    rendered = render(
        "diagnose",
        "v1",
        case_name="Login",
        case_summary="Demo login case",
        failed_step_index=2,
        failed_step_summary="button not found",
        trace_tail_lines=1,
        trace_tail="browser_click failed",
    )
    assert "Demo login case" in rendered
    assert "button not found" in rendered
    assert "browser_click failed" in rendered


def test_load_prompt_missing_raises():
    with pytest.raises(PromptNotFoundError):
        load_prompt("does_not_exist", "v1")


def test_prompt_id_format():
    assert prompt_id("test_design", "v1") == "test_design_v1"


def test_test_design_prompt_requires_reviewable_coverage_contract():
    prompt = load_prompt("test_design", "v1").lower()

    assert "strict json" in prompt
    assert "do not draft test cases" in prompt
    assert "deduplicate" in prompt
    assert "quoted prd evidence" in prompt
    assert "executable" in prompt
    assert "one coverage item" in prompt
    assert "output language" in prompt
    assert "requirement" in prompt
    assert "risk_type" in prompt
    assert "coverage_type" in prompt
