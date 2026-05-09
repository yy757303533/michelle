"""AI failure diagnosis — Day 11.

Given a failed Run, render diagnose_v1 with trace tail + failed step + nearby
screenshots, call the LLM gateway, parse the strict-JSON result into a
Diagnosis row.

The diagnose_v1 prompt has a hard contract:
  {"category": "...", "confidence": 0.0..1.0,
   "reasoning": "<3 sentences>", "fix_suggestion": "<1 sentence>",
   "evidence": ["..."]}

Categories: real_bug | flaky | selector_drift | vision_misjudge |
            env_issue | data_issue | unknown
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.llm import get_gateway, prompt_id, render
from app.llm.base import LLMError
from app.models import Diagnosis, Run, StepEvent, TestCase
from app.obs import EVENTS, get_logger
from app.runtime_config import get_diagnosis_provider

_log = get_logger(__name__)

VALID_CATEGORIES = {
    "real_bug",
    "flaky",
    "selector_drift",
    "vision_misjudge",
    "env_issue",
    "data_issue",
    "unknown",
}


class DiagnoserError(RuntimeError):
    """Raised when diagnosis can't be produced (input incomplete or LLM failed)."""


# ── Public API ─────────────────────────────────────────────────────────────


async def diagnose_run(
    *,
    run_id: str,
    session: AsyncSession,
    prefer_provider: str | None = None,
    overwrite_existing: bool = False,
) -> Diagnosis:
    """Generate a Diagnosis for a finished failed Run.

    Idempotent: if a Diagnosis already exists for this run, returns it unless
    overwrite_existing=True (which creates a new one + leaves old in place).
    """
    log = _log.bind(run_id=run_id)
    run = await session.get(Run, run_id)
    if run is None:
        raise DiagnoserError(f"run {run_id} not found")

    if run.status not in {"failed", "flaky", "aborted"}:
        raise DiagnoserError(
            f"diagnosis is only meaningful on failed/flaky/aborted runs, got {run.status}"
        )

    if not overwrite_existing:
        existing = (
            (
                await session.execute(
                    select(Diagnosis)
                    .where(Diagnosis.run_id == run_id)
                    .order_by(Diagnosis.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            log.info("diagnoser.skip_existing", diag_id=existing.diag_id)
            return existing

    case = await session.get(TestCase, run.case_id)
    steps = (
        (
            await session.execute(
                select(StepEvent).where(StepEvent.run_id == run_id).order_by(StepEvent.step_index)
            )
        )
        .scalars()
        .all()
    )

    failed_step = next((s for s in steps if s.status == "failed"), None) or (
        steps[-1] if steps else None
    )

    prompt = _render_prompt(run=run, case=case, steps=list(steps), failed_step=failed_step)

    # Pick screenshot bytes around the failed step (if any) for vision input.
    image_bytes = _read_screenshot_for_step(run=run, failed_step=failed_step)

    pv_id = prompt_id("diagnose", "v1")
    log.info(
        "diagnoser.start",
        prompt_version=pv_id,
        has_image=bool(image_bytes),
        failed_step_index=getattr(failed_step, "step_index", None),
    )
    gw = get_gateway()

    # Provider routing for diagnosis. Internal rollout currently supports
    # claude-cli and codex-cli only, so image input is downgraded to text if
    # neither CLI can relay screenshots.
    chosen_prefer = prefer_provider or await get_diagnosis_provider(session)
    skip_for_image: list[str] = []
    if chosen_prefer is None:
        for cand in ("claude-cli", "codex-cli"):
            if gw.get(cand) is not None:
                chosen_prefer = cand
                break
    if image_bytes:
        # claude-cli / codex-cli can't relay images in -p mode; skip them.
        skip_for_image = ["claude-cli", "codex-cli"]
        if chosen_prefer in skip_for_image:
            chosen_prefer = None

    try:
        result = await gw.chat(
            prompt,
            prompt_version=pv_id,
            prefer=chosen_prefer,
            skip=skip_for_image or None,
            image=image_bytes,
            json_mode=True,
            max_tokens=600,
            timeout_seconds=120,
        )
    except LLMError as exc:
        # Last-ditch: retry without the image (text-only) so the diagnosis
        # still produces SOMETHING the human can review.
        retry_event = (
            "diagnoser.llm_failed_with_image_retry_text"
            if image_bytes
            else "diagnoser.llm_failed_retry"
        )
        log.warning(retry_event, error=str(exc)[:200])
        try:
            result = await gw.chat(
                prompt,
                prompt_version=pv_id,
                prefer=prefer_provider or await get_diagnosis_provider(session),
                image=None,
                json_mode=True,
                max_tokens=600,
                timeout_seconds=120,
            )
        except LLMError as exc2:
            log.error("diagnoser.llm_failed", error=str(exc2)[:300])
            raise DiagnoserError(f"LLM diagnosis failed: {exc2}") from exc2

    parsed = _parse_diagnosis_json(result.text)

    diag = Diagnosis(
        diag_id="diag_" + uuid4().hex[:12],
        run_id=run_id,
        case_id=run.case_id,
        diagnoser_prompt_version=pv_id,
        diagnoser_model=result.model or result.provider,
        category=parsed["category"],
        confidence=float(parsed.get("confidence") or 0.0),
        reasoning=parsed.get("reasoning", "")[:4000],
        fix_suggestion=parsed.get("fix_suggestion", "")[:2000],
    )
    # Capture log fields before commit() in case the session is configured
    # with expire_on_commit=True (default). Without this, downstream lazy
    # attribute reads can MissingGreenlet.
    diag_id_v, category_v, confidence_v = diag.diag_id, diag.category, diag.confidence
    session.add(diag)
    await session.commit()
    log.info(
        EVENTS.DIAGNOSIS_GENERATED.name,
        diag_id=diag_id_v,
        run_id=run_id,
        category=category_v,
        confidence=confidence_v,
    )
    try:
        from app.services.email_notifications import notify_diagnosis_generated

        await notify_diagnosis_generated(diag_id=diag_id_v, session=session)
    except Exception as exc:  # noqa: BLE001
        log.warning("diagnoser.email_notification_failed", diag_id=diag_id_v, error=str(exc)[:200])
    return diag


async def record_feedback(
    *,
    diag_id: str,
    feedback: str,
    reason: str = "",
    note: str = "",
    session: AsyncSession,
) -> Diagnosis:
    """Persist human feedback. Triggers pattern_store.absorb on the
    transition into `confirmed` (idempotent — repeated POSTs from a flaky
    network or button mash do NOT re-bump pattern hit counts)."""
    if feedback not in {"confirmed", "wrong", "partially_correct"}:
        raise DiagnoserError(f"invalid feedback {feedback!r}")
    diag = await session.get(Diagnosis, diag_id)
    if diag is None:
        raise DiagnoserError(f"diagnosis {diag_id} not found")

    was_confirmed = diag.human_feedback == "confirmed"
    diag.human_feedback = feedback
    reason = reason.strip()
    detail = note.strip()
    if reason:
        detail = f"[reason:{reason}] {detail}".strip()
    diag.feedback_note = detail[:1000]
    diag.feedback_at = datetime.now(UTC)
    await session.commit()

    _log.info(
        EVENTS.DIAGNOSIS_FEEDBACK.name,
        diag_id=diag_id,
        feedback=feedback,
    )

    # Only absorb on the transition into confirmed, never on a repeat.
    if feedback == "confirmed" and not was_confirmed:
        from app.services.pattern_store import absorb_diagnosis

        await absorb_diagnosis(diag=diag, session=session)

    return diag


# ── Helpers ────────────────────────────────────────────────────────────────


def _render_prompt(
    *,
    run: Run,
    case: TestCase | None,
    steps: list[StepEvent],
    failed_step: StepEvent | None,
) -> str:
    case_summary = ""
    if case:
        case_summary = (
            f"name: {case.name}\n"
            f"intent: {case.intent}\n"
            f"module: {case.module}\n"
            f"priority: {case.priority}"
        )

    failed_step_summary = ""
    if failed_step is not None:
        evidence = ""
        if isinstance(failed_step.tool_result, dict):
            evidence = str(failed_step.tool_result.get("evidence") or "")[:500]
        failed_step_summary = (
            f"step_index: {failed_step.step_index}\n"
            f"phase: {getattr(failed_step, 'phase', 'action')}\n"
            f"tool: {failed_step.tool_name}\n"
            f"intent: {failed_step.intent or '(none)'}\n"
            f"status: {failed_step.status}\n"
            f"error: {failed_step.error_message or '(no explicit error)'}\n"
            f"evidence: {evidence or '(none)'}"
        )

    # Tail: last 30 step events as a compact list
    tail_lines: list[str] = []
    for s in steps[-30:]:
        tail_lines.append(
            f"[{s.step_index}] phase={getattr(s, 'phase', 'action')} "
            f"{s.tool_name or '?'} status={s.status}"
            f" url={(s.tool_result or {}).get('page_url') or '-'}"
            f" err={(s.error_message or '-')[:100]}"
        )

    return render(
        "diagnose",
        "v1",
        case_name=getattr(case, "name", run.case_id) or run.case_id,
        case_summary=case_summary or "(case row missing)",
        failed_step_index=getattr(failed_step, "step_index", "?"),
        failed_step_summary=failed_step_summary or "(no failed step recorded)",
        trace_tail_lines=len(tail_lines),
        trace_tail="\n".join(tail_lines) or "(trace empty)",
    )


def _read_screenshot_for_step(*, run: Run, failed_step: StepEvent | None) -> bytes | None:
    """Best-effort: load the most relevant screenshot near the failed step.

    Priority:
      1. failed_step.screenshot_after / screenshot_before
      2. step-<index>.png in the run's artifacts dir
      3. final.png as last resort
    """
    if not run.artifacts_dir:
        return None
    base = Path(run.artifacts_dir)
    if not base.is_dir():
        return None

    candidates: list[Path] = []
    if failed_step:
        for path_attr in ("screenshot_after", "screenshot_before"):
            v = getattr(failed_step, path_attr, None)
            if v:
                p = Path(v)
                if not p.is_absolute():
                    p = base / p
                candidates.append(p)
        idx = failed_step.step_index
        candidates.append(base / f"step-{idx}.png")
        if idx > 0:
            candidates.append(base / f"step-{idx - 1}.png")
        try:
            step_images = sorted(base.rglob("step-*.png"))
            before_or_at = []
            for img in step_images:
                m = re.search(r"step-(\d+)\.png$", img.name)
                if m and int(m.group(1)) <= idx:
                    before_or_at.append((int(m.group(1)), img))
            candidates.extend(img for _, img in sorted(before_or_at, reverse=True)[:3])
        except OSError:
            pass
    candidates.append(base / "final.png")

    for c in candidates:
        try:
            if c.is_file():
                data = c.read_bytes()
                if data:
                    return data
        except OSError:
            continue
    return None


_FENCE_RE = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"\s*```\s*$")


def _parse_diagnosis_json(text: str) -> dict[str, Any]:
    """Coerce LLM output into a valid diagnosis dict. Defaults to 'unknown'
    rather than raising when fields are missing — better to show something."""
    s = (text or "").strip()
    s = _FENCE_RE.sub("", s)
    s = _FENCE_END_RE.sub("", s)

    parsed: dict[str, Any] = {}
    # Models often emit literal newlines inside string values. strict=False
    # tolerates ASCII control chars in strings — friendlier on real LLM output.
    try:
        parsed = json.loads(s, strict=False)
        if not isinstance(parsed, dict):
            parsed = {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(), strict=False)
            except json.JSONDecodeError:
                parsed = {}

    cat = str(parsed.get("category", "")).strip().lower()
    if cat not in VALID_CATEGORIES:
        cat = "unknown"

    return {
        "category": cat,
        "confidence": _safe_float(parsed.get("confidence", 0.0)),
        "reasoning": str(parsed.get("reasoning", "")),
        "fix_suggestion": str(parsed.get("fix_suggestion", "")),
        "evidence": parsed.get("evidence", []),
    }


def _safe_float(v: Any) -> float:
    """Coerce anything model-shaped to a [0,1] confidence. Tolerates `"80%"`,
    `"0.8"`, `0.8`, `80` (>1 → divided by 100 only when it has a `%` suffix;
    bare `80` clamps to 1.0 since the model meant `0.8` poorly)."""
    try:
        if isinstance(v, str):
            s = v.strip()
            if s.endswith("%"):
                return max(0.0, min(1.0, float(s[:-1].strip()) / 100.0))
            v = s
        f = float(v)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return 0.0
