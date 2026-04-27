# ADR 0003 — Fork webtest-mcp-server as Michelle's execution kernel

**Status**: Accepted (2026-04-27)

## Context

The user's existing project `webtest-mcp-server` (in the same workspace, owned by them) overlaps ~70% with what Michelle needs to build for case generation, execution, and HTML reporting.

## Decision

**Fork `webtest-mcp-server` into `vendor/webtest-mcp/` and reuse its mature pieces.**

Reused:

- Excel case schema (用例编号 / 模块 / 测试类型 / 步骤 / 预期 / 等级 / 标签)
- HTML report generator (`save_test_results`, ~400 lines of well-tested code with screenshot embedding + cumulative report)
- `@playwright/mcp` integration patterns
- Multi-project layout (`projects/<key>/`)

Replaced:

- The MCP server itself (Michelle uses REST + has its own MCP server, see ADR 0005)
- Excel-only persistence (Michelle uses SQLite as canonical, Excel as import/export format)
- Direct Claude Code orchestration (Michelle has a Web UI workflow with review states)

## Why fork, not depend?

- Quick MVP: cp + edit > write a wrapper layer
- We will materially diverge (review workflow, AI diagnosis, observability)
- License clarity (it's the user's project) makes forking risk-free

## Consequences

- ~3-4 days saved vs writing from scratch
- We must keep `vendor/webtest-mcp/` re-syncable (documented in `vendor/README.md`)
- Story for interview: "I refactored my prior project into the execution kernel of a new platform" — shows growth

## Revisit when

- Upstream webtest-mcp gains features we want and divergence is small enough to upstream
