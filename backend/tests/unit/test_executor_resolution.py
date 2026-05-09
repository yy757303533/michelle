from __future__ import annotations

import pytest

from app.agent import executor


@pytest.mark.asyncio
async def test_execute_provider_claude_cli_selects_claude_loop(monkeypatch):
    async def fake_execution_provider(_session=None):
        return "claude-cli"

    monkeypatch.setattr(executor, "get_case_execution_provider", fake_execution_provider)
    monkeypatch.setattr(executor, "get_executor_loop", _auto_loop)
    monkeypatch.setattr(executor, "generic_openai_providers", lambda: ["codex-cli"])
    monkeypatch.setattr(executor, "claude_cli_available", lambda: True)
    monkeypatch.setattr(executor, "npx_available", lambda: True)

    status = await executor.resolve_executor_status()

    assert status.status == "ready"
    assert status.configured_loop == "provider:claude-cli"
    assert status.resolved_loop == "claude_cli"


@pytest.mark.asyncio
async def test_execute_provider_codex_selects_michelle_loop(monkeypatch):
    async def fake_execution_provider(_session=None):
        return "codex-cli"

    monkeypatch.setattr(executor, "get_case_execution_provider", fake_execution_provider)
    monkeypatch.setattr(executor, "get_executor_loop", _auto_loop)
    monkeypatch.setattr(executor, "generic_openai_providers", lambda: ["codex-cli"])
    monkeypatch.setattr(executor, "claude_cli_available", lambda: True)
    monkeypatch.setattr(executor, "npx_available", lambda: True)

    status = await executor.resolve_executor_status()

    assert status.status == "ready"
    assert status.configured_loop == "provider:codex-cli"
    assert status.resolved_loop == "generic_openai"
    assert "codex-cli" in status.detail


@pytest.mark.asyncio
async def test_execute_provider_unavailable_reports_down(monkeypatch):
    async def fake_execution_provider(_session=None):
        return "codex-cli"

    monkeypatch.setattr(executor, "get_case_execution_provider", fake_execution_provider)
    monkeypatch.setattr(executor, "get_executor_loop", _auto_loop)
    monkeypatch.setattr(executor, "generic_openai_providers", lambda: ["claude-cli"])
    monkeypatch.setattr(executor, "claude_cli_available", lambda: True)
    monkeypatch.setattr(executor, "npx_available", lambda: True)

    status = await executor.resolve_executor_status()

    assert status.status == "down"
    assert status.resolved_loop == "generic_openai"
    assert "codex-cli" in status.detail


async def _auto_loop(_session=None):
    return "auto"
