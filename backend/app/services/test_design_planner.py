"""PRD chapter analysis for the coverage-first product spine.

This first implementation is deliberately conservative and deterministic. It
creates one requirement and one coverage item per selected actionable chapter so
the new domain objects and review flow are usable before the LLM prompt is
introduced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import get_gateway
from app.llm.prompts.registry import render
from app.models import PRD, CoverageItem, RequirementItem
from app.services.prd_parser import Chapter


@dataclass(frozen=True)
class DesignAnalysisResult:
    requirements: list[RequirementItem]
    coverage: list[CoverageItem]


async def analyze_prd_chapters(
    *,
    session: AsyncSession,
    prd: PRD,
    chapter_indices: list[int] | None = None,
    prefer_provider: str | None = None,
    output_language: str = "auto",
) -> DesignAnalysisResult:
    chapters = [Chapter(**c) for c in prd.chapters]
    if chapter_indices is None:
        chapter_indices = list(range(len(chapters)))
    indices = sorted({i for i in chapter_indices if 0 <= i < len(chapters)})

    requirements: list[RequirementItem] = []
    coverage_items: list[CoverageItem] = []
    for index in indices:
        chapter = chapters[index]
        language = _resolve_output_language(output_language, chapter)
        if prefer_provider:
            chapter_requirements, chapter_coverage = await _llm_design_from_chapter(
                prd=prd,
                chapter=chapter,
                chapter_index=index,
                prefer_provider=prefer_provider,
                output_language=language,
            )
        else:
            requirement = _requirement_from_chapter(
                prd=prd,
                chapter=chapter,
                chapter_index=index,
                output_language=language,
            )
            coverage = _coverage_from_requirement(
                prd=prd,
                chapter=chapter,
                chapter_index=index,
                requirement=requirement,
                output_language=language,
            )
            chapter_requirements = [requirement]
            chapter_coverage = [coverage]
        for requirement in chapter_requirements:
            session.add(requirement)
        for coverage in chapter_coverage:
            session.add(coverage)
        requirements.extend(chapter_requirements)
        coverage_items.extend(chapter_coverage)

    await session.commit()
    return DesignAnalysisResult(requirements=requirements, coverage=coverage_items)


async def _llm_design_from_chapter(
    *,
    prd: PRD,
    chapter: Chapter,
    chapter_index: int,
    prefer_provider: str,
    output_language: str,
) -> tuple[list[RequirementItem], list[CoverageItem]]:
    prompt = render(
        "test_design",
        "v1",
        prd_name=prd.name,
        chapter_title=chapter.title,
        chapter_body=chapter.body,
        output_language_name=_prompt_language_name(output_language),
    )
    result = await get_gateway().chat(
        prompt,
        prompt_version="test_design_v1",
        prefer=prefer_provider,
        json_mode=True,
        temperature=0,
        timeout_seconds=120,
    )
    data = json.loads(result.text)
    requirements: list[RequirementItem] = []
    coverage_items: list[CoverageItem] = []
    for raw_req in data.get("requirements") or []:
        if not isinstance(raw_req, dict):
            continue
        text = str(raw_req.get("text") or "").strip()
        if not text:
            continue
        requirement = RequirementItem(
            requirement_id="req_" + uuid4().hex[:12],
            project_id=prd.project_id,
            prd_id=prd.prd_id,
            chapter_index=chapter_index,
            chapter_hash=chapter.hash,
            text=text[:1000],
            type=str(raw_req.get("type") or _infer_requirement_type(text))[:60],
            evidence=str(raw_req.get("evidence") or chapter.body or chapter.title)[:1000],
            confidence=float(raw_req.get("confidence") or 0.7),
        )
        requirements.append(requirement)
        for raw_cov in raw_req.get("coverage") or []:
            if not isinstance(raw_cov, dict):
                continue
            scenario = str(raw_cov.get("scenario") or text).strip()
            coverage_items.append(
                CoverageItem(
                    coverage_id="cov_" + uuid4().hex[:12],
                    project_id=prd.project_id,
                    prd_id=prd.prd_id,
                    requirement_id=requirement.requirement_id,
                    chapter_index=chapter_index,
                    risk_type=str(raw_cov.get("risk_type") or _infer_risk_type(text))[:60],
                    coverage_type=str(raw_cov.get("coverage_type") or "happy")[:60],
                    title=str(raw_cov.get("title") or _title_fragment(text))[:200],
                    scenario=scenario[:1000],
                    rationale=str(raw_cov.get("rationale") or "Generated by test design LLM.")[
                        :1000
                    ],
                    priority=str(raw_cov.get("priority") or "P1")[:20],
                    review_status="proposed",
                )
            )
    if requirements and coverage_items:
        return requirements, coverage_items
    requirement = _requirement_from_chapter(
        prd=prd,
        chapter=chapter,
        chapter_index=chapter_index,
        output_language=output_language,
    )
    return [requirement], [
        _coverage_from_requirement(
            prd=prd,
            chapter=chapter,
            chapter_index=chapter_index,
            requirement=requirement,
            output_language=output_language,
        )
    ]


def _requirement_from_chapter(
    *,
    prd: PRD,
    chapter: Chapter,
    chapter_index: int,
    output_language: str = "en",
) -> RequirementItem:
    body = _compact_text(chapter.body)
    text = body or chapter.title
    if output_language == "zh":
        text = f"{_zh_title(chapter.title)}：{text}"
    return RequirementItem(
        requirement_id="req_" + uuid4().hex[:12],
        project_id=prd.project_id,
        prd_id=prd.prd_id,
        chapter_index=chapter_index,
        chapter_hash=chapter.hash,
        text=text[:1000],
        type=_infer_requirement_type(text),
        evidence=(body or chapter.title)[:1000],
        confidence=0.6,
    )


def _coverage_from_requirement(
    *,
    prd: PRD,
    chapter: Chapter,
    chapter_index: int,
    requirement: RequirementItem,
    output_language: str = "en",
) -> CoverageItem:
    risk_type = _infer_risk_type(requirement.text)
    coverage_type = _coverage_type_for_risk(risk_type)
    if output_language == "zh":
        title = f"{_zh_title(chapter.title)}：{_title_fragment(requirement.text)}"
        scenario = f"请验证：{requirement.text}"
        rationale = "基于 PRD 证据生成，供评审确认。"
    else:
        title = f"{chapter.title}: {_title_fragment(requirement.text)}"
        scenario = requirement.text
        rationale = "Generated from PRD evidence for reviewer confirmation."
    return CoverageItem(
        coverage_id="cov_" + uuid4().hex[:12],
        project_id=prd.project_id,
        prd_id=prd.prd_id,
        requirement_id=requirement.requirement_id,
        chapter_index=chapter_index,
        risk_type=risk_type,
        coverage_type=coverage_type,
        title=title[:200],
        scenario=scenario[:1000],
        rationale=rationale,
        priority="P0" if risk_type in {"permission", "data"} else "P1",
        review_status="proposed",
    )


def _compact_text(text: str) -> str:
    lines = [line.strip(" -*\t") for line in text.splitlines() if line.strip()]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _title_fragment(text: str) -> str:
    first_sentence = re.split(r"[。.!?]", text, maxsplit=1)[0].strip()
    return first_sentence[:80] or "core behavior"


def _infer_requirement_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"权限|permission|role|access", lowered):
        return "permission"
    if re.search(r"数据|data|record|state|status", lowered):
        return "data"
    if re.search(r"必须|不得|should|must|required|rule", lowered):
        return "rule"
    return "behavior"


def _infer_risk_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"权限|permission|role|access|unauthorized", lowered):
        return "permission"
    if re.search(r"密码|账号|login|auth|credential|session", lowered):
        return "validation"
    if re.search(r"数据|data|record|state|status", lowered):
        return "data"
    return "business"


def _coverage_type_for_risk(risk_type: str) -> str:
    if risk_type == "permission":
        return "permission"
    if risk_type == "data":
        return "data"
    if risk_type == "validation":
        return "happy"
    return "happy"


def _resolve_output_language(requested: str, chapter: Chapter) -> str:
    if requested in {"zh", "en"}:
        return requested
    return "zh" if _contains_cjk(f"{chapter.title}\n{chapter.body}") else "en"


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _prompt_language_name(language: str) -> str:
    if language == "zh":
        return "Chinese"
    if language == "en":
        return "English"
    return "the PRD chapter's primary language"


def _zh_title(title: str) -> str:
    translations = {
        "project overview": "项目概览",
        "table of contents": "目录",
        "document information": "文档信息",
        "user registration and sign in": "用户注册与登录",
        "kyc verification": "KYC 认证",
        "project management": "项目管理",
        "investment flow": "投资流程",
        "backoffice management": "后台管理",
        "finance and cashflow": "财务与现金流",
        "static pages": "静态页面",
        "q&a and feedback": "问答与反馈",
        "algorithm rules": "算法规则",
    }
    cleaned = re.sub(r"^\d+\.?\s*", "", title).strip()
    return translations.get(cleaned.lower(), cleaned or "章节")
