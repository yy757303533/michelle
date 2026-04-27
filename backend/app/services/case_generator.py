"""Turn a PRD chapter into validated TestCase rows via the LLM gateway.

Pipeline:
  Chapter
    → render(case_gen_v1, ...)
    → gateway.chat(json_mode=True, prefer=claude-cli, fallback minimax/flywheel)
    → strip fences / parse JSON
    → Pydantic validate against CaseGenSchema
    → mint case_ids (TC-YYYYMMDD-NNN) → insert TestCases
    → emit `case.generated` events

Failure modes handled:
  - LLM returns non-JSON → catch + retry once with a stricter prompt; if still
    bad, surface as ResponseFormatError (do NOT silently swallow)
  - LLM returns valid JSON but cases missing required fields → log + skip those
    cases (the rest are still saved)
  - Empty `cases` array (PRD chapter is too thin) → returned, not raised; the
    `coverage_notes` field tells the user why
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.llm import LLMGateway, get_gateway, prompt_id, render
from app.models.case import TestCase
from app.obs import EVENTS, get_logger
from app.services.prd_parser import Chapter

_log = get_logger(__name__)

# ── Pydantic schema for what we expect back from the LLM ──


class GeneratedStep(BaseModel):
    intent: str
    expected: str = ""


class GeneratedAssertion(BaseModel):
    description: str


class GeneratedCase(BaseModel):
    name: str
    intent: str
    module: str = ""
    tags: list[str] = Field(default_factory=list)
    priority: str = "P1"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[GeneratedStep] = Field(default_factory=list)
    assertions: list[GeneratedAssertion] = Field(default_factory=list)


class GeneratedBatch(BaseModel):
    coverage_notes: str = ""
    cases: list[GeneratedCase] = Field(default_factory=list)


# ── Public API ──


async def generate_cases_for_chapter(
    *,
    project_id: str,
    project_name: str,
    base_url: str,
    chapter: Chapter,
    session: AsyncSession,
    max_cases: int = 8,
    gateway: LLMGateway | None = None,
    prefer_provider: str | None = None,
) -> tuple[list[TestCase], GeneratedBatch]:
    """Generate cases for one chapter; persist them; return (saved_cases, raw_batch).

    The raw_batch is also returned so the caller can show coverage_notes.
    """
    log = _log.bind(project_id=project_id, chapter=chapter.normalized_title)
    gw = gateway or get_gateway()

    pv_id = prompt_id("case_gen", "v1")
    prompt = render(
        "case_gen",
        "v1",
        project_name=project_name,
        base_url=base_url or "(unknown)",
        chapter_id=f"{chapter.level}:{chapter.normalized_title}",
        chapter_text=chapter.body[:6000],  # cap to keep prompt cheap
        max_cases=max_cases,
        module_hint=chapter.title[:60],
    )

    log.info("case.generation.start", prompt_version=pv_id, max_cases=max_cases)

    text, model = await _call_with_one_retry(gw, prompt, pv_id, log, prefer_provider)

    raw = _parse_batch(text, log)
    saved: list[TestCase] = []
    batch_id_seq = await _next_seq(session, project_id)

    for i, gc in enumerate(raw.cases):
        if not gc.steps:
            log.warning("case.generation.skip_empty_steps", case_name=gc.name)
            continue
        seq = batch_id_seq + i
        case_id = _mint_case_id(seq)
        tc = TestCase(
            case_id=case_id,
            project_id=project_id,
            name=gc.name[:200],
            intent=gc.intent[:1000],
            module=gc.module or chapter.title[:60],
            tags=gc.tags,
            priority=gc.priority if gc.priority in {"P0", "P1", "P2"} else "P1",
            preconditions=gc.preconditions,
            steps=[s.model_dump() for s in gc.steps],
            assertions=[a.model_dump() for a in gc.assertions],
            source="ai-generated",
            prompt_version=pv_id,
            model_version=model or "unknown",
            generated_from=f"chapter:{chapter.normalized_title}#{chapter.position}",
            review_status="pending",
            version=1,
        )
        session.add(tc)
        saved.append(tc)
        log.info(
            EVENTS.CASE_GENERATED.name,
            case_id=case_id,
            prompt_version=pv_id,
            model=model,
            from_chapter=chapter.normalized_title,
        )

    await session.commit()
    return saved, raw


async def _call_with_one_retry(
    gw: LLMGateway, prompt: str, prompt_version: str, log, prefer: str | None
) -> tuple[str, str]:
    """Call LLM, retry once with a tighter system prompt if first JSON parse fails."""
    res = await gw.chat(
        prompt,
        prompt_version=prompt_version,
        prefer=prefer,
        json_mode=True,
        max_tokens=4000,
        timeout_seconds=180,
    )
    if _looks_like_json(res.text):
        return res.text, res.model

    log.warning("case.generation.retry", reason="non-JSON response on first attempt")
    res2 = await gw.chat(
        prompt,
        prompt_version=prompt_version,
        prefer=prefer,
        system="Output STRICT JSON only. No prose, no markdown fences. Begin with '{' and end with '}'.",
        json_mode=True,
        max_tokens=4000,
        timeout_seconds=180,
    )
    return res2.text, res2.model


def _looks_like_json(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    if s.startswith("```"):
        # likely fenced; we strip in parse step. For "looks like" purposes accept.
        return True
    return s.startswith("{")


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"\s*```\s*$")


def _strip_fences(text: str) -> str:
    s = text.strip()
    s = _FENCE_RE.sub("", s)
    s = _FENCE_END_RE.sub("", s)
    return s.strip()


def _parse_batch(text: str, log) -> GeneratedBatch:
    cleaned = _strip_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("case.generation.unparseable", text_preview=cleaned[:300])
        raise ValueError(f"LLM returned non-JSON: {exc}") from exc

    try:
        return GeneratedBatch.model_validate(data)
    except ValidationError as exc:
        # Try to salvage individual cases
        log.warning("case.generation.partial_validation_failure", detail=str(exc)[:300])
        cases_raw = data.get("cases", []) if isinstance(data, dict) else []
        salvaged: list[GeneratedCase] = []
        for c in cases_raw:
            try:
                salvaged.append(GeneratedCase.model_validate(c))
            except ValidationError:
                continue
        return GeneratedBatch(
            coverage_notes=(data.get("coverage_notes") if isinstance(data, dict) else "") or "",
            cases=salvaged,
        )


def _mint_case_id(seq: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"TC-{today}-{seq:04d}"


async def _next_seq(session: AsyncSession, project_id: str) -> int:
    """Find the next case sequence for today + project."""
    today_prefix = f"TC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
    rows = await session.execute(
        select(TestCase.case_id).where(
            TestCase.project_id == project_id,
            TestCase.case_id.like(f"{today_prefix}%"),
        )
    )
    ids = [r[0] for r in rows.all()]
    if not ids:
        return 1
    max_seq = 0
    for cid in ids:
        try:
            n = int(cid.rsplit("-", 1)[1])
            max_seq = max(max_seq, n)
        except ValueError:
            continue
    return max_seq + 1
