from __future__ import annotations

import pytest

from app.services.dev_context.external import collect_external_context, extract_jira_keys


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
