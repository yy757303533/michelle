# ADR 0005 — Agent-native surface (MCP + Skills + Subagents + Hooks)

**Status**: Accepted (2026-04-27)

## Context

Michelle is an AI-native test platform. If only humans can use it (via the Web UI), we miss half the audience: AI agents (Claude Code sessions, Cursor, future custom agents). The compound-engineering principle says: **anything a human can do, an agent should be able to do too.**

## Decision

Provide three layers of agent surface, each with a distinct audience:

| Layer | What | For whom |
|-------|------|---------|
| **1. REST API** (`/api/...`) | Standard HTTP — `POST /api/runs`, `GET /api/cases`, etc | Web UI + any HTTP client + LLM with `curl` access |
| **2. Claude Code Skills** (`.claude/skills/michelle-*/`) | Slash commands — `/michelle-run`, `/michelle-diagnose`, `/michelle-suggest` | Claude Code users (power users in terminal) |
| **3. MCP server** (`backend/app/mcp/server.py`) | `michelle.execute_case` etc as MCP tools | Any MCP client (Cursor, Windsurf, custom agents) — agent-to-agent |

Plus two supporting structures:

- **Subagent definitions** (`.claude/agents/*.md`) — `michelle-diagnoser`, `michelle-reviewer` for parallel/specialised AI work
- **Internal hooks** (`backend/app/agent/hooks.py`) — auto-trigger flows on business events (case approved → optional auto-run, run failed → auto-diagnose, diagnosis confirmed → sediment)

## Why three layers, not just one

- REST is universal but verbose for AI ergonomics
- Slash commands are zero-friction for Claude Code users
- MCP is the emerging standard for agent-to-tool communication; a platform that ignores it is opting out of the agent ecosystem

## Why hooks separate from external Claude Code hooks

Internal hooks (this codebase) are about *business event chaining*: case approved → run, run failed → diagnose. They're tested with the rest of the platform.

External Claude Code hooks (`.claude/settings.json` shell hooks) are user-side ergonomics ("ring a bell when a run finishes") — out of scope for the platform itself.

## Consequences

- Day 6 implements MCP server bodies (currently stubs)
- The Skills MUST work via REST — we never duplicate logic
- Story for interview: "Michelle is agent-native — the same execute_case capability is reachable as a button, a slash command, or an MCP tool. Any AI you build can use this platform without modification."

## Revisit when

- A new agent protocol becomes dominant (e.g., A2A) and overlaps with MCP enough to consolidate
