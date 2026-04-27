# ADR 0001 — Vite + React 19 over Next.js

**Status**: Accepted (2026-04-27)

## Context

Frontend framework choice for a 2-week MVP internal test platform with separate Python backend.

## Decision

Use **Vite + React 19 + TypeScript** (with TanStack Router) instead of Next.js.

## Why

| Need | Next.js value | Our reality |
|------|---------------|-------------|
| API Routes / Server Actions / RSC | yes | **zero** — backend is FastAPI |
| SEO / SSR | yes | **zero** — internal tool |
| Image optimisation, Edge runtime | yes | not relevant |
| Fast dev server | medium | Vite faster |
| Mental model on a separated backend/frontend | server/client component split adds load | **pure SPA, simplest** |

In a 2-week sprint, every removed concept saves time. Next.js would have us pay for SSR / Server Components without using them.

## Consequences

- Lose ergonomics that come from co-located backend (none we needed)
- TanStack Router gives us file-based routing + type safety, comparable DX to Next App Router for our scale
- Static build → trivial deploy (any nginx, S3, file serve)

## Revisit when

- We need server-rendered marketing pages
- We need streaming SSR for an LLM chat surface
