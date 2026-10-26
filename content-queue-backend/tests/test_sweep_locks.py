"""
The two 5-minute embedding sweeps must run on consecutive beat firings.

Their dispatch is async, so the overlap lock is never released after dispatch
and only expires by TTL. A TTL >= the 300 s schedule interval makes every
second firing skip. Uses a fake Redis with a controllable clock.
"""

from unittest.mock import patch

import pytest

BEAT_INTERVAL = 300


class FakeRedis:
    def __init__(self):
        self.now = 0.0
        self.keys = {}

    def set(self, key, value, nx=False, ex=None):
        if key in self.keys and self.keys[key] > self.now:
            return None
        self.keys[key] = self.now + ex
        return True

    def delete(self, key):
        self.keys.pop(key, None)


@pytest.fixture
def fake_redis():
    r = FakeRedis()
    with patch("app.core.task_locks._get_redis_client", return_value=r):
        yield r


def _run_three_firings(fake_redis, task):
    statuses = []
    for _ in range(3):
        statuses.append(task.run().get("status"))
        fake_redis.now += BEAT_INTERVAL
    return statuses


def test_embeddings_sweep_runs_every_firing(fake_redis, db_session):
    from app.tasks.embedding import process_all_missing_embeddings

    with patch("app.tasks.embedding.DatabaseTask.db", db_session):
        statuses = _run_three_firings(fake_redis, process_all_missing_embeddings)
    assert "skipped" not in statuses


def test_chunks_sweep_runs_every_firing(fake_redis, db_session):
    from app.tasks.chunk_embeddings import process_all_missing_chunks

    with patch("app.tasks.chunk_embeddings.DatabaseTask.db", db_session):
        statuses = _run_three_firings(fake_redis, process_all_missing_chunks)
    assert "skipped" not in statuses


def test_overlap_within_interval_still_skipped(fake_redis, db_session):
    from app.tasks.embedding import process_all_missing_embeddings

    with patch("app.tasks.embedding.DatabaseTask.db", db_session):
        process_all_missing_embeddings.run()
        fake_redis.now += 60
        assert process_all_missing_embeddings.run()["status"] == "skipped"
