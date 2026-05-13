# Configurable Dev Context Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Michelle configurable against an external zstack-workspace and zstack-dev-mcp, then use that configuration for PRD import from GitLab/workspace without moving the Michelle repo.

**Architecture:** Michelle remains the product system and owns PRD/Coverage/Run/Diagnosis data. A new DevContext layer reads configured workspace metadata and calls zstack-dev-mcp through the existing stdio MCP client. PRD import becomes source-based, while `/api/prd/upload` stays compatible.

**Tech Stack:** FastAPI, SQLModel, Pydantic settings, existing `StdioMCPClient`, pytest, React/TanStack Query.

---

## File Structure

- Modify `backend/app/config.py`
  - Add `michelle_workspace_root`, `michelle_zdev_mcp_command`, `michelle_zdev_mcp_args`, `michelle_zdev_mcp_cwd`, `michelle_zdev_mcp_timeout_seconds`.
- Modify `backend/app/agent/mcp_stdio.py`
  - Let callers pass extra subprocess environment so zstack-dev-mcp can receive `WORKSPACE_DIR`.
- Create `backend/app/services/dev_context/workspace.py`
  - Detect configured workspace root, `.gitmodules` repos, actual repo directories, branch/commit status.
- Create `backend/app/services/dev_context/zdev_mcp.py`
  - Build a zstack-dev-mcp stdio client and expose `call_tool`.
- Create `backend/app/services/prd_sources/models.py`
  - Define import source request/result models and source metadata shape.
- Create `backend/app/services/prd_sources/parser.py`
  - Parse GitLab blob/raw URLs into `project`, `file_path`, `ref`.
- Create `backend/app/services/prd_sources/workspace_file.py`
  - Read markdown from a whitelisted repo under configured workspace root.
- Create `backend/app/services/prd_sources/gitlab_mcp.py`
  - Fetch GitLab file contents via `gl_get_file_contents`.
- Create `backend/app/services/prd_sources/service.py`
  - Dispatch source providers and return markdown + source metadata.
- Modify `backend/app/models/project.py`
  - Add `PRD.source_ref` JSON field with default `{}`.
- Modify `backend/app/api/prd.py`
  - Extract shared PRD persistence helper from upload.
  - Add `POST /api/prd/import`.
  - Include `source_ref` in list/get/upload/import responses.
- Create `backend/app/api/dev_context.py`
  - Add status endpoint for configured workspace and zstack-dev-mcp.
- Modify `backend/app/main.py`
  - Register `dev_context` router.
- Add backend tests:
  - `backend/tests/unit/test_dev_context_workspace.py`
  - `backend/tests/unit/test_prd_source_parser.py`
  - `backend/tests/unit/test_prd_import_api.py`
- Modify `frontend/src/routes/prd.tsx`
  - Add import source controls for GitLab URL and workspace repo/path.
  - Keep markdown paste/upload path working.

## Task 1: Configuration and Workspace Status

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/services/dev_context/__init__.py`
- Create: `backend/app/services/dev_context/workspace.py`
- Create: `backend/app/api/dev_context.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_dev_context_workspace.py`

- [ ] **Step 1: Write failing tests for workspace detection**

```python
from pathlib import Path

from app.services.dev_context.workspace import inspect_workspace


def test_inspect_workspace_reports_disabled_when_root_empty():
    status = inspect_workspace("")
    assert status.enabled is False
    assert status.ok is False
    assert status.repos == []


def test_inspect_workspace_reads_gitmodules_and_repo_dirs(tmp_path: Path):
    root = tmp_path / "zstack-workspace"
    root.mkdir()
    (root / ".gitmodules").write_text(
        '[submodule "zstack"]\n\tpath = zstack\n\turl = ../zstack.git\n',
        encoding="utf-8",
    )
    (root / "zstack").mkdir()
    status = inspect_workspace(str(root))
    assert status.enabled is True
    assert status.ok is True
    assert status.root == str(root.resolve())
    assert status.repos[0].name == "zstack"
    assert status.repos[0].path == "zstack"
    assert status.repos[0].exists is True
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run pytest tests/unit/test_dev_context_workspace.py -q`

Expected: import failure because `app.services.dev_context.workspace` does not exist.

- [ ] **Step 3: Add config fields**

Add these fields to `Settings` in `backend/app/config.py`:

```python
    michelle_workspace_root: str = ""
    """Optional external zstack-workspace root used for PRD/code/dev context."""
    michelle_zdev_mcp_command: str = "node"
    michelle_zdev_mcp_args: str = ""
    """Shell-like argument string, e.g. /path/to/zstack-dev-mcp/dist/index.js."""
    michelle_zdev_mcp_cwd: str = ""
    michelle_zdev_mcp_timeout_seconds: int = 60
