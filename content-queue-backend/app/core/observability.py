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
    import os
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    # pydantic-settings reads .env into its model but doesn't write values back
    # to os.environ. Mirror them first so the OTEL SDK (which reads os.environ
    # directly) sees them — including before Resource.create() is called.
    from app.core.config import settings as _s

    for _k, _v in [
        ("OTEL_EXPORTER_OTLP_ENDPOINT", otlp_endpoint),
        ("OTEL_EXPORTER_OTLP_HEADERS", _s.OTEL_EXPORTER_OTLP_HEADERS),
        ("OTEL_EXPORTER_OTLP_PROTOCOL", _s.OTEL_EXPORTER_OTLP_PROTOCOL),
        ("OTEL_RESOURCE_ATTRIBUTES", _s.OTEL_RESOURCE_ATTRIBUTES),
    ]:
        if _v:
            os.environ[_k] = _v

    # Resource.create() merges the passed dict with OTEL_RESOURCE_ATTRIBUTES
    # from os.environ (now populated above). deployment.environment comes from
    # settings.SEDI_ENV so it doesn't need to be duplicated in OTEL_RESOURCE_ATTRIBUTES.
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            "deployment.environment": _s.SEDI_ENV,
        }
    )
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter()
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


def setup_observability(app) -> None:
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
        setup_sentry(dsn=settings.SENTRY_DSN, environment=settings.SEDI_ENV)
    except Exception as e:
        logger.warning("Sentry setup failed, error tracking disabled: %s", e)


def setup_worker_observability() -> None:
    """
    Bootstrap observability for the Celery worker process.

    Called from the worker_process_init signal in celery_app.py — no FastAPI
    app instance is available here, so FastAPIInstrumentor is skipped.
    SQLAlchemy, Celery spans, and Sentry error capture are all wired up.
    """
    from app.core.config import settings

    try:
        setup_tracing(
            service_name=f"{settings.OTEL_SERVICE_NAME}-worker",
            otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        )
        instrument_sqlalchemy()
        instrument_celery()
        logger.info("OTEL instrumentation active (worker)")
    except Exception as e:
        logger.warning("OTEL setup failed in worker, tracing disabled: %s", e)

    try:
        setup_sentry(dsn=settings.SENTRY_DSN, environment=settings.SEDI_ENV)
    except Exception as e:
        logger.warning("Sentry setup failed in worker: %s", e)
