"""Spawn `claude -p` with `@playwright/mcp` loaded, capture stream, parse trace.

This is the heart of execution. Day 6 will wrap this in a service that takes a
TestCase row and returns a Run row. Day 2 just needs the primitive.

Subprocess invocation:

    claude -p "<prompt>"
        --mcp-config <path>
        --strict-mcp-config        # only use our config, ignore user config
        --permission-mode bypassPermissions
        --output-format stream-json
        --verbose                   # required to enable streamed events
        --dangerously-skip-permissions   # alt to permission-mode for older builds
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from app.agent.claude_env import build_claude_subprocess_env
from app.agent.mcp_config import build_playwright_mcp_config, write_config
from app.agent.trace_parser import ParsedRun, parse_stream, redact_bytes
from app.config import settings
from app.obs import EVENTS, get_logger

_log = get_logger(__name__)


class ClaudeRunnerError(RuntimeError):
    pass


@dataclass
class RunRequest:
    prompt: str
    work_dir: Path
    """Working directory for `claude` (also where MCP config & output land)."""
    timeout_seconds: int = 300
    extra_mcp_args: list[str] | None = None
    """Extra args appended to `npx @playwright/mcp` (e.g. ['--storage-state', '...']) ."""
    headless: bool = True
    isolated: bool = True
    secrets: list[str] | None = None
    """Literal strings (passwords, tokens) to redact from logs, persisted
    artifacts, and StepEvent rows. The orchestrator builds this list from the
    target credentials baked into the prompt."""


@dataclass
class RunOutcome:
    parsed: ParsedRun
    stdout_path: Path
    stderr_path: Path
    mcp_config_path: Path
    exit_code: int
    elapsed_ms: int


def _resolve_claude_binary() -> str:
    return settings.claude_cli_path or "claude"


async def run_claude_with_playwright(req: RunRequest) -> RunOutcome:
    """Run a Claude session with `@playwright/mcp` loaded; return parsed trace."""
    work = req.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)

    mcp_path = work / "mcp.json"
    write_config(
        mcp_path,
        build_playwright_mcp_config(
            isolated=req.isolated,
            headless=req.headless,
            extra_args=req.extra_mcp_args,
        ),
    )

    stdout_path = work / "claude.stream.jsonl"
    stderr_path = work / "claude.err.log"

    cmd = [
        _resolve_claude_binary(),
        "-p",
        req.prompt,
        "--mcp-config",
        str(mcp_path),
        "--strict-mcp-config",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]

    log = _log.bind(work_dir=str(work))
    # The prompt position (cmd[2]) carries credentials for login smoke runs.
    # Log a redacted shape so operators can see invocation flags without
    # leaking secrets to log aggregators.
    safe_cmd = list(cmd)
    safe_cmd[2] = "<redacted-prompt>"
    log.info("agent.claude.spawn", cmd=shlex.join(safe_cmd))

    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(work),
        env=build_claude_subprocess_env(michelle_run=True),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=req.timeout_seconds
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        elapsed = int((time.monotonic() - t0) * 1000)
        log.error("agent.claude.timeout", elapsed_ms=elapsed, timeout=req.timeout_seconds)
        raise ClaudeRunnerError(f"claude timed out after {req.timeout_seconds}s") from exc

    elapsed = int((time.monotonic() - t0) * 1000)

    redacted_stdout = redact_bytes(stdout_bytes, req.secrets)
    redacted_stderr = redact_bytes(stderr_bytes, req.secrets)
    stdout_path.write_bytes(redacted_stdout)
    stderr_path.write_bytes(redacted_stderr)

    parsed = parse_stream(
        redacted_stdout.decode("utf-8", errors="replace").splitlines(),
        secrets=req.secrets,
    )

    if proc.returncode:
        log.error(
            "agent.claude.nonzero_exit",
            exit_code=proc.returncode,
            stderr_tail=redacted_stderr.decode("utf-8", errors="replace")[-2000:],
        )

    log.info(
        EVENTS.RUN_COMPLETED.name,
        exit_code=proc.returncode,
        elapsed_ms=elapsed,
        steps=len(parsed.steps),
        cost_usd=parsed.summary.cost_usd,
        success_hint=parsed.summary.success,
    )

    return RunOutcome(
        parsed=parsed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        mcp_config_path=mcp_path,
        exit_code=proc.returncode or 0,
        elapsed_ms=elapsed,
    )


def render_login_smoke_prompt(*, url: str, username: str, password: str) -> str:
    """Day 2 smoke prompt — proves the agent + MCP stack works end-to-end."""
    return f"""You are a browser test agent. Drive a real Chromium browser via the
playwright MCP server (`@playwright/mcp`) to perform a single user flow.

## Task: Log into the target web app

Target URL: {url}
Username: {username}
Password: {password}

## Steps

1. Call mcp__playwright__browser_navigate to open the URL.
2. Call mcp__playwright__browser_snapshot to see the current page (ARIA tree).
3. Find the username textbox in the snapshot. Use mcp__playwright__browser_type to enter "{username}".
4. Find the password textbox. Use mcp__playwright__browser_type to enter "{password}".
5. Find the login button (text "登录" or "Login"). Use mcp__playwright__browser_click on it.
6. Wait briefly for navigation. Call mcp__playwright__browser_snapshot again.
7. Take a screenshot with mcp__playwright__browser_take_screenshot (filename: "after-login.png").
8. Determine if login succeeded:
   - If you see post-login UI (sidebar, dashboard, top nav, user badge, etc.), login SUCCEEDED.
   - If you still see the login form or an error, login FAILED.

## Output

End your final message with EXACTLY this on a single line:

RESULT={{"login":"success|failed","new_title":"...","evidence":"...","step_count":N}}
"""
