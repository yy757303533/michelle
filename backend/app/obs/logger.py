"""Structlog setup — JSON output, AI-consumable, trace_id propagated."""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.types import Processor

from app.config import settings
from app.obs.events import Event

_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
)

_DOMAIN_LOG_FILES = {
    "prd_upload": "prd_upload.log",
    "case_generation": "case_generation.log",
    "case_execution": "case_execution.log",
    "diagnosis": "diagnosis.log",
    "settings": "settings.log",
}


def _normalize_event_catalog(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Allow `log.info(EVENTS.X, **fields)` — render the Event dataclass as its
    canonical name string, and surface missing key_fields for debugging.
    Without this processor JSONRenderer raises TypeError on the Event object."""
    event = event_dict.get("event")
    if isinstance(event, Event):
        event_dict["event"] = event.name
        missing = [f for f in event.key_fields if f not in event_dict]
        if missing:
            event_dict["event_missing_fields"] = missing
    return event_dict


def _redact_sensitive_fields(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _redact_value(event_dict)


def _redact_value(value: Any, *, key: str = "") -> Any:
    if key and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _event_domain(event_dict: dict[str, Any]) -> str | None:
    event = str(event_dict.get("event") or "")
    prompt_version = str(event_dict.get("prompt_version") or "")

    if event.startswith("prd.generation.") or event.startswith("case.generation."):
        return "case_generation"
    if event == "case.generated":
        return "case_generation"
    if prompt_version.startswith("case_gen"):
        return "case_generation"

    if event.startswith("prd."):
        return "prd_upload"

    if event.startswith("diagnosis.") or event.startswith("diagnoser."):
        return "diagnosis"
    if prompt_version.startswith("diagnose"):
        return "diagnosis"

    if event.startswith("settings."):
        return "settings"

    if event.startswith(("run.", "orchestrator.", "agent.", "mcp.")):
        return "case_execution"
    if event.startswith("hook.run_failed") or prompt_version.startswith("execute"):
        return "case_execution"

    return None


class _DomainLogFilter(logging.Filter):
    def __init__(self, domain: str):
        super().__init__()
        self.domain = domain

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            payload = json.loads(record.getMessage())
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        return _event_domain(payload) == self.domain


def _add_trace_id(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Pull current OTel trace_id into the log record (best-effort).
    Don't gate on is_recording() — a non-recording span can still carry a valid
    trace context that downstream log aggregators use to stitch records."""
    try:
        from opentelemetry.trace import format_trace_id, get_current_span

        span = get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
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
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(_rotating_handler(log_path))
        handlers.extend(
            _domain_handlers(
                log_dir=log_path.parent,
                domains=_DOMAIN_LOG_FILES,
            )
        )

    # stdlib root logger → stderr + optional rotating file at the chosen level
    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=level,
        force=True,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service,
        _add_trace_id,
        _normalize_event_catalog,
        _redact_sensitive_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """Bind values to current async-context (e.g. trace_id, request_id)."""
    bind_contextvars(**kwargs)


def clear_request_context() -> None:
    clear_contextvars()


def _rotating_handler(path: Path) -> RotatingFileHandler:
    return RotatingFileHandler(
        path,
        maxBytes=max(1024, settings.log_max_bytes),
        backupCount=max(0, settings.log_backup_count),
        encoding="utf-8",
    )


def _domain_handlers(
    *,
    log_dir: Path,
    domains: dict[str, str],
) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []
    for domain, filename in domains.items():
        handler = _rotating_handler(log_dir / filename)
        handler.addFilter(_DomainLogFilter(domain))
        handlers.append(handler)
    return handlers
