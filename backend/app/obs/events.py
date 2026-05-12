"""Event Catalog — canonical event names for AI-consumable logging.

Naming: <domain>.<entity>.<action>

When emitting, always:
  log.info(EVENTS.<NAME>, **fields)

so all event names live in one place and are searchable. The catalog also
acts as documentation that humans (and future AI diagnosers) can read.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    name: str
    when: str
    key_fields: tuple[str, ...]


class _Events:
    # ── PRD ingest ──
    PRD_UPLOADED = Event(
        "prd.uploaded",
        "user uploads PRD",
        ("prd_id", "chapter_count", "hash"),
    )
    PRD_CHAPTER_DIFF = Event(
        "prd.chapter.diff",
        "second upload, diff vs prior",
        ("prd_id", "changed_chapters"),
    )

    # ── LLM ──
    LLM_COMPLETION = Event(
        "llm.completion",
        "any LLM call returned",
        ("provider", "model", "input_tokens", "output_tokens", "latency_ms"),
    )
    LLM_FALLBACK = Event(
        "llm.fallback",
        "primary failed, fell back",
        ("from_provider", "to_provider", "reason"),
    )
    LLM_FAILED = Event(
        "llm.failed",
        "all providers exhausted",
        ("error",),
    )

    # ── Case drafting ──
    CASE_DRAFTED = Event(
        "case.drafted",
        "accepted coverage produced a draft case",
        ("case_id", "prompt_version", "model", "coverage_id"),
    )

    # ── Review ──
    REVIEW_CASE_ACTION = Event(
        "review.case.action",
        "human review action on a case",
        ("case_id", "action", "before_state", "after_state"),
    )

    # ── Run / agent execution ──
    RUN_CREATED = Event(
        "run.created",
        "execution requested",
        ("run_id", "case_ids", "env"),
    )
    AGENT_STEP_STARTED = Event(
        "agent.step.started",
        "before each step",
        ("case_id", "step_index", "step_intent"),
    )
    AGENT_STEP_EXECUTED = Event(
        "agent.step.executed",
        "after each step",
        ("case_id", "step_index", "tool_name", "tool_args", "result", "latency_ms"),
    )
    AGENT_ASSERTION_EVALUATED = Event(
        "agent.assertion.evaluated",
        "assertion result",
        ("case_id", "type", "expected", "actual", "passed"),
    )
    RUN_COMPLETED = Event(
        "run.completed",
        "execution finished",
        ("run_id", "status", "duration_ms", "passed", "failed"),
    )

    # ── Diagnosis ──
    DIAGNOSIS_GENERATED = Event(
        "diagnosis.generated",
        "AI produced diagnosis for a failure",
        ("diag_id", "run_id", "category", "confidence"),
    )
    DIAGNOSIS_FEEDBACK = Event(
        "diagnosis.feedback",
        "human feedback on diagnosis",
        ("diag_id", "feedback"),
    )

    # ── Sediment ──
    PATTERN_MATCHED = Event(
        "pattern.matched",
        "an accumulated failure pattern matched a new failure",
        ("pattern_id", "pattern_type"),
    )

    # ── App lifecycle ──
    APP_STARTED = Event(
        "app.started",
        "FastAPI lifespan startup",
        ("version", "env"),
    )
    APP_SHUTDOWN = Event(
        "app.shutdown",
        "FastAPI lifespan shutdown",
        (),
    )


EVENTS = _Events()
