"""External DevContext evidence through zstack-dev-mcp."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.services.dev_context.zdev_mcp import call_zdev_tool
from app.services.prd_sources.gitlab_mcp import extract_mcp_text

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b", re.IGNORECASE)
_GITLAB_JOB_RE = re.compile(r"/-/jobs/(\d+)")
_CONFLUENCE_PAGE_RE = re.compile(r"(?:pageId=|/pages/)(\d+)")
_JENKINS_URL_RE = re.compile(r"https?://[^\s)'\"]+/job/[^\s)'\"]+")


def extract_jira_keys(text_parts: list[str], *, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text in text_parts:
        for match in _JIRA_KEY_RE.findall(text or ""):
            key = match.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= limit:
                return out
    return out


def extract_gitlab_job_ids(text_parts: list[str], *, limit: int = 3) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for text in text_parts:
        for raw in _GITLAB_JOB_RE.findall(text or ""):
            job_id = int(raw)
            if job_id in seen:
                continue
            seen.add(job_id)
            out.append(job_id)
            if len(out) >= limit:
                return out
    return out


def extract_confluence_page_ids(text_parts: list[str], *, limit: int = 3) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text in text_parts:
        for page_id in _CONFLUENCE_PAGE_RE.findall(text or ""):
            if page_id in seen:
                continue
            seen.add(page_id)
            out.append(page_id)
            if len(out) >= limit:
                return out
    return out


def extract_jenkins_build_urls(text_parts: list[str], *, limit: int = 3) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text in text_parts:
        for raw in _JENKINS_URL_RE.findall(text or ""):
            url = raw.rstrip("/.,;")
            url = re.sub(r"/(?:console|consoleFull|artifact/.*)$", "", url)
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
            if len(out) >= limit:
                return out
    return out


async def collect_external_context(
    *,
    text_parts: list[str],
    call_tool: ToolCaller = call_zdev_tool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mcp_args = str((config or {}).get("zdev_mcp_args") or settings.michelle_zdev_mcp_args)
    if not mcp_args and call_tool is call_zdev_tool:
        return _empty_external_context()
    jira = []
    for key in extract_jira_keys(text_parts):
        try:
            result = await call_tool("jira_get_issue", {"issueKey": key})
            jira.append({"key": key, "ok": True, "text": extract_mcp_text(result)[:4000]})
        except Exception as exc:  # noqa: BLE001 - evidence collection is best-effort
            jira.append({"key": key, "ok": False, "error": str(exc)[:300]})
    ci = []
    for job_id in extract_gitlab_job_ids(text_parts):
        try:
            result = await call_tool("ci_get_job_logs", {"jobId": job_id})
            ci.append({"job_id": job_id, "ok": True, "text": extract_mcp_text(result)[:6000]})
        except Exception as exc:  # noqa: BLE001
            ci.append({"job_id": job_id, "ok": False, "error": str(exc)[:300]})
    confluence = []
    for page_id in extract_confluence_page_ids(text_parts):
        try:
            result = await call_tool("confluence_get_page", {"pageId": page_id})
            confluence.append(
                {"page_id": page_id, "ok": True, "text": extract_mcp_text(result)[:4000]}
            )
        except Exception as exc:  # noqa: BLE001
            confluence.append({"page_id": page_id, "ok": False, "error": str(exc)[:300]})
    jenkins = []
    for url in extract_jenkins_build_urls(text_parts):
        try:
            result = await call_tool("jenkins_get_build_log", {"url": url})
            jenkins.append({"url": url, "ok": True, "text": extract_mcp_text(result)[:6000]})
        except Exception as exc:  # noqa: BLE001
            jenkins.append({"url": url, "ok": False, "error": str(exc)[:300]})
    return {
        "jira": jira,
        "confluence": confluence,
        "ci": ci,
        "jenkins": jenkins,
        "gitlab": [],
    }


def _empty_external_context() -> dict[str, Any]:
    return {"jira": [], "confluence": [], "ci": [], "jenkins": [], "gitlab": []}