```

- [ ] **Step 4: Implement workspace inspection**

Create `backend/app/services/dev_context/workspace.py` with typed models:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import configparser


@dataclass(frozen=True)
class WorkspaceRepoStatus:
    name: str
    path: str
    exists: bool


@dataclass(frozen=True)
class WorkspaceStatus:
    enabled: bool
    ok: bool
    root: str
    detail: str
    repos: list[WorkspaceRepoStatus]


def inspect_workspace(root: str) -> WorkspaceStatus:
    if not root:
        return WorkspaceStatus(False, False, "", "MICHELLE_WORKSPACE_ROOT is not configured", [])
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        return WorkspaceStatus(True, False, str(root_path), "workspace root does not exist", [])
    repos = _read_gitmodule_repos(root_path)
    return WorkspaceStatus(True, True, str(root_path), "ready", repos)


def _read_gitmodule_repos(root_path: Path) -> list[WorkspaceRepoStatus]:
    gitmodules = root_path / ".gitmodules"
    if not gitmodules.exists():
        return []
    parser = configparser.ConfigParser()
    parser.read(gitmodules, encoding="utf-8")
    repos: list[WorkspaceRepoStatus] = []
    for section in parser.sections():
        path = parser.get(section, "path", fallback="")
        if not path:
            continue
        name = path.rstrip("/").split("/")[-1]
        repos.append(WorkspaceRepoStatus(name=name, path=path, exists=(root_path / path).exists()))
    return repos
```

- [ ] **Step 5: Add status API**

Create `backend/app/api/dev_context.py`:

```python
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.services.dev_context.workspace import inspect_workspace

router = APIRouter()


@router.get("/status")
async def get_dev_context_status() -> dict:
    workspace = inspect_workspace(settings.michelle_workspace_root)
    return {
        "data": {
            "workspace": {
                "enabled": workspace.enabled,
                "ok": workspace.ok,
                "root": workspace.root,
                "detail": workspace.detail,
                "repos": [repo.__dict__ for repo in workspace.repos],
            },
            "zdev_mcp": {
                "configured": bool(settings.michelle_zdev_mcp_args),
                "command": settings.michelle_zdev_mcp_command,
                "cwd": settings.michelle_zdev_mcp_cwd,
            },
        }
    }
```

Register in `backend/app/main.py` under `/api/dev-context`.

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_dev_context_workspace.py -q`

Expected: PASS.

## Task 2: PRD Source Parsing and Workspace Provider

**Files:**
- Create: `backend/app/services/prd_sources/__init__.py`
- Create: `backend/app/services/prd_sources/models.py`
- Create: `backend/app/services/prd_sources/parser.py`
- Create: `backend/app/services/prd_sources/workspace_file.py`
- Test: `backend/tests/unit/test_prd_source_parser.py`

- [ ] **Step 1: Write parser/provider tests**

```python
from pathlib import Path

import pytest

from app.services.prd_sources.parser import parse_gitlab_file_url
from app.services.prd_sources.workspace_file import fetch_workspace_file


def test_parse_gitlab_blob_url():
    parsed = parse_gitlab_file_url(
        "http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/foo.md"
    )
    assert parsed.project == "zstackio/zstack"
    assert parsed.ref == "master"
    assert parsed.file_path == "docs/prd/foo.md"


