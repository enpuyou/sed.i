# ruff: noqa: E402,F401
"""
Celery application configuration and worker lifecycle hooks.

Configures task serialization, routing, rate limits, and observability
(OpenTelemetry + Braintrust). Uses worker_ready signal (not worker_process_init)
so hooks fire with --pool=solo in production.
"""

from celery import Celery
from app.core.config import settings

# Create Celery app
celery_app = Celery(
    "content_queue", broker=settings.REDIS_URL, backend=settings.REDIS_URL
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max per task
    task_soft_time_limit=25 * 60,  # Soft limit at 25 minutes
    worker_prefetch_multiplier=1,  # Take one task at a time
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
    beat_schedule={
        # Cleanup old deleted items daily at 3 AM UTC
        "cleanup-old-deleted-items": {
            "task": "app.tasks.cleanup.cleanup_old_deleted_items",
            "schedule": 60 * 60 * 24,  # Every 24 hours
        },
        # Check for missing highlight embeddings every 5 minutes
        "process-missing-embeddings": {
            "task": "app.tasks.embedding.process_all_missing_embeddings",
            "schedule": 300.0,  # Every 5 minutes
        },
        # Cluster user tags into reading themes weekly
        "cluster-reading-themes": {
            "task": "app.tasks.clustering.cluster_all_users_task",
            "schedule": 60 * 60 * 24 * 7,  # Every 7 days
        },
        # Deduplicate near-identical entity nodes weekly (cheap: ~$0.001 per run)
        "deduplicate-entities": {
            "task": "app.tasks.entity_dedup.deduplicate_entities_task",
            "schedule": 60 * 60 * 24 * 7,  # Every 7 days
            "kwargs": {"user_id": None},  # None = all users (handled in task)
        },
        # Embed any entity nodes missing vectors (hourly safety net)
        "embed-new-entities-sweep": {
            "task": "app.tasks.entity_backfill.embed_new_entities_beat_task",
            "schedule": 60 * 60,  # Every hour
        },
        # Queue entity analysis for articles that pre-date the entity system
        "backfill-missing-entity-analysis": {
            "task": "app.tasks.entity_backfill.backfill_missing_entities_task",
            "schedule": 60 * 60 * 24,  # Every 24 hours
        },
        # Nightly memory consolidation: extract structured insights per active user
        "consolidate-memory-nightly": {
            "task": "app.tasks.memory.consolidate_all_users_task",
            "schedule": 60
            * 60
            * 24,  # Every 24 hours at 3 AM UTC (offset via crontab if needed)
        },
        # Recover stalled research runs every 5 minutes
        "recover-orphaned-runs": {
            "task": "app.tasks.research.recover_orphaned_runs_task",
            "schedule": 60 * 5,
        },
    },
)

# # Auto-discover tasks from app/tasks/ directory
# celery_app.autodiscover_tasks(['app.tasks'])

# Import tasks here (explicit import)
# Note: entity_extraction is intentionally excluded — it is superseded by
# article_analysis (combined tags+entities in one LLM call). extract_entities_task
# is dead code; analyze_article_task is the live dispatch path.
from app.tasks import (
    extraction,
    summarization,
    cleanup,
    discogs,
    embedding,
    tagging,
    clustering,
    email,
    chunk_embeddings,
    article_analysis,
    entity_embedding,
    entity_dedup,
    entity_backfill,
    memory,
    research,
    research_memory,
)


# Observability bootstrap.
#
# worker_process_init fires for each forked pool child — but NOT for --pool=solo
# (the production pool), because solo runs everything in the main process with no
# child processes at all.  Use worker_ready instead: it fires once the worker has
# connected to the broker, regardless of pool type.
from celery.signals import worker_ready, worker_shutdown, worker_process_shutdown


@worker_ready.connect
def init_worker_observability(**kwargs):
    from app.core.observability import setup_worker_observability

    setup_worker_observability()


def _flush_observability():
    """Drain all buffered observability data before a process exits."""
    # OTEL BatchSpanProcessor — drain buffered spans
    try:
        from opentelemetry import trace

        trace.get_tracer_provider().shutdown()
    except Exception:
        pass
    # Braintrust async logger
    if settings.BRAINTRUST_API_KEY:
        try:
            import braintrust

            braintrust.flush()
        except Exception:
            pass


# worker_shutdown: main worker process exits (covers --pool=solo).
# worker_process_shutdown: forked pool child exits (covers prefork/gevent/etc).
# Both are registered so we flush regardless of which pool type is in use.
@worker_shutdown.connect
def flush_on_worker_shutdown(**kwargs):
    _flush_observability()


@worker_process_shutdown.connect
def flush_on_process_shutdown(**kwargs):
    _flush_observability()
