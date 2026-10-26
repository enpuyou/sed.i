"""
recover_stuck_processing_items: items left at processing_status='processing'
by a killed worker are reset to 'pending' once, and failed the second time.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.content import ContentItem
from app.tasks.extraction import (
    RESTART_MARKER,
    recover_stuck_processing_items,
)


def _item(db, user, status, age_min, error=None, url="https://example.com/a"):
    item = ContentItem(
        original_url=url,
        user_id=user.id,
        processing_status=status,
        processing_error=error,
    )
    db.add(item)
    db.commit()
    stamp = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    db.execute(
        text("UPDATE content_items SET updated_at=:t WHERE id=:i"),
        {"t": stamp, "i": item.id},
    )
    db.commit()
    return item.id


def _run(db):
    with patch("app.core.task_locks.acquire_run_lock", return_value=True), patch(
        "app.core.task_locks.release_run_lock"
    ), patch("app.tasks.extraction.DatabaseTask.db", db):
        return recover_stuck_processing_items.run()


def _get(db, item_id):
    db.expire_all()
    return db.query(ContentItem).filter(ContentItem.id == item_id).one()


def test_first_restart_sets_pending_with_marker(db_session, test_user):
    iid = _item(db_session, test_user, "processing", 36)
    assert _run(db_session)["recovered"] == 1
    item = _get(db_session, iid)
    assert item.processing_status == "pending"
    assert item.processing_error == RESTART_MARKER


def test_second_restart_marks_failed(db_session, test_user):
    iid = _item(db_session, test_user, "processing", 36, error=RESTART_MARKER)
    result = _run(db_session)
    item = _get(db_session, iid)
    assert result["failed"] == 1
    assert item.processing_status == "failed"
    assert "interrupted" in item.processing_error.lower()


def test_recent_processing_item_untouched(db_session, test_user):
    iid = _item(db_session, test_user, "processing", 20)
    assert _run(db_session)["recovered"] == 0
    assert _get(db_session, iid).processing_status == "processing"


def test_other_statuses_untouched(db_session, test_user):
    a = _item(db_session, test_user, "completed", 90, url="https://example.com/b")
    b = _item(db_session, test_user, "failed", 90, url="https://example.com/c")
    _run(db_session)
    assert _get(db_session, a).processing_status == "completed"
    assert _get(db_session, b).processing_status == "failed"


def test_batch_is_bounded(db_session, test_user):
    for n in range(5):
        _item(db_session, test_user, "processing", 60, url=f"https://example.com/{n}")
    with patch("app.tasks.extraction._STUCK_BATCH_LIMIT", 2):
        assert _run(db_session)["recovered"] == 2


def test_skips_when_lock_held():
    with patch("app.core.task_locks.acquire_run_lock", return_value=False):
        assert recover_stuck_processing_items.run()["status"] == "skipped"


def test_marker_is_not_labelled_as_source_problem():
    """The failed message must fall through to the generic 'unknown' bucket in
    frontend/lib/ingestErrors.ts: none of its substring patterns may match."""
    from app.tasks.extraction import STUCK_FAILED_MESSAGE

    patterns = [
        "403",
        "blocked",
        "bot",
        "captcha",
        "cloudflare",
        "rate limit",
        "429",
        "401",
        "unauthorized",
        "authorization",
        "login required",
        "access denied",
        "httperror",
        "client error",
        "server error",
        "http ",
        "status code",
        "for url",
        "timed out",
        "timeout",
        "request error",
        "network",
        "connection",
        "dns",
        "temporary",
        "unreachable",
        "too many requests",
        "not authorized",
        "authentication required",
    ]
    low = STUCK_FAILED_MESSAGE.lower()
    assert [p for p in patterns if p in low] == []


def test_registered_in_beat_every_10_minutes():
    from app.core.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["recover-stuck-processing-items"]
    assert entry["task"] == "app.tasks.extraction.recover_stuck_processing_items"
    assert entry["schedule"] == 600
