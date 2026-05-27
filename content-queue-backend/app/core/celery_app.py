# ruff: noqa: E402,F401
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
    },
)

# # Auto-discover tasks from app/tasks/ directory
# celery_app.autodiscover_tasks(['app.tasks'])

# Import tasks here (explicit import)
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
)


# Observability bootstrap — must run in each worker process, not the main process.
# FastAPI's lifespan (which calls setup_observability) never runs in the worker.
from celery.signals import worker_process_init, worker_process_shutdown


@worker_process_init.connect
def init_worker_observability(**kwargs):
    from app.core.observability import setup_worker_observability

    setup_worker_observability()


# Flush all observability buffers before the worker process exits.
# worker_max_tasks_per_child kills the process; background threads won't drain.
@worker_process_shutdown.connect
def flush_on_shutdown(**kwargs):
    # OTEL BatchSpanProcessor — drain buffered spans before exit
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
