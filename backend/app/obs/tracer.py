"""OpenTelemetry tracing setup.

If LOGFIRE_TOKEN is set, we configure Logfire (handles OTel under the hood).
If not, we still set up a no-op-but-real OTel tracer so trace_ids are generated
locally and visible in our structured logs (Layer 1 stays consistent).
"""

from __future__ import annotations

from app.config import settings
from app.obs.logger import get_logger

_log = get_logger(__name__)


def setup_tracing(app=None) -> None:
    """Initialize tracing. Idempotent."""
    try:
        if settings.has_logfire:
            import logfire

            logfire.configure(
                token=settings.logfire_token,
                service_name=settings.otel_service_name,
                environment=settings.app_env,
            )
            if app is not None:
                logfire.instrument_fastapi(app)
            _log.info("obs.logfire.configured", project=settings.logfire_project)
            return

        # No Logfire token → set up local OTel tracer so trace_ids exist
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
        )
        trace.set_tracer_provider(provider)

        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor.instrument_app(app)
            except Exception as e:
                _log.warning("obs.otel.fastapi_instrument_failed", error=str(e))

        _log.info("obs.tracing.local_only", reason="no LOGFIRE_TOKEN")
    except Exception as e:
        _log.error("obs.tracing.setup_failed", error=str(e))
