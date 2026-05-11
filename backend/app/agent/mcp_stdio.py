"""Tiny stdio JSON-RPC client for MCP servers.

Only the subset Michelle needs is implemented: initialize, tools/list and
tools/call. This lets the generic executor own the agent loop instead of
delegating that loop to Claude CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from asyncio import LimitOverrunError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.mcp_config import build_playwright_mcp_config
from app.config import settings


class MCPClientError(RuntimeError):
    pass


MCP_STDIO_BUFFER_LIMIT = 16 * 1024 * 1024
MCP_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


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
            limit=MCP_STDIO_BUFFER_LIMIT,
        )
        try:
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
        except Exception:
            await self._stop_proc()
            raise

    async def __aexit__(self, *_exc) -> None:
        if self._proc is None:
            return
        await self._stop_proc()

    async def _stop_proc(self) -> None:
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
            return await _read_stdio_json(self._proc.stdout, self.timeout_seconds)
        except TimeoutError as exc:
            stderr = await self._read_stderr_preview()
            detail = _classify_mcp_stderr(stderr) if stderr else ""
            msg = "timed out waiting for MCP response"
            if detail:
                msg += f"; {detail}"
            raise MCPClientError(msg) from exc
        except EOFError as exc:
            stderr = await self._read_stderr_preview()
            detail = _classify_mcp_stderr(stderr)
            if detail:
                raise MCPClientError(f"MCP process exited unexpectedly: {detail}") from exc
            raise MCPClientError(f"MCP process exited unexpectedly: {stderr[:500]}") from exc
        except json.JSONDecodeError as exc:
            raise MCPClientError("MCP returned malformed JSON frame") from exc

    async def _read_stderr_preview(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            chunk = await asyncio.wait_for(self._proc.stderr.read(4000), timeout=0.2)
            return chunk.decode("utf-8", errors="replace")
        except TimeoutError:
            return ""


def build_playwright_stdio_client(
    *,
    cwd: Path,
    headless: bool,
    isolated: bool = True,
    extra_args: list[str] | None = None,
    output_dir: Path | None = None,
    timeout_seconds: int | None = None,
) -> StdioMCPClient:
    cfg = build_playwright_mcp_config(
        headless=headless,
        isolated=isolated,
        extra_args=extra_args,
        output_dir=str(output_dir) if output_dir is not None else None,
    )
    server = cfg["mcpServers"]["playwright"]
    return StdioMCPClient(
        command=server["command"],
        args=list(server.get("args") or []),
        cwd=cwd,
        timeout_seconds=max(
            30,
            timeout_seconds
            if timeout_seconds is not None
            else settings.playwright_mcp_startup_timeout_seconds,
        ),
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

    env.setdefault("NPM_CONFIG_CACHE", str(settings.playwright_mcp_cache_path))
    if settings.playwright_mcp_npm_registry:
        env.setdefault("NPM_CONFIG_REGISTRY", settings.playwright_mcp_npm_registry)
    env.setdefault("NO_PROXY", "*")
    return env


async def probe_playwright_mcp(*, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Start Playwright MCP and verify initialize + tools/list completes."""
    probe_dir = settings.artifacts_path / ".mcp-probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    timeout = timeout_seconds or settings.playwright_mcp_startup_timeout_seconds
    t0 = time.monotonic()
    try:
        async with build_playwright_stdio_client(
            cwd=probe_dir,
            headless=True,
            isolated=True,
            timeout_seconds=timeout,
        ) as client:
            tools = await client.list_tools()
    except MCPClientError as exc:
        detail = str(exc)
        npm_hint = _latest_npm_log_hint(settings.playwright_mcp_cache_path)
        if npm_hint and "npx failed" not in detail:
            detail = f"{detail}; latest npm log: {npm_hint}"
        return {
            "ok": False,
            "detail": detail,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "cache_dir": str(settings.playwright_mcp_cache_path),
        }
    return {
        "ok": True,
        "detail": f"ready; {len(tools)} tools",
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "cache_dir": str(settings.playwright_mcp_cache_path),
        "tools": [t.name for t in tools],
    }


