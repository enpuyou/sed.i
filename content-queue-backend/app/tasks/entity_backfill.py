"""
Entity backfill tasks.

Two periodic beat jobs:

1. embed_new_entities_beat_task  (hourly)
   Catches entity nodes that missed their embedding window — e.g. when
   embed_new_entities_task.delay() fired but the broker was down. Calls
   embed_new_entities() directly for every user; safe because it is
   idempotent (skips entities that already have vectors).

2. backfill_missing_entities_task  (daily)
   Queues analyze_article_task for completed articles that have never had
   entity analysis run (entities_analyzed_at IS NULL). Throttled to
   _BACKFILL_BATCH per run to avoid overwhelming the LLM API.

   Uses entities_analyzed_at rather than entity_mentions so articles that
   were analyzed but produced zero entities are not re-queued repeatedly.

Dispatch:
    embed_new_entities_beat_task.delay()
    backfill_missing_entities_task.delay()
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.core.celery_app import celery_app
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

_BACKFILL_BATCH = 50  # articles queued per daily run


@celery_app.task(
    base=DatabaseTask,
    bind=True,
    name="app.tasks.entity_backfill.embed_new_entities_beat_task",
)
def embed_new_entities_beat_task(self):
    """
    Hourly sweep: embed entity nodes missing vectors across all users.

    Calls embed_new_entities() directly (the underlying function, not the
    task) to avoid blocking on .get() inside a worker. Idempotent — each
    call skips users whose entities are already fully embedded. Guarded
    by a Redis lock (see app.core.task_locks) so a still-running previous
    hour's sweep can't overlap with this one. All work runs synchronously
    in this call, so the lock is released as soon as it finishes.
    """
    from app.core.task_locks import acquire_run_lock, release_run_lock
    from app.tasks.entity_embedding import embed_new_entities
    from app.models.user import User

    lock_name = "embed_new_entities_beat_task"
    if not acquire_run_lock(lock_name, ttl_seconds=60 * 50):
        logger.info(f"{lock_name}: previous run still in progress, skipping")
        return {"status": "skipped", "reason": "lock_held"}

    try:
        user_ids = [str(u.id) for u in self.db.query(User.id).all()]

        total = 0
        for uid in user_ids:
            result = embed_new_entities(uid, db=self.db)
            total += result.get("embedded", 0)

        logger.info(
            f"embed_new_entities_beat: embedded {total} entities across {len(user_ids)} users"
        )
        return {"status": "completed", "total_embedded": total}
    finally:
        release_run_lock(lock_name)


@celery_app.task(
    base=DatabaseTask,
    bind=True,
    name="app.tasks.entity_backfill.backfill_missing_entities_task",
)
def backfill_missing_entities_task(self):
    """
    Daily sweep: queue analyze_article_task for articles never entity-analyzed.

    Uses entities_analyzed_at IS NULL to find articles that pre-date the
    entity system. Throttled to _BACKFILL_BATCH per run. Articles that were
    analyzed and produced zero entities have entities_analyzed_at set, so
    they are correctly excluded. Guarded by a Redis lock (see
    app.core.task_locks) so a still-running previous day's sweep can't
    overlap with this one. The lock guards this task's own work (querying
    and queueing) — it's released once queueing finishes, not held until the
    dispatched analyze_article_task runs complete, since this task doesn't
    track those.
    """
    from app.core.task_locks import acquire_run_lock, release_run_lock

    lock_name = "backfill_missing_entities_task"
    if not acquire_run_lock(lock_name, ttl_seconds=60 * 60 * 20):
        logger.info(f"{lock_name}: previous run still in progress, skipping")
        return {"status": "skipped", "reason": "lock_held"}

    try:
        rows = self.db.execute(
            text(
                """
                SELECT id
                FROM content_items
                WHERE processing_status = 'completed'
                  AND full_text IS NOT NULL
                  AND entities_analyzed_at IS NULL
                ORDER BY created_at ASC
                LIMIT :batch
                """
            ),
            {"batch": _BACKFILL_BATCH},
        ).fetchall()

        if not rows:
            logger.debug("backfill_missing_entities: nothing to backfill")
            return {"status": "nothing_to_backfill", "queued": 0}

        from app.tasks.article_analysis import analyze_article_task

        queued = 0
        for row in rows:
            try:
                analyze_article_task.delay(str(row.id))
                queued += 1
            except Exception as e:
                logger.warning(f"backfill: could not queue {row.id}: {e}")

        logger.info(f"backfill_missing_entities: queued {queued} articles")
        return {"status": "completed", "queued": queued}
    finally:
        release_run_lock(lock_name)
