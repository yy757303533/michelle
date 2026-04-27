"""Real-network smoke for MiniMax. Burns a few tokens.

Skipped unless MINIMAX_REAL_TEST=1 in env. The conftest clears proxy env vars,
which is what we need to reach api.minimax.chat directly.

Usage:
    MINIMAX_REAL_TEST=1 uv run pytest tests/integration/test_minimax_real.py -v
"""

from __future__ import annotations

import os

import pytest

from app.llm.minimax import MiniMaxClient

# Read MINIMAX_API_KEY directly from env at module import time. Tests'
# conftest.py sets it to "" if not provided, so this skip path activates.
_KEY = os.environ.get("MINIMAX_API_KEY", "") or os.environ.get(
    "MINIMAX_API_KEY_FOR_TESTS", ""
)
_FORCE = os.environ.get("MINIMAX_REAL_TEST") == "1"

skip_if_no_key = pytest.mark.skipif(
    not _KEY or not _FORCE,
    reason="set MINIMAX_REAL_TEST=1 + MINIMAX_API_KEY to run real-network MiniMax test",
)


@skip_if_no_key
@pytest.mark.asyncio
async def test_real_minimax_text_completion():
    c = MiniMaxClient(api_key=_KEY)
    r = await c.chat(
        "Reply with the single word: ok",
        prompt_version="probe_real_v1",
        max_tokens=10,
    )
    assert r.text  # non-empty
    assert r.input_tokens > 0
    assert r.output_tokens > 0
    assert r.provider == "minimax"
    assert r.model == "MiniMax-Text-01"


@skip_if_no_key
@pytest.mark.asyncio
async def test_real_minimax_reasoning_model():
    """M2.7 is a reasoning model — requires bigger max_tokens to surface a final answer."""
    c = MiniMaxClient(api_key=_KEY, model="MiniMax-M2.7")
    r = await c.chat(
        "Output only the JSON: {\"status\":\"ok\"}",
        prompt_version="probe_real_v1",
        max_tokens=500,  # reasoning model needs room
    )
    assert r.text
    # M2.7 may emit reasoning tokens not counted as content
    assert "ok" in r.text.lower() or r.metadata.get("reasoning_content")
