from __future__ import annotations

from pathlib import Path

import pytest

from app.services.prd_sources.gitlab_mcp import extract_mcp_text, fetch_gitlab_file_via_mcp
from app.services.prd_sources.parser import parse_gitlab_file_url
from app.services.prd_sources.workspace_file import fetch_workspace_file


def test_parse_gitlab_blob_url() -> None:
    parsed = parse_gitlab_file_url(
        "http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/foo.md"
    )

    assert parsed.project == "zstackio/zstack"
    assert parsed.ref == "master"
    assert parsed.file_path == "docs/prd/foo.md"


def test_parse_gitlab_raw_url() -> None:
    parsed = parse_gitlab_file_url(
        "https://gitlab.zstack.io/zstackio/premium/-/raw/main/specs/a.md"
    )

    assert parsed.project == "zstackio/premium"
    assert parsed.ref == "main"
    assert parsed.file_path == "specs/a.md"


def test_workspace_file_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(ValueError, match="file_path must stay inside"):
        fetch_workspace_file(root=root, repo="zstack", file_path="../secret.md", ref=None)


def test_workspace_file_reads_markdown(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    prd_dir = root / "zstack" / "docs"
    prd_dir.mkdir(parents=True)
    (prd_dir / "prd.md").write_text("# Imported PRD\n\n## Login\n\nRequired.", encoding="utf-8")

    doc = fetch_workspace_file(root=root, repo="zstack", file_path="docs/prd.md", ref=None)

    assert doc.markdown.startswith("# Imported PRD")
    assert doc.suggested_name == "prd.md"
    assert doc.source_ref["source_type"] == "workspace"
    assert doc.source_ref["repo"] == "zstack"


def test_extract_mcp_text_accepts_text_content() -> None:
    text = extract_mcp_text({"content": [{"type": "text", "text": "# Git PRD"}]})

    assert text == "# Git PRD"


@pytest.mark.asyncio
async def test_fetch_gitlab_file_via_mcp_uses_injected_tool() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "# Remote PRD"}]}

    doc = await fetch_gitlab_file_via_mcp(
        url="http://gitlab.zstack.io/zstackio/zstack/-/blob/master/docs/prd/foo.md",
        project=None,
        file_path=None,
        ref=None,
        call_tool=call_tool,
    )

    assert doc.markdown == "# Remote PRD"
    assert doc.suggested_name == "foo.md"
    assert doc.source_ref["source_type"] == "gitlab_mcp"
    assert calls == [
        (
            "gl_get_file_contents",
            {"project": "zstackio/zstack", "file_path": "docs/prd/foo.md", "ref": "master"},
        )
    ]
