"""Tiny stdio JSON-RPC client for MCP servers.

Only the subset Michelle needs is implemented: initialize, tools/list and
tools/call. This lets the generic executor own the agent loop instead of
delegating that loop to Claude CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.mcp_config import build_playwright_mcp_config


class MCPClientError(RuntimeError):
    pass


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]


class StdioMCPClient:
    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        cwd: Path,
        timeout_seconds: int = 30,
    ):
        self.command = command
        self.args = args
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self) -> StdioMCPClient:
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_mcp_subprocess_env(self.cwd),
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "michelle", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._proc is None:
            return
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()

    async def list_tools(self) -> list[MCPTool]:
        data = await self._request("tools/list", {})
        tools = data.get("tools") or []
        return [
            MCPTool(
                name=str(t.get("name", "")),
                description=str(t.get("description", "")),
                input_schema=t.get("inputSchema") or {},
            )
            for t in tools
            if t.get("name")
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        await self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            msg = await self._read_message()
            if msg.get("id") != req_id:
                # Notifications/log messages are ignored by this minimal client.
                continue
            if "error" in msg:
                err = msg["error"]
                raise MCPClientError(f"{method} failed: {err}")
            result = msg.get("result")
            return result if isinstance(result, dict) else {}

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("MCP process not started")
        self._proc.stdin.write(_encode_message(payload))
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise MCPClientError("MCP process not started")
        try:
            return await _read_framed_json(self._proc.stdout, self.timeout_seconds)
        except TimeoutError as exc:
            raise MCPClientError("timed out waiting for MCP response") from exc
        except EOFError as exc:
            stderr = ""
            if self._proc.stderr is not None:
                try:
                    chunk = await asyncio.wait_for(self._proc.stderr.read(4000), timeout=0.2)
                    stderr = chunk.decode("utf-8", errors="replace")
                except TimeoutError:
                    stderr = ""
            raise MCPClientError(f"MCP process exited unexpectedly: {stderr[:500]}") from exc
        except json.JSONDecodeError as exc:
            raise MCPClientError("MCP returned malformed JSON frame") from exc


def build_playwright_stdio_client(
    *,
    cwd: Path,
    headless: bool,
    isolated: bool = True,
    extra_args: list[str] | None = None,
) -> StdioMCPClient:
    cfg = build_playwright_mcp_config(
        headless=headless,
        isolated=isolated,
        extra_args=extra_args,
    )
    server = cfg["mcpServers"]["playwright"]
    return StdioMCPClient(
        command=server["command"],
        args=list(server.get("args") or []),
        cwd=cwd,
        timeout_seconds=90,
    )


def _mcp_subprocess_env(cwd: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "SOCKS_PROXY",
        "socks_proxy",
    ):
        env.pop(key, None)

    cache_dir = cwd / ".npm-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env.setdefault("NPM_CONFIG_CACHE", str(cache_dir))
    env.setdefault("NO_PROXY", "*")
    return env


def _encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def _read_framed_json(reader: asyncio.StreamReader, timeout_seconds: int) -> dict[str, Any]:
    header = await asyncio.wait_for(_read_header(reader), timeout_seconds)
    content_length = _content_length(header)
    if content_length <= 0:
        raise MCPClientError("MCP frame missing Content-Length")
    body = await asyncio.wait_for(reader.readexactly(content_length), timeout_seconds)
    data = json.loads(body.decode("utf-8"))
    return data if isinstance(data, dict) else {}


async def _read_header(reader: asyncio.StreamReader) -> bytes:
    buf = bytearray()
    while True:
        chunk = await reader.read(1)
        if not chunk:
            raise EOFError
        buf.extend(chunk)
        if buf.endswith(b"\r\n\r\n") or buf.endswith(b"\n\n"):
            return bytes(buf)
        if len(buf) > 8192:
            raise MCPClientError("MCP response header too large")


def _content_length(header: bytes) -> int:
    text = header.decode("ascii", errors="replace")
    for line in text.replace("\r\n", "\n").split("\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip().lower() == "content-length":
            try:
                return int(v.strip())
            except ValueError as exc:
                raise MCPClientError(f"invalid Content-Length: {v.strip()}") from exc
    return 0
