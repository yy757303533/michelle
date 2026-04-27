# ADR 0004 — Claude CLI subprocess as primary LLM channel

**Status**: Accepted (2026-04-27)

## Context

Michelle needs LLM access for three jobs: case generation, execution orchestration, failure diagnosis. The user has a Claude Max subscription but no API key.

## Decision

Primary LLM channel = `claude -p ... --output-format json [--mcp-config X]` subprocess.

Backup channels via a provider-agnostic `LLMGateway`:
1. Primary: `ClaudeCLIClient` (this ADR)
2. Fallback: `MiniMaxClient` (when Claude rate-limits)
3. Premium upgrade: `FlywheelClient` for Opus / GPT-5.4 (when quota recovers)

## Why CLI over a custom subscription proxy

- CLI is the **officially-sanctioned** way to use a subscription programmatically
- Reverse-engineered subscription proxies violate ToS and break under rotation/risk-controls
- `claude -p` exits cleanly to JSON, includes usage tokens, supports `--image` for vision input, supports `--mcp-config` for tool access — all of which we need

## Why provider-agnostic gateway

Claude Max has rate limits (5h rolling). A live demo running a big batch could hit them. We need transparent fallback so:

- Normal path = $0 (subscription is sunk cost)
- Backup path = ~MiniMax pricing (a few RMB)
- Premium path = Flywheel network gateway (Opus / GPT-5.4) for hard diagnosis cases

The gateway interface is one abstract method (`async chat(...) -> LLMResult`); adding a new provider is one class.

## Token visibility

Each LLM call logs `llm.completion` event with provider/model/tokens/latency. Logfire shows daily cumulative — we can spot trends before hitting limits.

## Consequences

- All LLM-touching code goes through `app/llm/gateway.py`, never directly `subprocess.run`
- Switching to API key in Phase 2 = swap one client class
- Demo cost: $0 unless we hit Claude limits

## Revisit when

- Throughput needs warrant a paid API key (sustained > 10M tokens/day)
- A new better model arrives that's not on any of our channels
