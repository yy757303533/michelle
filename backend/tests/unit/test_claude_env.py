"""Claude subprocess environment helpers."""

from __future__ import annotations

import os

from app.agent import claude_env


def test_build_claude_env_dotenv_empty_value_keeps_shell_export(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://shell.example.invalid")
    monkeypatch.setattr(claude_env.settings, "anthropic_base_url", "")
    monkeypatch.setattr(
        claude_env,
        "read_dotenv_anthropic_overrides",
        lambda: {"ANTHROPIC_BASE_URL": ""},
    )

    env = claude_env.build_claude_subprocess_env()

    assert env["ANTHROPIC_BASE_URL"] == "https://shell.example.invalid"


def test_build_claude_env_dotenv_value_wins_over_shell(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "shell-model")
    monkeypatch.setattr(
        claude_env,
        "read_dotenv_anthropic_overrides",
        lambda: {"ANTHROPIC_MODEL": "repo-model"},
    )

    env = claude_env.build_claude_subprocess_env(michelle_run=True)

    assert env["ANTHROPIC_MODEL"] == "repo-model"
    assert env["MICHELLE_RUN"] == "1"


def test_build_claude_env_preserves_unrelated_environment(monkeypatch):
    monkeypatch.setenv("MICHELLE_TEST_SENTINEL", "ok")
    monkeypatch.setattr(claude_env, "read_dotenv_anthropic_overrides", lambda: {})

    env = claude_env.build_claude_subprocess_env()

    assert env["MICHELLE_TEST_SENTINEL"] == "ok"
    assert os.environ["MICHELLE_TEST_SENTINEL"] == "ok"
