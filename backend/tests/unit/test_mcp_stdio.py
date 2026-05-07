"""Unit tests for the minimal MCP stdio transport."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.mcp_stdio import (
    _content_length,
    _encode_message,
    _mcp_subprocess_env,
    _read_framed_json,
)


def test_encode_message_uses_mcp_content_length_frame():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    raw = _encode_message(payload)
    header, body = raw.split(b"\r\n\r\n", 1)

    assert header.startswith(b"Content-Length: ")
    assert _content_length(header + b"\r\n\r\n") == len(body)
    assert json.loads(body) == payload


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


def test_mcp_subprocess_env_strips_proxy_and_uses_workspace_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7897")

    env = _mcp_subprocess_env(tmp_path)

    assert "HTTP_PROXY" not in env
    assert "all_proxy" not in env
    assert env["NO_PROXY"] == "*"
    assert env["NPM_CONFIG_CACHE"] == str(tmp_path / ".npm-cache")