def test_workspace_file_rejects_path_escape(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(ValueError):
        fetch_workspace_file(root=root, repo="zstack", file_path="../secret.md", ref=None)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run pytest tests/unit/test_prd_source_parser.py -q`

Expected: import failure because `app.services.prd_sources` does not exist.

- [ ] **Step 3: Define source document models**

Create `backend/app/services/prd_sources/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceType = Literal["markdown", "workspace", "gitlab_mcp", "confluence_mcp"]


@dataclass(frozen=True)
class PRDSourceDocument:
    markdown: str
    suggested_name: str
    source_ref: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Implement GitLab URL parser**

Implement `parse_gitlab_file_url(url: str)` supporting `/-/blob/<ref>/<path>` and `/-/raw/<ref>/<path>`. Raise `ValueError` for unsupported URLs.

- [ ] **Step 5: Implement workspace file reader**

Implement `fetch_workspace_file(root: Path, repo: str, file_path: str, ref: str | None)` using direct file reads only when `ref` is empty. Reject absolute paths, `..`, missing repos, missing files, directories, and files larger than a conservative limit such as 2 MiB. Return `PRDSourceDocument`.

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_prd_source_parser.py -q`

Expected: PASS.

## Task 3: zstack-dev-mcp Provider

**Files:**
- Modify: `backend/app/agent/mcp_stdio.py`
- Create: `backend/app/services/dev_context/zdev_mcp.py`
- Create: `backend/app/services/prd_sources/gitlab_mcp.py`
- Test: `backend/tests/unit/test_prd_source_parser.py`

- [ ] **Step 1: Add unit test for GitLab MCP provider with fake client**

Add a test that injects a fake `call_tool` coroutine returning MCP text content and asserts markdown and source metadata are preserved.

- [ ] **Step 2: Allow extra env in `StdioMCPClient`**

Add `extra_env: dict[str, str] | None = None` to the constructor and merge it into `_mcp_subprocess_env(self.cwd)` before `create_subprocess_exec`.

- [ ] **Step 3: Add zdev MCP client builder**

Create `backend/app/services/dev_context/zdev_mcp.py` with:

```python
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from app.agent.mcp_stdio import StdioMCPClient
from app.config import settings


def build_zdev_mcp_client() -> StdioMCPClient:
    if not settings.michelle_zdev_mcp_args:
        raise RuntimeError("MICHELLE_ZDEV_MCP_ARGS is not configured")
    cwd = Path(settings.michelle_zdev_mcp_cwd or settings.michelle_workspace_root or ".").resolve()
    extra_env = {}
    if settings.michelle_workspace_root:
        extra_env["WORKSPACE_DIR"] = str(Path(settings.michelle_workspace_root).resolve())
    return StdioMCPClient(
        command=settings.michelle_zdev_mcp_command,
        args=shlex.split(settings.michelle_zdev_mcp_args),
        cwd=cwd,
        timeout_seconds=settings.michelle_zdev_mcp_timeout_seconds,
        extra_env=extra_env,
    )
```

- [ ] **Step 4: Implement GitLab MCP source provider**

Create `fetch_gitlab_file_via_mcp(url: str | None, project: str | None, file_path: str | None, ref: str | None)` that parses URL when provided, calls `gl_get_file_contents`, extracts MCP text, and returns `PRDSourceDocument`.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && uv run pytest tests/unit/test_prd_source_parser.py tests/unit/test_mcp_stdio.py -q`

Expected: PASS.

## Task 4: Unified PRD Import API

**Files:**
- Modify: `backend/app/models/project.py`
- Modify: `backend/app/api/prd.py`
- Create: `backend/app/services/prd_sources/service.py`
- Test: `backend/tests/unit/test_prd_import_api.py`

- [ ] **Step 1: Write API tests**

Add tests for:

- `POST /api/prd/import` with `{source: {type: "markdown", markdown: "# Title"}}`.
- `POST /api/prd/import` stores `source_ref`.
- Existing `POST /api/prd/upload` still works.

- [ ] **Step 2: Add `source_ref` to PRD model**

Add to `PRD`:

```python
    source_ref: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    """Where this PRD content came from: markdown, workspace, gitlab_mcp, confluence, jira."""
```

- [ ] **Step 3: Extract shared persistence helper**

In `backend/app/api/prd.py`, create `_persist_prd(...)` that accepts `project_id`, `name`, `markdown`, `source_ref`, `request`, `session`, and returns the same response data currently built by `upload_prd`.

- [ ] **Step 4: Add import request schema and endpoint**

Add:

```python
class PRDImportIn(BaseModel):
    project_id: str
    name: str = ""
    source: dict[str, Any]
```

`POST /api/prd/import` should dispatch to `fetch_prd_source`, then call `_persist_prd`.

- [ ] **Step 5: Include source_ref in responses**

Include `source_ref` in list, get, upload, and import response data.

- [ ] **Step 6: Run PRD tests**

Run: `cd backend && uv run pytest tests/unit/test_prd_import_api.py tests/unit/test_projects_and_prd_jobs.py -q`

Expected: PASS.

## Task 5: PRD Page UI

**Files:**
- Modify: `frontend/src/routes/prd.tsx`

- [ ] **Step 1: Add source mode state**

Add mode state:

```ts
type PrdSourceMode = "markdown" | "gitlab" | "workspace";
```

Add state for `gitlabUrl`, `workspaceRepo`, `workspaceFilePath`, and `workspaceRef`.

- [ ] **Step 2: Add import mutation**

Create a mutation that calls `/api/prd/import` with different payloads based on selected mode.

- [ ] **Step 3: Add compact source controls**

In the existing PRD page, keep the raw markdown textarea. Add tabs or segmented buttons for:

- Markdown
- GitLab URL
- Workspace File

Do not turn the page into a generic tool console.

- [ ] **Step 4: Preserve existing analyze flow**

On import success, set the returned PRD response into the same state used by upload, write `?prd_id=`, and leave chapter selection/generation unchanged.

- [ ] **Step 5: Run frontend validation**

Run: `cd frontend && pnpm lint`

Expected: PASS.

## Task 6: Verification

**Files:**
- No new source files.

- [ ] **Step 1: Run backend focused tests**

Run: `cd backend && uv run pytest tests/unit/test_dev_context_workspace.py tests/unit/test_prd_source_parser.py tests/unit/test_prd_import_api.py -q`

Expected: PASS.

- [ ] **Step 2: Run backend lint**

Run: `cd backend && uv run ruff check .`

Expected: PASS.

- [ ] **Step 3: Run frontend lint**

Run: `cd frontend && pnpm lint`

Expected: PASS.

- [ ] **Step 4: Manual smoke**

Start the app with:

```bash
MICHELLE_WORKSPACE_ROOT=/Users/yy/code/zstack-workspace \
MICHELLE_ZDEV_MCP_ARGS=/Users/yy/code/zstack-workspace/zstack-dev-mcp/dist/index.js \
make dev
```

Then verify:

- `GET /api/dev-context/status` reports workspace repos.
- PRD page can still import markdown.
- PRD page can import a workspace file.
- GitLab URL import either succeeds or returns a clear configuration/MCP error.

