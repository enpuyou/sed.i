"""process_all_missing_embeddings also covers completed items without an embedding."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.content import ContentItem
from app.tasks.embedding import process_all_missing_embeddings


def _item(
    db,
    user,
    url,
    *,
    status="completed",
    full_text="body text",
    age_min=30,
    deleted=False,
):
    item = ContentItem(
        original_url=url,
        user_id=user.id,
        processing_status=status,
        full_text=full_text,
    )
    db.add(item)
    db.commit()
    db.execute(
        text("UPDATE content_items SET updated_at=:t, deleted_at=:d WHERE id=:i"),
        {
            "t": datetime.now(timezone.utc) - timedelta(minutes=age_min),
            "d": datetime.now(timezone.utc) if deleted else None,
            "i": item.id,
        },
    )
    db.commit()
    return str(item.id)


def _run(db):
    with patch("app.core.task_locks.acquire_run_lock", return_value=True), patch(
        "app.tasks.embedding.DatabaseTask.db", db
    ), patch("app.tasks.embedding.generate_embedding.delay") as d, patch(
        "app.tasks.embedding.generate_highlight_embeddings_batch.delay"
    ):
        result = process_all_missing_embeddings.run()
    return result, [c.args[0] for c in d.call_args_list]


def test_dispatches_old_completed_item_without_embedding(db_session, test_user):
    iid = _item(db_session, test_user, "https://e.com/1")
    result, dispatched = _run(db_session)
    assert dispatched == [iid]
    assert result["items_dispatched"] == 1


def test_skips_recent_in_flight_pending_failed_deleted_and_blank(db_session, test_user):
    _item(db_session, test_user, "https://e.com/recent", age_min=1)
    _item(db_session, test_user, "https://e.com/pending", status="pending")
    _item(db_session, test_user, "https://e.com/failed", status="failed")
    _item(db_session, test_user, "https://e.com/del", deleted=True)
    _item(db_session, test_user, "https://e.com/blank", full_text="   ")
    _, dispatched = _run(db_session)
    assert dispatched == []


def test_skips_item_with_embedding(db_session, test_user):
    iid = _item(db_session, test_user, "https://e.com/emb")
    db_session.execute(
        text("UPDATE content_items SET embedding=:e WHERE id=:i"),
        {"e": "[" + ",".join(["0.1"] * 1536) + "]", "i": iid},
    )
    db_session.commit()
    _, dispatched = _run(db_session)
    assert dispatched == []


def test_item_batch_is_bounded(db_session, test_user):
    for n in range(4):
        _item(db_session, test_user, f"https://e.com/b{n}")
    with patch("app.tasks.embedding._ITEM_SWEEP_LIMIT", 2):
        _, dispatched = _run(db_session)
    assert len(dispatched) == 2
