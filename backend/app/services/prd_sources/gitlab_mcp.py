"""GitLab PRD source provider backed by zstack-dev-mcp."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.services.dev_context.zdev_mcp import call_zdev_tool
from app.services.prd_sources.models import PRDSourceDocument
from app.services.prd_sources.parser import parse_gitlab_file_url

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def fetch_gitlab_file_via_mcp(
    *,
    url: str | None,
    project: str | None,
    file_path: str | None,
    ref: str | None,
    call_tool: ToolCaller = call_zdev_tool,
) -> PRDSourceDocument:
    if url:
        parsed = parse_gitlab_file_url(url)
        project = project or parsed.project
        file_path = file_path or parsed.file_path
        ref = ref or parsed.ref
    if not project or not file_path:
        raise ValueError("gitlab_mcp source requires url or project + file_path")
    args = {"project": project, "file_path": file_path}
    if ref:
        args["ref"] = ref
    result = await call_tool("gl_get_file_contents", args)
    markdown = extract_mcp_text(result)
    return PRDSourceDocument(
        markdown=markdown,
        suggested_name=Path(file_path).name or project.split("/")[-1],
        source_ref={
            "source_type": "gitlab_mcp",
            "repo": project,
            "file_path": file_path,
            "ref": ref or "",
            "url": url or "",
        },
    )


def extract_mcp_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        texts = [
            str(item.get("text"))
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and item.get("text") is not None
        ]
        if texts:
            return "\n".join(texts)
    if isinstance(result.get("text"), str):
        return str(result["text"])
    if isinstance(result.get("data"), str):
        return str(result["data"])
    raise ValueError("MCP tool response did not include text content")
