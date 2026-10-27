"""
Integration tests for GET /analytics/stats.

Covers:
- Empty state (all-zero response)
- Mixed read/unread/archived items with reading time
- Cross-user isolation (User B cannot see User A's stats)
- Regression: items_unread was always 0 due to `not Column` Python bug
- Auth guard
"""

import uuid

from app.models.content import ContentItem


def _make_item(user_id, *, is_read=False, is_archived=False, reading_time=None):
    return ContentItem(
        user_id=user_id,
        original_url=f"https://example.com/article-{uuid.uuid4().hex}",
        processing_status="completed",
        is_read=is_read,
        is_archived=is_archived,
        reading_time_minutes=reading_time,
    )


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_stats_empty_returns_zeros(client, auth_headers, db_session, test_user):
    """User with no content items gets all-zero stats."""
    response = client.get("/analytics/stats", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 0
    assert data["items_read"] == 0
    assert data["items_unread"] == 0
    assert data["items_archived"] == 0
    assert data["total_reading_time_minutes"] == 0
    assert data["read_reading_time_minutes"] == 0


# ---------------------------------------------------------------------------
# Correct counts
# ---------------------------------------------------------------------------


def test_stats_counts_are_correct(client, auth_headers, db_session, test_user):
    """
    Stats correctly reflect mixed item states.

    Regression: Python `not ContentItem.is_read` was always False
    (SQLAlchemy column objects are truthy), making items_unread always 0.
    """
    items = [
        _make_item(test_user.id, is_read=True, reading_time=10),  # read
        _make_item(test_user.id, is_read=True, reading_time=5),  # read
        _make_item(
            test_user.id, is_read=False, is_archived=False, reading_time=8
        ),  # unread
        _make_item(
            test_user.id, is_read=False, is_archived=True, reading_time=3
        ),  # archived
    ]
    for item in items:
        db_session.add(item)
    db_session.commit()

    response = client.get("/analytics/stats", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total_items"] == 4
    assert data["items_read"] == 2  # 2 explicitly read
    assert data["items_unread"] == 1  # 1 not read, not archived
    assert data["items_archived"] == 1  # 1 archived
    assert data["total_reading_time_minutes"] == 26  # 10+5+8+3
    assert data["read_reading_time_minutes"] == 15  # 10+5


def test_stats_excludes_soft_deleted(client, auth_headers, db_session, test_user):
    """Soft-deleted items are excluded from stats."""
    from datetime import datetime

    item = _make_item(test_user.id, is_read=True, reading_time=20)
    item.deleted_at = datetime.utcnow()
    db_session.add(item)
    db_session.commit()

    response = client.get("/analytics/stats", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 0
    assert data["total_reading_time_minutes"] == 0


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------


def test_stats_isolated_to_current_user(client, auth_headers, db_session, test_user):
    """User B's items do not appear in User A's stats."""
    from app.models.user import User
    from app.core.security import get_password_hash

    # Create a second user
    other_user = User(
        email="other@example.com",
        hashed_password=get_password_hash("pass"),
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    # Add items for other user only
    for _ in range(5):
        db_session.add(_make_item(other_user.id, is_read=True, reading_time=10))
    db_session.commit()

    # test_user has no items → stats should all be zero
    response = client.get("/analytics/stats", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 0
    assert data["items_read"] == 0


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_stats_unauthenticated_returns_401(client):
    response = client.get("/analytics/stats")
    assert response.status_code == 401
