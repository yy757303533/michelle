"""Unit tests for CodexCLIClient — subprocess fully mocked."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import LLMAuthError, LLMResponseFormatError, RateLimitError
from app.llm.codex_cli import CodexCLIClient


def _mk_proc(*, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_codex_disabled_when_binary_missing():
    with patch("app.llm.codex_cli.shutil.which", return_value=None):
        c = CodexCLIClient(binary="codex-not-installed")
    assert c.enabled is False
    with pytest.raises(LLMAuthError):
        await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_codex_happy_path_returns_stdout():
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    proc = _mk_proc(stdout=b"ok answer\n")
    with patch("app.llm.codex_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        r = await c.chat("hi", prompt_version="probe_v1")
    assert r.text == "ok answer"
    assert r.provider == "codex-cli"


@pytest.mark.asyncio
async def test_codex_exec_uses_noninteractive_fast_path():
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    proc = _mk_proc(stdout=b"ok\n")
    create = AsyncMock(return_value=proc)

    with patch("app.llm.codex_cli.asyncio.create_subprocess_exec", create):
        await c.chat("hi", prompt_version="probe_v1")

    args = create.call_args.args
    kwargs = create.call_args.kwargs
    assert args[:2] == ("codex", "exec")
    assert "--ephemeral" in args
    assert "--ignore-rules" in args
    assert "--skip-git-repo-check" in args
    assert "--output-last-message" in args
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert kwargs["cwd"] == "/private/tmp"


@pytest.mark.asyncio
async def test_codex_prefers_output_last_message_file(tmp_path):
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    proc = _mk_proc(stdout=b"session header\nfinal answer\n")

    async def _spawn(*args, **kwargs):
        out_path = args[args.index("--output-last-message") + 1]
        tmp_path.joinpath("seen").write_text(out_path)
        return proc

    async def _communicate():
        out_path = tmp_path.joinpath("seen").read_text()
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("final answer\n")
        return b"session header\nfinal answer\n", b""

    proc.communicate = AsyncMock(side_effect=_communicate)
    with patch("app.llm.codex_cli.asyncio.create_subprocess_exec", AsyncMock(side_effect=_spawn)):
        r = await c.chat("hi", prompt_version="probe_v1")

    assert r.text == "final answer"


@pytest.mark.asyncio
async def test_codex_rate_limit_in_stderr():
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    proc = _mk_proc(returncode=1, stderr=b"too many requests, slow down")
    with patch("app.llm.codex_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(RateLimitError):
            await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_codex_auth_in_stderr():
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    proc = _mk_proc(returncode=1, stderr=b"please run /login first")
    with patch("app.llm.codex_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(LLMAuthError):
            await c.chat("hi", prompt_version="probe_v1")


@pytest.mark.asyncio
async def test_codex_image_input_rejected():
    with patch("app.llm.codex_cli.shutil.which", return_value="/usr/bin/codex"):
        c = CodexCLIClient()
    with pytest.raises(LLMResponseFormatError):
        await c.chat("hi", prompt_version="probe_v1", image=b"\x89PNG")