def _classify_mcp_stderr(stderr: str) -> str:
    if not stderr:
        return ""
    compact = " ".join(stderr.strip().split())
    if "npm error" in stderr.lower() or "fetcherror" in stderr.lower():
        if any(s in stderr for s in ("ETIMEDOUT", "ENOTFOUND", "ECONNREFUSED", "EPERM")):
            return f"npx failed to fetch/start @playwright/mcp ({compact[:500]})"
        return f"npx failed to start @playwright/mcp ({compact[:500]})"
    return compact[:500]


def _latest_npm_log_hint(cache_dir: Path) -> str:
    logs_dir = cache_dir / "_logs"
    if not logs_dir.exists():
        return ""
    logs = sorted(logs_dir.glob("*-debug-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return ""
    try:
        text = logs[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [
        " ".join(line.strip().split())
        for line in text.splitlines()
        if any(token in line for token in ("ETIMEDOUT", "ENOTFOUND", "ECONNREFUSED", "EPERM"))
        or "error" in line.lower()
    ]
    return "; ".join(lines[-5:])[:800]


def _encode_message(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


async def _read_stdio_json(reader: asyncio.StreamReader, timeout_seconds: int) -> dict[str, Any]:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout_seconds)
    except (LimitOverrunError, ValueError) as exc:
        if isinstance(exc, LimitOverrunError) or "Separator is not found" in str(exc):
            raise MCPClientError(
                f"MCP response too large: exceeded {MCP_MAX_RESPONSE_BYTES} bytes before newline"
            ) from exc
        raise
    if not line:
        raise EOFError
    if len(line) > MCP_MAX_RESPONSE_BYTES:
        raise MCPClientError(
            f"MCP response too large: {len(line)} bytes exceeds {MCP_MAX_RESPONSE_BYTES}"
        )
    if line.lower().startswith(b"content-length:"):
        header = await _read_remaining_header(reader, line)
        content_length = _content_length(header)
        if content_length <= 0:
            raise MCPClientError("MCP frame missing Content-Length")
        if content_length > MCP_MAX_RESPONSE_BYTES:
            raise MCPClientError(
                f"MCP response too large: Content-Length {content_length} exceeds "
                f"{MCP_MAX_RESPONSE_BYTES}"
            )
        body = await asyncio.wait_for(reader.readexactly(content_length), timeout_seconds)
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    data = json.loads(line.decode("utf-8"))
    return data if isinstance(data, dict) else {}


async def _read_framed_json(reader: asyncio.StreamReader, timeout_seconds: int) -> dict[str, Any]:
    header = await asyncio.wait_for(_read_header(reader), timeout_seconds)
    content_length = _content_length(header)
    if content_length <= 0:
        raise MCPClientError("MCP frame missing Content-Length")
    if content_length > MCP_MAX_RESPONSE_BYTES:
        raise MCPClientError(
            f"MCP response too large: Content-Length {content_length} exceeds {MCP_MAX_RESPONSE_BYTES}"
        )
    body = await asyncio.wait_for(reader.readexactly(content_length), timeout_seconds)
    data = json.loads(body.decode("utf-8"))
    return data if isinstance(data, dict) else {}


async def _read_remaining_header(reader: asyncio.StreamReader, first_line: bytes) -> bytes:
    buf = bytearray(first_line)
    if buf.endswith(b"\r\n\r\n") or buf.endswith(b"\n\n"):
        return bytes(buf)
    while True:
        line = await reader.readline()
        if not line:
            raise EOFError
        buf.extend(line)
        if buf.endswith(b"\r\n\r\n") or buf.endswith(b"\n\n"):
            return bytes(buf)
        if len(buf) > 8192:
            raise MCPClientError("MCP response header too large")


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
