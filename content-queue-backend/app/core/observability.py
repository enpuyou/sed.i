"""
Observability bootstrap for sed.i (Layer 2).

Call setup_observability() once at app startup. All configuration is driven
by environment variables via settings — empty values disable the respective tool.

Tools wired up here:
  - OpenTelemetry: traces every FastAPI request + SQLAlchemy query + Celery task.
    Exports to OTLP endpoint (Grafana Cloud) when OTEL_EXPORTER_OTLP_ENDPOINT is set,
    otherwise falls back to console exporter for local inspection.
  - Sentry: captures exceptions and performance events when SENTRY_DSN is set.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def setup_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Wire up OpenTelemetry SDK with FastAPI + SQLAlchemy + Celery instrumentation."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        logger.info("OTEL: exporting traces to %s", otlp_endpoint)
    else:
        exporter = ConsoleSpanExporter()
        logger.info("OTEL: no OTLP endpoint set — using console exporter")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def instrument_fastapi(app) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine=None) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    else:
        SQLAlchemyInstrumentor().instrument()


def instrument_celery() -> None:
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument()


def setup_sentry(dsn: str, environment: str = "production") -> None:
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            CeleryIntegration(),
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
        # Capture 10% of transactions for performance monitoring (free tier friendly)
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry error tracking enabled (environment=%s)", environment)


def setup_observability(app, *, debug: bool = False) -> None:
    """
    Bootstrap all observability tooling. Call once at lifespan startup.

    Reads configuration from app.core.config.settings. Any tool whose key
    is absent or empty is silently skipped — safe in dev/test.
    """
    from app.core.config import settings

    # OpenTelemetry
    try:
        setup_tracing(
            service_name=settings.OTEL_SERVICE_NAME,
            otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        )
        instrument_fastapi(app)
        instrument_sqlalchemy()
        instrument_celery()
        logger.info("OTEL instrumentation active")
    except Exception as e:
        logger.warning("OTEL setup failed, tracing disabled: %s", e)

    # Sentry
    try:
        environment = "development" if debug else "production"
        setup_sentry(dsn=settings.SENTRY_DSN, environment=environment)
    except Exception as e:
        logger.warning("Sentry setup failed, error tracking disabled: %s", e)
