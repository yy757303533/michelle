"""Execution policy for Michelle's generic browser runner.

Keep resilience decisions centralized so the runner does not accumulate
one-off checks for every flaky browser or LLM failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolSeverity = Literal["ok", "warning", "fatal"]


@dataclass(frozen=True)
class ToolDecision:
    severity: ToolSeverity
    reason: str = ""
    continue_batch: bool = True
    override_error: bool = False


MAX_LLM_TIMEOUT_RETRIES_PER_RUN = 1


def classify_tool_result(tool_name: str, result_text: str, is_error: bool) -> ToolDecision:
    """Classify tool failures into warnings vs fatal batch blockers."""
    if not is_error:
        return ToolDecision(severity="ok")

    lowered = result_text.lower()
    if tool_name == "browser_take_screenshot" and "page.screenshot" in lowered:
        if "waiting for fonts to load" in lowered:
            return ToolDecision(
                severity="warning",
                reason="screenshot timed out while waiting for web fonts",
                continue_batch=True,
                override_error=True,
            )
        if "timeouterror" in lowered or "timeout" in lowered:
            return ToolDecision(
                severity="warning",
                reason="screenshot timed out",
                continue_batch=True,
                override_error=True,
            )

    return ToolDecision(severity="fatal", reason="tool returned an error", continue_batch=False)
