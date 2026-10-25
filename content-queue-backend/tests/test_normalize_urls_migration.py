"""
Data migration a7d3e9c15b42: normalize stored content URLs.

Acceptance: legacy rows are rewritten to the form ingest_url stores; two rows
that normalize alike keep one (already-normalized wins, else newest) and
soft-delete the rest without violating the unique index; re-running changes
nothing; a legacy URL is still treated as a duplicate on save after migrating
(the old fallback scan is gone, the migration is what makes this true).
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.content import ContentItem
from app.services.content import normalize_url

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "a7d3e9c15b42_normalize_stored_content_urls.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("normalize_urls_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(db, user, url, *, created_at=None, deleted=False):
    item = ContentItem(
        user_id=user.id,
        original_url=url,
        submitted_via="web",
        processing_status="completed",
        created_at=created_at or datetime.now(timezone.utc),
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _run(migration, db):
    counts = migration.normalize_active_urls(db.connection())
    db.commit()
    db.expire_all()
    return counts


def test_frozen_normalization_matches_runtime_function(migration):
    samples = [
        "HTTPS://Example.com/article/?utm_source=x&b=2&a=1#frag",
        "https://example.com/a/",
        "https://example.com/a?ref=tw",
        "https://example.com",
        "  https://Example.COM/Path  ",
    ]
    for url in samples:
        assert migration._normalize_url(url) == normalize_url(url)


def test_legacy_rows_are_rewritten_and_normalized_rows_untouched(
    migration, db_session, test_user
):
    legacy = _item(
        db_session, test_user, "HTTPS://Example.com/article/?utm_source=n#section"
    )
    clean = _item(db_session, test_user, "https://example.com/other")

    counts = _run(migration, db_session)

    assert db_session.get(ContentItem, legacy.id).original_url == (
        "https://example.com/article"
    )
    assert db_session.get(ContentItem, clean.id).original_url == (
        "https://example.com/other"
    )
    assert counts == {"scanned": 2, "rewritten": 1, "duplicates_soft_deleted": 0}


def test_collision_keeps_already_normalized_row_and_soft_deletes_other(
    migration, db_session, test_user
):
    clean = _item(db_session, test_user, "https://example.com/a")
    legacy = _item(
        db_session,
        test_user,
        "https://example.com/a/",
        created_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    counts = _run(migration, db_session)

    assert db_session.get(ContentItem, clean.id).deleted_at is None
    assert db_session.get(ContentItem, legacy.id).deleted_at is not None
    assert counts["duplicates_soft_deleted"] == 1


def test_collision_between_two_legacy_rows_keeps_newest(
    migration, db_session, test_user
):
    now = datetime.now(timezone.utc)
    older = _item(
        db_session,
        test_user,
        "https://example.com/b/",
        created_at=now - timedelta(days=2),
    )
    newer = _item(db_session, test_user, "https://example.com/b#x", created_at=now)

    _run(migration, db_session)

    assert db_session.get(ContentItem, older.id).deleted_at is not None
    survivor = db_session.get(ContentItem, newer.id)
    assert survivor.deleted_at is None
    assert survivor.original_url == "https://example.com/b"


def test_same_url_for_different_users_is_not_a_collision(
    migration, db_session, test_user
):
    from app.core.security import get_password_hash
    from app.models.user import User

    other = User(
        email="migration-other@example.com",
        username="migrationother",
        hashed_password=get_password_hash("pw"),
    )
    db_session.add(other)
    db_session.commit()
    a = _item(db_session, test_user, "https://example.com/c/")
    b = _item(db_session, other, "https://example.com/c/")

    try:
        counts = _run(migration, db_session)

        assert counts["duplicates_soft_deleted"] == 0
        assert db_session.get(ContentItem, a.id).original_url == "https://example.com/c"
        assert db_session.get(ContentItem, b.id).original_url == "https://example.com/c"
    finally:
        db_session.query(ContentItem).filter(ContentItem.user_id == other.id).delete()
        db_session.delete(other)
        db_session.commit()


def test_soft_deleted_rows_are_left_alone(migration, db_session, test_user):
    gone = _item(db_session, test_user, "https://example.com/d/", deleted=True)

    counts = _run(migration, db_session)

    assert db_session.get(ContentItem, gone.id).original_url == "https://example.com/d/"
    assert counts["scanned"] == 0


def test_rerun_is_a_no_op(migration, db_session, test_user):
    _item(db_session, test_user, "https://example.com/e/")
    _item(db_session, test_user, "https://example.com/f")
    _run(migration, db_session)

    counts = _run(migration, db_session)

    assert counts == {"scanned": 2, "rewritten": 0, "duplicates_soft_deleted": 0}


@patch("app.tasks.extraction.extract_metadata")
def test_legacy_url_is_still_a_duplicate_after_migrating(
    mock_extract, migration, client, auth_headers, test_user, db_session
):
    legacy = _item(
        db_session, test_user, "HTTPS://Example.com/article/?utm_source=n#section"
    )
    _run(migration, db_session)

    response = client.post(
        "/content", json={"url": "https://example.com/article"}, headers=auth_headers
    )

    assert response.status_code == 409
    detail = json.loads(response.json()["detail"])
    assert detail["existing_id"] == str(legacy.id)
    mock_extract.delay.assert_not_called()


def _highlight(db, user, item, text="a highlighted passage"):
    from app.models.highlight import Highlight

    row = Highlight(
        user_id=user.id,
        content_item_id=item.id,
        text=text,
        start_offset=0,
        end_offset=len(text),
    )
    db.add(row)
    db.commit()
    return row


def test_collision_keeps_the_row_that_has_highlights(migration, db_session, test_user):
    now = datetime.now(timezone.utc)
    highlighted = _item(
        db_session,
        test_user,
        "https://example.com/g/",
        created_at=now - timedelta(days=5),
    )
    _highlight(db_session, test_user, highlighted)
    clean_newer = _item(db_session, test_user, "https://example.com/g", created_at=now)

    _run(migration, db_session)

    assert db_session.get(ContentItem, clean_newer.id).deleted_at is not None
    survivor = db_session.get(ContentItem, highlighted.id)
    assert survivor.deleted_at is None
    assert survivor.original_url == "https://example.com/g"


def test_winner_inherits_list_memberships_of_the_loser(
    migration, db_session, test_user
):
    from app.models.list import List, content_list_membership

    now = datetime.now(timezone.utc)
    winner = _item(db_session, test_user, "https://example.com/h", created_at=now)
    loser = _item(
        db_session,
        test_user,
        "https://example.com/h/",
        created_at=now - timedelta(days=1),
    )
    reading = List(name="Reading", owner_id=test_user.id)
    db_session.add(reading)
    db_session.commit()
    db_session.refresh(reading)
    db_session.execute(
        content_list_membership.insert().values(
            content_item_id=loser.id, list_id=reading.id, added_by=test_user.id
        )
    )
    db_session.commit()

    _run(migration, db_session)

    members = db_session.execute(
        content_list_membership.select().where(
            content_list_membership.c.list_id == reading.id
        )
    ).fetchall()
    assert winner.id in {m.content_item_id for m in members}
    assert db_session.get(ContentItem, loser.id).deleted_at is not None


def test_losing_row_with_highlights_is_logged_not_lost(
    migration, db_session, test_user, caplog
):
    import logging

    now = datetime.now(timezone.utc)
    first = _item(db_session, test_user, "https://example.com/i", created_at=now)
    second = _item(
        db_session,
        test_user,
        "https://example.com/i/",
        created_at=now - timedelta(days=1),
    )
    _highlight(db_session, test_user, first)
    _highlight(db_session, test_user, second, text="another passage")

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        _run(migration, db_session)

    loser = next(
        i for i in (first, second) if db_session.get(ContentItem, i.id).deleted_at
    )
    assert str(loser.id) in caplog.text
    # The loser's highlight row still exists (soft delete only).
    from app.models.highlight import Highlight

    assert db_session.query(Highlight).filter_by(content_item_id=loser.id).count() == 1
