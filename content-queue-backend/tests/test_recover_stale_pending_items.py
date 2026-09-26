"""
Unit tests for recover_stale_pending_items (app/tasks/extraction.py).

Beat-scheduled every 5 minutes; re-dispatches content items stuck at
processing_status='pending'. Guarded by a Redis lock (app.core.task_locks)
so an overrunning previous firing can't double-dispatch the same item.
"""

from unittest.mock import patch

from app.tasks.extraction import recover_stale_pending_items


def test_skips_when_lock_held():
    """A second firing while the lock is held must not query or dispatch."""
    with patch("app.core.task_locks.acquire_run_lock", return_value=False), patch(
        "app.tasks.extraction.extract_metadata.delay"
    ) as mock_delay:
        result = recover_stale_pending_items.run()

    assert result == {"recovered": 0, "status": "skipped", "reason": "lock_held"}
    mock_delay.assert_not_called()


def test_dispatches_stale_items_when_lock_acquired():
    """When the lock is free, stale pending items are re-dispatched as before."""
    fake_id = "22222222-2222-2222-2222-222222222222"

    with patch("app.core.task_locks.acquire_run_lock", return_value=True), patch(
        "app.tasks.extraction.DatabaseTask.db"
    ) as mock_db, patch("app.tasks.extraction.extract_metadata.delay") as mock_delay:
        fake_item = type("FakeItem", (), {"id": fake_id, "created_at": "2026-01-01"})()
        mock_db.query.return_value.filter.return_value.all.return_value = [fake_item]
        result = recover_stale_pending_items.run()

    assert result == {"recovered": 1}
    mock_delay.assert_called_once_with(fake_id)
