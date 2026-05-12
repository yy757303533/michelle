"""Prompt registry tests."""

from __future__ import annotations

import pytest

from app.llm.prompts.registry import PromptNotFoundError, load_prompt, prompt_id, render


def test_load_prompt_v1_files_exist():
    # All three v1 prompts ship with the project
    assert "PRD chapter" in load_prompt("case_gen", "v1")
    assert "diagnosis" in load_prompt("diagnose", "v1").lower()
    assert "browser test agent" in load_prompt("execute", "v1").lower()


def test_case_gen_prompt_requires_explicit_verification_milestones():
    prompt = load_prompt("case_gen", "v1").lower()

    assert "email verification" in prompt
    assert "steps" in prompt
    assert "assertions" in prompt
    assert "evidence" in prompt
    assert "chapter title alone" in prompt
    assert "runtime cost discipline" in prompt
    assert "only the primary happy-path case" in prompt
    assert "stop at the earliest observable outcome" in prompt
    assert "verified/next registration state" in prompt
    assert "do not assert that a backend registration api request must happen" in prompt
    assert "case_type" in prompt
    assert "execution_scope" in prompt
    assert "requires_email_verification" in prompt
    assert "requires_real_login" in prompt
    assert "receivable temporary email" in prompt
    assert "random\n  unique email addresses are enough" in prompt
    assert "do not prepend login steps" in prompt
    assert "deterministic login bootstrap" in prompt


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
        "case_gen",
        "v1",
        project_name="Demo",
        max_cases=5,
        target_cases=3,
        base_url="http://example.com",
        login_context="(no creds)",
        chapter_id="ch1",
        chapter_text="some PRD content",
        module_hint="login",
    )
    assert "Demo" in rendered
    assert "up to 3 concrete UI test cases" in rendered
    assert "not a quota" in rendered
    assert "ch1" in rendered
    assert "some PRD content" in rendered


def test_load_prompt_missing_raises():
    with pytest.raises(PromptNotFoundError):
        load_prompt("does_not_exist", "v1")


def test_prompt_id_format():
    assert prompt_id("case_gen", "v1") == "case_gen_v1"
