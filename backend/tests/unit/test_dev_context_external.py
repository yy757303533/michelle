from __future__ import annotations

import pytest

from app.services.dev_context.external import (
    collect_external_context,
    extract_jenkins_build_urls,
    extract_jira_keys,
)


def test_extract_jira_keys_deduplicates() -> None:
    assert extract_jira_keys(["Fix ZSTAC-82099, see zstac-82099 and QA-12"]) == [
        "ZSTAC-82099",
        "QA-12",
    ]


@pytest.mark.asyncio
async def test_collect_external_context_fetches_jira_with_injected_tool() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": '{"key":"ZSTAC-82099"}'}]}

    evidence = await collect_external_context(
        text_parts=["Failure from ZSTAC-82099"],
        call_tool=call_tool,
    )

    assert evidence["jira"][0]["key"] == "ZSTAC-82099"
    assert calls == [("jira_get_issue", {"issueKey": "ZSTAC-82099"})]


@pytest.mark.asyncio
async def test_collect_external_context_fetches_confluence_page_with_injected_tool() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "PRD page"}]}

    evidence = await collect_external_context(
        text_parts=["See https://wiki.example/pages/viewpage.action?pageId=12345"],
        call_tool=call_tool,
    )

    assert evidence["confluence"][0]["page_id"] == "12345"
    assert calls == [("confluence_get_page", {"pageId": "12345"})]


def test_extract_jenkins_build_urls() -> None:
    urls = extract_jenkins_build_urls(
        ["See http://jenkins.example/job/zstack/job/nightly/123/console"]
    )

    assert urls == ["http://jenkins.example/job/zstack/job/nightly/123"]


@pytest.mark.asyncio
async def test_collect_external_context_fetches_jenkins_build_with_injected_tool() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "Jenkins log"}]}

    evidence = await collect_external_context(
        text_parts=["http://jenkins.example/job/zstack/42/console"],
        call_tool=call_tool,
    )

    assert evidence["jenkins"][0]["url"] == "http://jenkins.example/job/zstack/42"
    assert calls == [("jenkins_get_build_log", {"url": "http://jenkins.example/job/zstack/42"})]
