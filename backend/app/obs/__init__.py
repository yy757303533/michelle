"""Observability: structured logging + tracing + event catalog.

Three layers:
  Layer 1 — OpenTelemetry traces / metrics / logs (infrastructure)
  Layer 2 — Business semantic events (agent.step.executed, llm.completion, ...)
  Layer 3 — AI diagnosis consumes layers 1+2

This package wires Layer 1+2.
"""

from .events import EVENTS, Event
from .logger import bind_request_context, get_logger, setup_logging
from .tracer import setup_tracing

__all__ = [
    "EVENTS",
    "Event",
    "bind_request_context",
    "get_logger",
    "setup_logging",
    "setup_tracing",
]
