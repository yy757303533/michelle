# ADR 0002 — `@playwright/mcp` over DIY DOM-augmented vision agent

**Status**: Accepted (2026-04-27, supersedes earlier vision-agent plan)

## Context

The execution engine needs to drive a real browser and follow natural-language test steps.

We initially planned a DIY agent: take screenshot → send screenshot + DOM elements list to a vision LLM (MiniMax-Text-01) → LLM picks element index → Playwright clicks. We benchmarked this and it works (3/3 intents on the ZStack login page).

But shortly after that, the user pointed at their own prior project `webtest-mcp-server`, which uses `@playwright/mcp` (Microsoft's official Playwright MCP) — ARIA-tree based, deterministic, no per-step LLM call.

## Decision

Use **`@playwright/mcp`** as the execution engine, driven by `claude -p --mcp-config`. The DIY vision agent is shelved as a fallback for areas where ARIA is too thin.

## Comparison

| Dimension | DIY vision agent | `@playwright/mcp` |
|-----------|------------------|-------------------|
| Per-step latency | 1.8s (LLM call) | ~100ms (deterministic) |
| Per-step LLM cost | 6.5k tokens | 0 |
| Demo stability | LLM JSON occasionally malformed | high (ARIA stable) |
| AI decision location | every step | orchestration layer (which tool to call) |
| Story for interview | "I built a vision agent" | "industrial MCP + AI at the right layers" |
| Risk to a demo if LLM hiccups | medium-high | low |

## Why this is the *senior* call

Concentrate AI compute where it actually adds value (PRD → cases generation, failure → diagnosis). The execution loop benefits from determinism, not from imagination.

The DIY agent narrative was novel but bought us coordination accuracy problems (vision LLMs are off by 300+ pixels on coordinates) we'd then have to engineer around.

## Consequences

- Drop the Node "worker" tier in our stack — `@playwright/mcp` is invoked by the spawned Claude subprocess directly
- LLM Gateway's vision adapter becomes optional / fallback
- Trace data now comes from parsing Claude's `tool_use` records, not from a custom JSONL emitter

## Revisit when

- ARIA coverage on a target site is too thin (`browser_snapshot` returns weak data)
- We need to test custom-rendered canvas / WebGL UIs with no DOM semantics
