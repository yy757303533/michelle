"""Turn a PRD chapter into validated TestCase rows via the LLM gateway.

Pipeline:
  Chapter
    → render(case_gen_v1, ...)
    → gateway.chat(json_mode=True, prefer=claude-cli/codex-cli)
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

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.llm import FallbackableLLMError, LLMGateway, LLMResult, get_gateway, prompt_id, render
from app.models.case import TestCase
from app.obs import EVENTS, get_logger
from app.services.prd_parser import Chapter

AuthState = Literal["logged-in", "logged-out", "wrong-creds", "public"]
VALID_AUTH_STATES: tuple[str, ...] = ("logged-in", "logged-out", "wrong-creds", "public")

_log = get_logger(__name__)
# Protect date-sequence allocation inside a single backend process. Shared or
# multi-replica deployments should replace this with a DB sequence/advisory lock.
CASE_ID_ALLOCATION_LOCK = asyncio.Lock()

_ACTIONABLE_RE = re.compile(
    r"\b("
    r"user|admin|customer|investor|operator|member|visitor|"
    r"click|tap|select|enter|input|type|submit|save|cancel|upload|download|"
    r"search|filter|sort|create|edit|update|delete|view|open|navigate|login|"
    r"logout|register|reset|verify|approve|reject|display|show|hide|redirect|"
    r"should|must|can|able|required|validate|permission|auth"
    r")\b"
    r"|用户|点击|选择|输入|提交|保存|取消|上传|下载|搜索|筛选|排序|创建|编辑|更新|删除|查看|打开|登录|注册|重置|校验|验证|权限|展示|显示|跳转|必须|应该|可以|允许|禁止",
    re.IGNORECASE,
)

_BROWSER_SURFACE_RE = re.compile(
    r"\b("
    r"page|screen|view|form|button|field|input|dropdown|modal|dialog|toast|"
    r"browser|ui|web|dashboard|login|register|profile|cart|checkout|search"
    r")\b"
    r"|页面|界面|表单|按钮|输入框|下拉|弹窗|提示|浏览器|前端|首页|登录|注册|个人资料|购物车|结账|搜索",
    re.IGNORECASE,
)

_INTERNAL_ONLY_RE = re.compile(
    r"\b(api|endpoint|database|schema|table|kafka|queue|cron|cache|service|worker|job)\b"
    r"|接口|数据库|表结构|消息队列|缓存|服务端|后端|定时任务",
    re.IGNORECASE,
)

_NO_BROWSER_SURFACE_RE = re.compile(
    r"\b(no|without)\s+(browser|ui|web|frontend|front-end)\b"
    r"|\b(browser|ui|web|frontend|front-end)\s+(is\s+)?not\s+involved\b"
    r"|无页面|无界面|不涉及(浏览器|页面|界面|前端)",
    re.IGNORECASE,
)

_NON_ACTIONABLE_TITLES = {
    "document information",
    "document info",
    "revision history",
    "version history",
    "change history",
    "table of contents",
    "contents",
    "overview",
    "project status",
    "project milestones",
    "milestones",
    "status",
    "assumptions",
    "risks",
    "glossary",
}

_ALWAYS_NON_ACTIONABLE_TITLES = {
    "document information",
    "document info",
    "revision history",
    "version history",
    "change history",
    "table of contents",
    "contents",
    "project status",
    "project milestones",
    "milestones",
    "glossary",
}

# ── Pydantic schema for what we expect back from the LLM ──


class GeneratedStep(BaseModel):
    intent: str
    expected: str = ""


class GeneratedAssertion(BaseModel):
    description: str
    source: Literal["prd_explicit", "domain_inferred", "exploratory"] = "domain_inferred"
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    rationale: str = ""


class GeneratedCase(BaseModel):
    chapter_id: str | None = None
    name: str
    intent: str
    module: str = ""
    tags: list[str] = Field(default_factory=list)
    priority: str = "P1"
    auth_state: AuthState = "logged-in"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[GeneratedStep] = Field(default_factory=list)
    assertions: list[GeneratedAssertion] = Field(default_factory=list)


class GeneratedBatch(BaseModel):
    coverage_notes: str = ""
    cases: list[GeneratedCase] = Field(default_factory=list)


# ── Public API ──


def _login_context_for_gen(
    default_username: str | None,
    default_password: str | None,
    login_url: str | None = None,
) -> str:
    """Tell the case-gen LLM whether the project has credentials. With them,
    the prompt explicitly asks for inline login steps in protected-feature
    cases; without them, the prompt asks the LLM to skip login automation
    and rely on `preconditions` instead. Either way the LLM stops guessing."""
    if default_username and default_password:
        login_url = (login_url or "").strip()
        login_line = f" Use login URL `{login_url}`." if login_url else ""
        return (
            f"This project has default test credentials configured "
            f"(username: {default_username}).{login_line} When a case targets a feature "
            f"that requires authentication, prepend login steps using these "
            f"credentials so the case is self-contained and runnable."
        )
    return (
        "No default credentials are configured for this project. For cases "
        "that target authenticated features, do NOT invent credentials; "
        "describe the login requirement in `preconditions` and let a human "
        "fill in real credentials before running."
    )


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
    default_username: str | None = None,
    default_password: str | None = None,
    login_url: str | None = None,
    generation_job_id: str | None = None,
    generation_timeout_seconds: int = 180,
) -> tuple[list[TestCase], GeneratedBatch]:
    """Generate cases for one chapter; persist them; return (saved_cases, raw_batch).

    The raw_batch is also returned so the caller can show coverage_notes.
    """
    log = _log.bind(project_id=project_id, chapter=chapter.normalized_title)
    gw = gateway or get_gateway()

    pv_id = prompt_id("case_gen", "v1")
    if not is_actionable_chapter(chapter):
        batch = GeneratedBatch(
            coverage_notes=(
                "Skipped before LLM call: this chapter looks like metadata, "
                "navigation scaffolding, or otherwise has no browser-actionable requirement."
            ),
            cases=[],
        )
        log.info("case.generation.skip_non_actionable", prompt_version=pv_id)
        return [], batch

    target_cases = estimate_target_cases(chapter, max_cases=max_cases)

    prompt = render(
        "case_gen",
        "v1",
        project_name=project_name,
        base_url=base_url or "(unknown)",
        login_context=_login_context_for_gen(default_username, default_password, login_url),
        chapter_id=f"{chapter.level}:{chapter.normalized_title}",
        chapter_text=chapter.body[:6000],  # cap to keep prompt cheap
        max_cases=max_cases,
        target_cases=target_cases,
        module_hint=chapter.title[:60],
    )

    log.info(
        "case.generation.start",
        prompt_version=pv_id,
        max_cases=max_cases,
        target_cases=target_cases,
    )

    model, raw = await _generate_batch_from_prompt(
        gw=gw,
        prompt=prompt,
        prompt_version=pv_id,
        log=log,
        prefer_provider=prefer_provider,
        target_cases=target_cases,
        generation_timeout_seconds=generation_timeout_seconds,
    )
    saved = await _persist_cases(
        session=session,
        project_id=project_id,
        chapter=chapter,
        cases=raw.cases,
        prompt_version=pv_id,
        model=model,
        generation_job_id=generation_job_id,
        log=log,
    )
    return saved, raw


async def generate_cases_for_chapters(
    *,
    project_id: str,
    project_name: str,
    base_url: str,
    chapters: list[Chapter],
    session: AsyncSession,
    max_cases: int = 8,
    gateway: LLMGateway | None = None,
    prefer_provider: str | None = None,
    default_username: str | None = None,
    default_password: str | None = None,
    login_url: str | None = None,
    generation_job_id: str | None = None,
    generation_timeout_seconds: int = 180,
) -> list[tuple[Chapter, list[TestCase], GeneratedBatch]]:
    """Generate cases for adjacent chapters in one LLM call.

    The model assigns each returned case to `chapter_id`. Results still persist
    and report per chapter so the job progress UI does not change shape.
    """
    actionables = [c for c in chapters if is_actionable_chapter(c)]
    if not actionables:
        return []
    if len(actionables) == 1:
        saved, batch = await generate_cases_for_chapter(
            project_id=project_id,
            project_name=project_name,
            base_url=base_url,
            chapter=actionables[0],
            session=session,
            max_cases=max_cases,
            gateway=gateway,
            prefer_provider=prefer_provider,
            default_username=default_username,
            default_password=default_password,
            login_url=login_url,
            generation_job_id=generation_job_id,
            generation_timeout_seconds=generation_timeout_seconds,
        )
        return [(actionables[0], saved, batch)]

    generated = await generate_batches_for_chapters(
        project_name=project_name,
        base_url=base_url,
        chapters=chapters,
        max_cases=max_cases,
        gateway=gateway,
        prefer_provider=prefer_provider,
        default_username=default_username,
        default_password=default_password,
        login_url=login_url,
        generation_timeout_seconds=generation_timeout_seconds,
    )
    out: list[tuple[Chapter, list[TestCase], GeneratedBatch]] = []
    pv_id = prompt_id("case_gen", "v1")
    for chapter, batch, model in generated:
        saved = await _persist_cases(
            session=session,
            project_id=project_id,
            chapter=chapter,
            cases=batch.cases,
            prompt_version=pv_id,
            model=model,
            generation_job_id=generation_job_id,
            log=_log.bind(project_id=project_id, chapter=chapter.normalized_title),
        )
        out.append((chapter, saved, batch))
    return out


async def generate_batches_for_chapters(
    *,
    project_name: str,
    base_url: str,
    chapters: list[Chapter],
    max_cases: int = 8,
    gateway: LLMGateway | None = None,
    prefer_provider: str | None = None,
    default_username: str | None = None,
    default_password: str | None = None,
    login_url: str | None = None,
    generation_timeout_seconds: int = 180,
) -> list[tuple[Chapter, GeneratedBatch, str]]:
    """Generate case batches for chapters without writing TestCase rows."""
    actionables = [c for c in chapters if is_actionable_chapter(c)]
    if not actionables:
        return []
    if len(actionables) == 1:
        chapter = actionables[0]
        gw = gateway or get_gateway()
        pv_id = prompt_id("case_gen", "v1")
        target_cases = estimate_target_cases(chapter, max_cases=max_cases)
        prompt = render(
            "case_gen",
            "v1",
            project_name=project_name,
            base_url=base_url or "(unknown)",
            login_context=_login_context_for_gen(default_username, default_password, login_url),
            chapter_id=f"{chapter.level}:{chapter.normalized_title}",
            chapter_text=chapter.body[:6000],
            max_cases=max_cases,
            target_cases=target_cases,
            module_hint=chapter.title[:60],
        )
        log = _log.bind(project_id="", chapter=chapter.normalized_title)
        log.info(
            "case.generation.start",
            prompt_version=pv_id,
            max_cases=max_cases,
            target_cases=target_cases,
        )
        model, raw = await _generate_batch_from_prompt(
            gw=gw,
            prompt=prompt,
            prompt_version=pv_id,
            log=log,
            prefer_provider=prefer_provider,
            target_cases=target_cases,
            generation_timeout_seconds=generation_timeout_seconds,
        )
        return [(chapter, raw, model)]

    gw = gateway or get_gateway()
    pv_id = prompt_id("case_gen", "v1")
    target_by_id = {
        _chapter_id(c): estimate_target_cases(c, max_cases=max_cases) for c in actionables
    }
    total_target = sum(target_by_id.values())
    chapter_text = "\n\n".join(
        (
            f"### Chapter {_chapter_id(c)}\n"
            f"Title: {c.title}\n"
            f"Target cases: {target_by_id[_chapter_id(c)]}\n"
            f"{c.body[:4500]}"
        )
        for c in actionables
    )
    log = _log.bind(project_id="", chapter="batch")
    prompt = render(
        "case_gen",
        "v1",
        project_name=project_name,
        base_url=base_url or "(unknown)",
        login_context=_login_context_for_gen(default_username, default_password, login_url),
        chapter_id="batch:" + ",".join(_chapter_id(c) for c in actionables),
        chapter_text=chapter_text,
        max_cases=total_target,
        target_cases=total_target,
        module_hint="; ".join(c.title[:40] for c in actionables),
    )
    log.info(
        "case.generation.batch_start",
        prompt_version=pv_id,
        chapters=len(actionables),
        target_cases=total_target,
    )
    model, raw = await _generate_batch_from_prompt(
        gw=gw,
        prompt=prompt,
        prompt_version=pv_id,
        log=log,
        prefer_provider=prefer_provider,
        target_cases=total_target,
        generation_timeout_seconds=generation_timeout_seconds,
    )

    by_id = {_chapter_id(c): c for c in actionables}
    grouped: dict[str, list[GeneratedCase]] = {cid: [] for cid in by_id}
    fallback_id = _chapter_id(actionables[0])
    for case in dedupe_generated_cases(raw.cases):
        cid = case.chapter_id if case.chapter_id in by_id else fallback_id
        if len(grouped[cid]) < target_by_id[cid]:
            grouped[cid].append(case)

    out: list[tuple[Chapter, GeneratedBatch, str]] = []
    for cid, chapter_cases in grouped.items():
        chapter = by_id[cid]
        out.append(
            (
                chapter,
                GeneratedBatch(coverage_notes=raw.coverage_notes, cases=chapter_cases),
                model,
            )
        )
    return out


async def persist_generated_batch(
    *,
    session: AsyncSession,
    project_id: str,
    chapter: Chapter,
    batch: GeneratedBatch,
    model: str,
    generation_job_id: str | None,
) -> list[TestCase]:
    """Persist one generated chapter batch. Worker calls this serially."""
    return await _persist_cases(
        session=session,
        project_id=project_id,
        chapter=chapter,
        cases=batch.cases,
        prompt_version=prompt_id("case_gen", "v1"),
        model=model,
        generation_job_id=generation_job_id,
        log=_log.bind(project_id=project_id, chapter=chapter.normalized_title),
    )


async def _persist_cases(
    *,
    session: AsyncSession,
    project_id: str,
    chapter: Chapter,
    cases: list[GeneratedCase],
    prompt_version: str,
    model: str,
    generation_job_id: str | None,
    log,
) -> list[TestCase]:
    saved: list[TestCase] = []
    async with CASE_ID_ALLOCATION_LOCK:
        batch_id_seq = await _next_seq(session, project_id)

        seq_offset = 0
        for gc in cases:
            if not gc.steps:
                log.warning("case.generation.skip_empty_steps", case_name=gc.name)
                continue
            seq = batch_id_seq + seq_offset
            seq_offset += 1
            case_id = _mint_case_id(seq)
            tc = TestCase(
                case_id=case_id,
                project_id=project_id,
                name=gc.name[:200],
                intent=gc.intent[:1000],
                module=gc.module or chapter.title[:60],
                tags=gc.tags,
                priority=gc.priority if gc.priority in {"P0", "P1", "P2"} else "P1",
                auth_state=gc.auth_state,
                preconditions=gc.preconditions,
                steps=[s.model_dump() for s in gc.steps],
                assertions=[a.model_dump() for a in gc.assertions],
                quality=_quality_review(gc, chapter),
                source="ai-generated",
                prompt_version=prompt_version,
                model_version=model or "unknown",
                generated_from=f"chapter:{chapter.level}:{chapter.normalized_title}",
                generation_job_id=generation_job_id,
                review_status="pending",
                version=1,
            )
            session.add(tc)
            saved.append(tc)
            log.info(
                EVENTS.CASE_GENERATED.name,
                case_id=case_id,
                prompt_version=prompt_version,
                model=model,
                from_chapter=chapter.normalized_title,
            )

        await session.commit()
    return saved


def is_actionable_chapter(chapter: Chapter) -> bool:
    """Cheap guardrail before spending an LLM call.

    Some imported PRDs contain chapters like "Document Information" with only
    version/status/date rows. The prompt asks the model to return zero cases for
    those, but invoking the CLI still costs a full slow completion. This helper
    skips only chapters with no visible UI/action language.
    """
    title = (chapter.normalized_title or chapter.title or "").strip().lower()
    body = (chapter.body or "").strip()
    if title in _ALWAYS_NON_ACTIONABLE_TITLES:
        return False

    text = f"{title}\n{body}"
    if _NO_BROWSER_SURFACE_RE.search(text):
        return False
    if _INTERNAL_ONLY_RE.search(text) and not _BROWSER_SURFACE_RE.search(text):
        return False
    if not _BROWSER_SURFACE_RE.search(text):
        broad_requirement_words = re.search(
            r"\b(should|must|required|can|able)\b|必须|应该|可以", text, re.IGNORECASE
        )
        strong_action_words = re.search(
            r"\b(click|tap|select|enter|input|type|submit|save|cancel|upload|download|"
            r"search|filter|sort|create|edit|update|delete|view|open|navigate|login|"
            r"logout|register|reset|verify|approve|reject|display|show|hide|redirect)\b"
            r"|点击|选择|输入|提交|保存|取消|上传|下载|搜索|筛选|排序|创建|编辑|更新|删除|查看|打开|登录|注册|重置|校验|验证|展示|显示|跳转",
            text,
            re.IGNORECASE,
        )
        if title in _NON_ACTIONABLE_TITLES or (
            broad_requirement_words and not strong_action_words and len(body) < 700
        ):
            return False
    if _ACTIONABLE_RE.search(text):
        return True

    meaningful = [
        line.strip(" -*\t")
        for line in body.splitlines()
        if line.strip() and line.strip() not in {"---", "```"}
    ]
    body_chars = sum(len(line) for line in meaningful)
    metadata_lines = sum(1 for line in meaningful if ":" in line[:40] or "：" in line[:40])
    mostly_metadata = bool(meaningful) and metadata_lines / len(meaningful) >= 0.6

    if title in _NON_ACTIONABLE_TITLES:
        return False
    if body_chars < 40:
        return False
    if mostly_metadata:
        return False
    return True


def estimate_target_cases(chapter: Chapter, *, max_cases: int = 8) -> int:
    """Pick a generation target from chapter density instead of filling a cap."""
    if max_cases <= 0 or not is_actionable_chapter(chapter):
        return 0
    text = f"{chapter.title}\n{chapter.body or ''}"
    action_hits = len(_ACTIONABLE_RE.findall(text))
    browser_hits = len(_BROWSER_SURFACE_RE.findall(text))
    chars = len((chapter.body or "").strip())

    target = 1
    if chars >= 120 or action_hits + browser_hits >= 2:
        target = 2
    if chars >= 500 or action_hits + browser_hits >= 5:
        target = 4
    if chars >= 1200 or action_hits + browser_hits >= 9:
        target = 6
    if re.search(r"权限|安全|登录|认证|auth|permission|security|payment|支付", text, re.I):
        target += 1
    return max(1, min(max_cases, target))


def dedupe_generated_cases(cases: list[GeneratedCase]) -> list[GeneratedCase]:
    """Merge exact intent/assertion duplicates within a generated batch."""
    best: dict[tuple[str, str, str], GeneratedCase] = {}
    for case in cases:
        assertions = " ".join(a.description for a in case.assertions)
        key = (
            _norm_for_dedupe(case.module),
            _norm_for_dedupe(case.intent),
            _norm_for_dedupe(assertions),
        )
        existing = best.get(key)
        if existing is None or _case_strength(case) > _case_strength(existing):
            best[key] = case
    return list(best.values())


def _case_strength(case: GeneratedCase) -> tuple[float, int, int]:
    confidences = [float(a.confidence) for a in case.assertions]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return (avg_conf, len(case.steps), len(case.assertions))


def _norm_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _chapter_id(chapter: Chapter) -> str:
    return f"{chapter.level}:{chapter.normalized_title}"


def _quality_review(case: GeneratedCase, chapter: Chapter) -> dict:
    flags: list[str] = []
    reviewer_notes: list[str] = []
    assertion_sources = [a.source for a in case.assertions]
    assertion_confidences = [float(a.confidence) for a in case.assertions]
    avg_conf = (
        sum(assertion_confidences) / len(assertion_confidences) if assertion_confidences else 0.5
    )

    text = " ".join(
        [
            case.name,
            case.intent,
            " ".join(case.tags),
            " ".join(s.intent + " " + s.expected for s in case.steps),
            " ".join(a.description for a in case.assertions),
        ]
    ).lower()
    chapter_text = (chapter.body or "").lower()

    if any(src != "prd_explicit" for src in assertion_sources):
        flags.append("needs_requirement_confirmation")
        reviewer_notes.append("至少一条断言不是 PRD 明确依据，review 时需要确认产品预期。")
    if any(src == "exploratory" for src in assertion_sources):
        flags.append("exploratory_boundary")
        reviewer_notes.append("包含探索性边界测试，失败后不应直接定性为产品 bug。")
    if _looks_too_specific(text):
        flags.append("assertion_too_specific")
        reviewer_notes.append("断言可能过度绑定具体错误文案或 UI 行为，建议改成结果约束。")
    if _looks_data_dependent(text):
        flags.append("may_depend_on_test_data")
        reviewer_notes.append("用例可能依赖特定项目、余额、状态或账号数据，运行前要确认测试数据。")
    if len(case.steps) < 2:
        flags.append("too_few_steps")
        reviewer_notes.append("步骤过少，agent 可能缺少可执行路径。")
    if not case.assertions:
        flags.append("missing_assertions")
        reviewer_notes.append("缺少可验证断言。")
    if case.auth_state == "logged-in" and not any(
        "登录" in s.intent or "login" in s.intent.lower() for s in case.steps
    ):
        flags.append("auth_setup_unclear")
        reviewer_notes.append("logged-in 用例没有显式登录步骤，确认项目默认登录态是否可用。")

    prd_overlap = _keyword_overlap(text, chapter_text)
    if prd_overlap < 0.08 and "prd_explicit" in assertion_sources:
        flags.append("weak_prd_traceability")
        reviewer_notes.append("断言标记为 PRD 明确依据，但和章节文本关键词重合较低。")

    severity = "low"
    if any(f in flags for f in {"missing_assertions", "too_few_steps", "assertion_too_specific"}):
        severity = "high"
    elif flags:
        severity = "medium"

    score = max(0.0, min(1.0, avg_conf - 0.08 * len(set(flags)) + min(0.12, prd_overlap)))
    return {
        "score": round(score, 2),
        "severity": severity,
        "flags": sorted(set(flags)),
        "assertion_sources": assertion_sources,
        "avg_assertion_confidence": round(avg_conf, 2),
        "prd_keyword_overlap": round(prd_overlap, 2),
        "reviewer_notes": reviewer_notes,
    }


def _looks_too_specific(text: str) -> bool:
    patterns = [
        "明确的校验错误提示文案",
        "错误提示文案",
        "必须显示",
        "should display exact",
        "exact message",
        "toast",
    ]
    return any(p in text for p in patterns)


def _looks_data_dependent(text: str) -> bool:
    patterns = [
        "余额",
        "库存",
        "项目",
        "project",
        "open",
        "fundraising",
        "available",
        "状态",
        "限额",
    ]
    return any(p in text for p in patterns)


def _keyword_overlap(case_text: str, chapter_text: str) -> float:
    words = {w for w in re.findall(r"[\w\u4e00-\u9fff]{2,}", case_text.lower()) if len(w) >= 2}
    if not words:
        return 0.0
    chapter_words = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", chapter_text.lower()))
    return len(words & chapter_words) / max(1, len(words))


async def _call_with_one_retry(
    gw: LLMGateway,
    prompt: str,
    prompt_version: str,
    log,
    prefer: str | None,
    *,
    target_cases: int,
    generation_timeout_seconds: int,
) -> tuple[str, str, GeneratedBatch]:
    """Call LLM, parse the JSON, and retry once with a stricter system prompt
    if real `json.loads` fails (not just a "looks like JSON" pre-check —
    truncated `{"cases": [...` passes that check but blows up at parse time)."""
    call_kwargs = dict(
        prompt_version=prompt_version,
        prefer=prefer,
        fallback=prefer is None,
        json_mode=True,
        max_tokens=max(1800, min(5000, 900 + target_cases * 550)),
        timeout_seconds=max(30, min(1800, generation_timeout_seconds)),
    )
    res = await _chat_with_backoff(gw, prompt, log=log, **call_kwargs)
    try:
        batch = _parse_batch(res.text, log)
        return res.text, res.model, batch
    except ValueError:
        log.warning("case.generation.retry", reason="JSON parse failed on first attempt")

    res2 = await _chat_with_backoff(
        gw,
        prompt,
        log=log,
        system="Output STRICT JSON only. No prose, no markdown fences. Begin with '{' and end with '}'.",
        **call_kwargs,
    )
    batch = _parse_batch(res2.text, log)
    return res2.text, res2.model, batch


async def _generate_batch_from_prompt(
    *,
    gw: LLMGateway,
    prompt: str,
    prompt_version: str,
    log,
    prefer_provider: str | None,
    target_cases: int,
    generation_timeout_seconds: int,
) -> tuple[str, GeneratedBatch]:
    _text, model, raw = await _call_with_one_retry(
        gw,
        prompt,
        prompt_version,
        log,
        prefer_provider,
        target_cases=target_cases,
        generation_timeout_seconds=generation_timeout_seconds,
    )
    raw.cases = dedupe_generated_cases(raw.cases)[:target_cases]
    return model, raw


async def _chat_with_backoff(
    gw: LLMGateway,
    prompt: str,
    *,
    log,
    attempts: int = 4,
    base_delay: float = 2.0,
    **kwargs,
) -> LLMResult:
    last: FallbackableLLMError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await gw.chat(prompt, **kwargs)
        except FallbackableLLMError as exc:
            last = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "case.generation.llm_backoff",
                attempt=attempt,
                delay_seconds=delay,
                error_type=type(exc).__name__,
                detail=str(exc)[:200],
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last


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
    today = datetime.now(UTC).strftime("%Y%m%d")
    return f"TC-{today}-{seq:04d}"


async def _next_seq(session: AsyncSession, project_id: str) -> int:
    """Find the next global case sequence for today.

    `case_id` is the primary key, so the date sequence must be global even
    though callers still pass project_id for API stability.
    """
    _ = project_id
    today_prefix = f"TC-{datetime.now(UTC).strftime('%Y%m%d')}-"
    rows = await session.execute(
        select(TestCase.case_id).where(
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
