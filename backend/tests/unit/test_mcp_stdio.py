"""Unit tests for the minimal MCP stdio transport."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.mcp_stdio import (
    _encode_message,
    _mcp_subprocess_env,
    _read_framed_json,
    _read_stdio_json,
)


def test_encode_message_uses_ndjson_frame():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    raw = _encode_message(payload)

    assert raw.endswith(b"\n")
    assert json.loads(raw) == payload


@pytest.mark.asyncio
async def test_read_stdio_json_accepts_ndjson():
    reader = asyncio.StreamReader()
    body = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n'
    reader.feed_data(body)

    data = await _read_stdio_json(reader, timeout_seconds=1)

    assert data["result"] == {"tools": []}


@pytest.mark.asyncio
async def test_read_framed_json_accepts_crlf_header():
    reader = asyncio.StreamReader()
    body = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'
    reader.feed_data(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)

    data = await _read_framed_json(reader, timeout_seconds=1)

    assert data["result"] == {"tools": []}


@pytest.mark.asyncio
async def test_read_framed_json_accepts_lf_header():
    reader = asyncio.StreamReader()
    body = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
    reader.feed_data(b"Content-Length: " + str(len(body)).encode("ascii") + b"\n\n" + body)

    data = await _read_framed_json(reader, timeout_seconds=1)

    assert data["method"] == "notifications/initialized"


def test_mcp_subprocess_env_strips_proxy_and_uses_shared_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7897")
    monkeypatch.setattr("app.config.settings.artifacts_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr("app.config.settings.playwright_mcp_cache_dir", "")

    env = _mcp_subprocess_env(tmp_path)

    assert "HTTP_PROXY" not in env
    assert "all_proxy" not in env
    assert env["NO_PROXY"] == "*"
    assert env["NPM_CONFIG_CACHE"] == str(tmp_path / "artifacts" / ".npm-cache")


def test_mcp_subprocess_env_resolves_relative_cache_from_app_cwd(tmp_path, monkeypatch):
    app_cwd = tmp_path / "backend"
    run_cwd = app_cwd / "artifacts" / "project" / "run"
    run_cwd.mkdir(parents=True)
    monkeypatch.chdir(app_cwd)
    monkeypatch.setattr("app.config.settings.artifacts_dir", "./artifacts")
    monkeypatch.setattr("app.config.settings.playwright_mcp_cache_dir", "")

    env = _mcp_subprocess_env(run_cwd)

    assert env["NPM_CONFIG_CACHE"] == str(app_cwd / "artifacts" / ".npm-cache")
