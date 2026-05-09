"""Prompt registry tests."""

from __future__ import annotations

import pytest

from app.llm.prompts.registry import PromptNotFoundError, load_prompt, prompt_id, render


def test_load_prompt_v1_files_exist():
    # All three v1 prompts ship with the project
    assert "PRD chapter" in load_prompt("case_gen", "v1")
    assert "diagnosis" in load_prompt("diagnose", "v1").lower()
    assert "browser test agent" in load_prompt("execute", "v1").lower()


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
