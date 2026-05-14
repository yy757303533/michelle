"""Publish Michelle diagnosis summaries to external collaboration systems."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.models import Diagnosis
from app.services.dev_context.zdev_mcp import call_zdev_tool
from app.services.prd_sources.gitlab_mcp import extract_mcp_text

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def build_diagnosis_comment(diag: Diagnosis) -> str:
    return (
        "Michelle diagnosis\n\n"
        f"- Run: {diag.run_id}\n"
        f"- Case: {diag.case_id}\n"
        f"- Category: {diag.category}\n"
        f"- Confidence: {diag.confidence:.2f}\n\n"
        f"Reasoning:\n{diag.reasoning or '-'}\n\n"
        f"Suggested fix:\n{diag.fix_suggestion or '-'}"
    )


async def publish_diagnosis(
    *,
    diag: Diagnosis,
    target: dict[str, Any],
    call_tool: ToolCaller = call_zdev_tool,
) -> dict[str, Any]:
    target_type = str(target.get("type") or "")
    comment = str(target.get("comment") or "") or build_diagnosis_comment(diag)
    if target_type == "jira":
        issue_key = str(target.get("issue_key") or target.get("issueKey") or "")
        if not issue_key:
            raise ValueError("jira publish requires issue_key")
        result = await call_tool("jira_add_comment", {"issueKey": issue_key, "comment": comment})
        return {"ok": True, "target_type": target_type, "text": extract_mcp_text(result)[:2000]}
    if target_type == "confluence":
        page_id = str(target.get("page_id") or target.get("pageId") or "")
        if not page_id:
            raise ValueError("confluence publish requires page_id")
        result = await call_tool("confluence_add_comment", {"pageId": page_id, "comment": comment})
        return {"ok": True, "target_type": target_type, "text": extract_mcp_text(result)[:2000]}
    if target_type == "gitlab_discussion":
        project = str(target.get("project") or "")
        mr_iid = int(target.get("mr_iid") or 0)
        discussion_id = str(target.get("discussion_id") or "")
        if not project or not mr_iid or not discussion_id:
            raise ValueError("gitlab_discussion publish requires project, mr_iid, discussion_id")
        result = await call_tool(
            "gl_reply_to_discussion",
            {
                "project": project,
                "mr_iid": mr_iid,
                "discussion_id": discussion_id,
                "body": comment,
            },
        )
        return {"ok": True, "target_type": target_type, "text": extract_mcp_text(result)[:2000]}
    raise ValueError("unsupported publish target type")
