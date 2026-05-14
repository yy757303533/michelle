from __future__ import annotations

import pytest

from app.models import Diagnosis
from app.services.report_publish import build_diagnosis_comment, publish_diagnosis


def _diag() -> Diagnosis:
    return Diagnosis(
        diag_id="D1",
        run_id="R1",
        case_id="C1",
        diagnoser_prompt_version="v1",
        diagnoser_model="model",
        category="real_bug",
        confidence=0.87,
        reasoning="Backend returned 500.",
        fix_suggestion="Handle missing profile.",
    )


def test_build_diagnosis_comment_contains_core_fields() -> None:
    text = build_diagnosis_comment(_diag())

    assert "Michelle diagnosis" in text
    assert "real_bug" in text
    assert "Backend returned 500." in text
    assert "Handle missing profile." in text


@pytest.mark.asyncio
async def test_publish_diagnosis_to_jira() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, args: dict) -> dict:
        calls.append((name, args))
        return {"content": [{"type": "text", "text": "ok"}]}

    result = await publish_diagnosis(
        diag=_diag(),
        target={"type": "jira", "issue_key": "ZSTAC-1"},
        call_tool=call_tool,
    )

    assert result["ok"] is True
    assert calls[0][0] == "jira_add_comment"
    assert calls[0][1]["issueKey"] == "ZSTAC-1"


@pytest.mark.asyncio
async def test_publish_diagnosis_to_confluence() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, args: dict) -> dict:
        calls.append((name, args))
        return {"content": [{"type": "text", "text": "ok"}]}

    await publish_diagnosis(
        diag=_diag(),
        target={"type": "confluence", "page_id": "123"},
        call_tool=call_tool,
    )

    assert calls[0][0] == "confluence_add_comment"
    assert calls[0][1]["pageId"] == "123"


@pytest.mark.asyncio
async def test_publish_diagnosis_to_gitlab_discussion() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, args: dict) -> dict:
        calls.append((name, args))
        return {"content": [{"type": "text", "text": "ok"}]}

    await publish_diagnosis(
        diag=_diag(),
        target={
            "type": "gitlab_discussion",
            "project": "zstack/main",
            "mr_iid": 42,
            "discussion_id": "abc123",
        },
        call_tool=call_tool,
    )

    assert calls[0][0] == "gl_reply_to_discussion"
    assert calls[0][1]["project"] == "zstack/main"
    assert calls[0][1]["mr_iid"] == 42
    assert calls[0][1]["discussion_id"] == "abc123"
    assert "Michelle diagnosis" in calls[0][1]["body"]
