"""
Redis-backed overlap guard for beat-triggered fan-out tasks.

Tasks like cluster_all_users_task and consolidate_all_users_task dispatch
one Celery task per user from a single beat trigger. If a previous run's
fan-out is still draining (per-user tasks still executing) when the next
beat fires, both runs proceed concurrently with no coordination — invisible
at current scale, but a source of duplicate work and confusing debugging
once fan-out volume or per-user task duration grows.

acquire_run_lock uses SET NX EX (atomic, one round-trip) so only one beat
firing at a time can hold a given task's lock. Degrades open (allows the
run) if Redis is unreachable — consistent with the LLM budget checker
(app/core/llm_client.py) and the rate limiter (app/middleware/rate_limit.py):
Redis is already a hard dependency for the Celery broker, so an outage
already stops the pipeline elsewhere.
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_redis_client():
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception as e:
        logger.warning(f"Redis unavailable for task lock ({e}), degrading open")
        return None


def acquire_run_lock(task_name: str, *, ttl_seconds: int) -> bool:
    """
    Try to acquire the overlap-guard lock for task_name.

    Returns True if the lock was acquired (caller should proceed) or if
    Redis is unreachable (fail open). Returns False if another run already
    holds the lock (caller should skip this firing and log it).

    ttl_seconds should be set comfortably above the task's expected p99
    runtime — it's a safety net against a crashed run leaving the lock
    held forever, not a precise timeout.
    """
    r = _get_redis_client()
    if r is None:
        return True

    key = f"task_lock:{task_name}"
    acquired = r.set(key, "1", nx=True, ex=ttl_seconds)
    return bool(acquired)


def release_run_lock(task_name: str) -> None:
    """Release the lock early on successful completion, so the next beat
    firing isn't blocked until the TTL expires. Best-effort — if this fails
    (or Redis is unreachable), the TTL is the fallback release mechanism.

    Only call this when the task's own execution covers all the work the
    lock guards (e.g. a synchronous per-user loop). A task that dispatches
    async fan-out work via .delay() and returns before that work completes
    (see cluster_all_users_task, consolidate_all_users_task) must NOT
    release early — the lock is deliberately held for its full TTL there,
    since this task can't observe when the dispatched work actually finishes."""
    r = _get_redis_client()
    if r is None:
        return
    try:
        r.delete(f"task_lock:{task_name}")
    except Exception as e:
        logger.warning(f"Failed to release task lock for {task_name} ({e})")
