"""OpenTelemetry tracing setup.

If LOGFIRE_TOKEN is set, we configure Logfire (handles OTel under the hood).
If not, we still set up a no-op-but-real OTel tracer so trace_ids are generated
locally and visible in our structured logs (Layer 1 stays consistent).
"""

from __future__ import annotations

from app.config import settings
from app.obs.logger import get_logger

_log = get_logger(__name__)

# Two distinct guards: the tracer provider is one-shot (OTel raises on
# re-set), but FastAPI instrumentation has its own lifecycle. Tracking them
# separately means `setup_tracing()` followed later by `setup_tracing(app)`
# still wires the FastAPI middleware.
_provider_configured = False
_app_instrumented = False


def setup_tracing(app=None) -> None:
    """Initialize tracing. Idempotent across multiple calls and across
    optional FastAPI app injection."""
    global _provider_configured, _app_instrumented
    try:
        if settings.has_logfire:
            import logfire

            if not _provider_configured:
                logfire.configure(
                    token=settings.logfire_token,
                    service_name=settings.otel_service_name,
                    environment=settings.app_env,
                )
                _provider_configured = True
                _log.info("obs.logfire.configured", project=settings.logfire_project)

            if app is not None and not _app_instrumented:
                logfire.instrument_fastapi(app)
                _app_instrumented = True
            return

        # No Logfire token → set up local OTel tracer so trace_ids exist
        if not _provider_configured:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider

            provider = TracerProvider(
                resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
            )
            trace.set_tracer_provider(provider)
            _provider_configured = True
            _log.info("obs.tracing.local_only", reason="no LOGFIRE_TOKEN")

        if app is not None and not _app_instrumented:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor.instrument_app(app)
                _app_instrumented = True
            except Exception as e:
                _log.warning("obs.otel.fastapi_instrument_failed", error=str(e))
    except Exception as e:
        _log.error("obs.tracing.setup_failed", error=str(e))
