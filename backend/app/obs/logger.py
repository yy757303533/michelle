"""Structlog setup — JSON output, AI-consumable, trace_id propagated."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.types import Processor

from app.config import settings


def _add_trace_id(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Pull current OTel trace_id into the log record (best-effort)."""
    try:
        from opentelemetry.trace import format_trace_id, get_current_span

        span = get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx.trace_id:
                event_dict.setdefault("trace_id", format_trace_id(ctx.trace_id))
    except Exception:
        pass
    return event_dict


def _add_service(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("service", settings.otel_service_name)
    event_dict.setdefault("env", settings.app_env)
    return event_dict


def setup_logging() -> None:
    """Configure structlog + stdlib logging. Call once at startup."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # stdlib root logger → stderr at the chosen level
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service,
        _add_trace_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """Bind values to current async-context (e.g. trace_id, request_id)."""
    bind_contextvars(**kwargs)


def clear_request_context() -> None:
    clear_contextvars()
