"""
Unit tests for the Redis-backed beat-task overlap guard (app/core/task_locks.py).

Covers:
- First acquire succeeds
- Second acquire while the first is held fails (overlap prevented)
- Lock can be re-acquired after explicit release
- Redis unavailable degrades open (acquire returns True)
"""

from unittest.mock import patch

from app.core.task_locks import acquire_run_lock, release_run_lock, _get_redis_client


def _cleanup(task_name: str):
    r = _get_redis_client()
    if r is not None:
        r.delete(f"task_lock:{task_name}")


def test_first_acquire_succeeds():
    task_name = "test_lock_first_acquire"
    _cleanup(task_name)
    try:
        assert acquire_run_lock(task_name, ttl_seconds=60) is True
    finally:
        _cleanup(task_name)


def test_second_acquire_while_held_fails():
    task_name = "test_lock_overlap"
    _cleanup(task_name)
    try:
        assert acquire_run_lock(task_name, ttl_seconds=60) is True
        # A second beat firing while the first run is still "in progress"
        # (lock not yet released) must not be allowed to proceed.
        assert acquire_run_lock(task_name, ttl_seconds=60) is False
    finally:
        _cleanup(task_name)


def test_acquire_after_release_succeeds():
    task_name = "test_lock_release"
    _cleanup(task_name)
    try:
        assert acquire_run_lock(task_name, ttl_seconds=60) is True
        release_run_lock(task_name)
        assert acquire_run_lock(task_name, ttl_seconds=60) is True
    finally:
        _cleanup(task_name)


def test_degrades_open_when_redis_unavailable():
    """A lock check that can't reach Redis should not block the task it
    guards — consistent with the LLM budget checker's fail-open behavior."""
    with patch("app.core.task_locks._get_redis_client", return_value=None):
        assert acquire_run_lock("test_lock_no_redis", ttl_seconds=60) is True
        # release is a no-op when Redis is unreachable — must not raise.
        release_run_lock("test_lock_no_redis")
