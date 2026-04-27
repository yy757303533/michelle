# vendor/

Vendored third-party / forked code that Michelle imports.

## webtest-mcp/

Forked from the user's prior project `webtest-mcp-server` (in the same workspace).

**What we use from it**:

- `src/webtest_mcp/loader.py` — Excel case schema + filtering primitives
- `src/webtest_mcp/server.py::save_test_results` — HTML report generation (~400 lines)
- `src/webtest_mcp/server.py::generate_cases` — case schema validation on write
- `mcp-config.json` — `@playwright/mcp` reference setup

**What we replace**:

- The MCP `server.py` itself — Michelle exposes its capabilities via REST API (and optionally as its own MCP server, see `backend/app/mcp/`)
- `projects/` — replaced by SQLite + per-project artifacts dir
- Excel-only persistence — replaced by SQLite (Excel kept as import/export format)

## Update workflow

This is a one-shot copy for MVP. If the upstream changes, re-sync with:

```bash
rsync -a --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
  /Users/yy/code/yal/webtest-mcp-server/ \
  /Users/yy/code/yal/michelle/vendor/webtest-mcp/
```
