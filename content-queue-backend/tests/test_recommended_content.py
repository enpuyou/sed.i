"""
Tests for GET /content/recommended.

Covers the pgvector rewrite of the embedding-similarity scoring factor
(previously a pure-Python O(N x M) nested loop — see app/api/content.py::
_max_similarity_to_recent_reads). These tests confirm the SQL query produces
the same shape of result the old Python loop did: unread items more similar
to recently-read items score higher.
"""

from datetime import datetime, timedelta, timezone

from app.models.content import ContentItem
from app.api.content import _max_similarity_to_recent_reads


def _make_item(db, user, *, title, is_read, embedding=None, read_at=None, **extra):
    item = ContentItem(
        user_id=user.id,
        title=title,
        original_url=f"https://example.com/{title.replace(' ', '-').lower()}",
        is_read=is_read,
        read_at=read_at,
        embedding=embedding,
        **extra,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class TestMaxSimilarityToRecentReads:
    """Direct tests for the new SQL helper."""

    def test_no_recent_reads_returns_empty_dict(self, db_session, test_user):
        _make_item(
            db_session, test_user, title="Unread", is_read=False, embedding=[0.1] * 1536
        )
        result = _max_similarity_to_recent_reads(
            db_session, test_user.id, datetime.now(timezone.utc) - timedelta(days=7)
        )
        assert result == {}

    def test_identical_embedding_scores_near_one(self, db_session, test_user):
        now = datetime.now(timezone.utc)
        _make_item(
            db_session,
            test_user,
            title="Recent Read",
            is_read=True,
            read_at=now,
            embedding=[0.5] * 1536,
        )
        unread = _make_item(
            db_session,
            test_user,
            title="Unread Similar",
            is_read=False,
            embedding=[0.5] * 1536,
        )
        result = _max_similarity_to_recent_reads(
            db_session, test_user.id, now - timedelta(days=7)
        )
        assert str(unread.id) in result
        assert result[str(unread.id)] > 0.99

    def test_old_read_outside_window_excluded(self, db_session, test_user):
        now = datetime.now(timezone.utc)
        _make_item(
            db_session,
            test_user,
            title="Old Read",
            is_read=True,
            read_at=now - timedelta(days=30),
            embedding=[0.5] * 1536,
        )
        unread = _make_item(
            db_session,
            test_user,
            title="Unread",
            is_read=False,
            embedding=[0.5] * 1536,
        )
        result = _max_similarity_to_recent_reads(
            db_session, test_user.id, now - timedelta(days=7)
        )
        assert str(unread.id) not in result

    def test_takes_max_across_multiple_recent_reads(self, db_session, test_user):
        now = datetime.now(timezone.utc)
        # One dissimilar, one identical recent read — should take the max (identical).
        _make_item(
            db_session,
            test_user,
            title="Dissimilar Read",
            is_read=True,
            read_at=now,
            embedding=[1.0] + [0.0] * 1535,
        )
        _make_item(
            db_session,
            test_user,
            title="Identical Read",
            is_read=True,
            read_at=now,
            embedding=[0.5] * 1536,
        )
        unread = _make_item(
            db_session,
            test_user,
            title="Unread",
            is_read=False,
            embedding=[0.5] * 1536,
        )
        result = _max_similarity_to_recent_reads(
            db_session, test_user.id, now - timedelta(days=7)
        )
        assert result[str(unread.id)] > 0.99

    def test_cross_user_isolation(self, db_session, test_user):
        from app.models.user import User
        from app.core.security import get_password_hash

        other_user = User(
            email="other-recommended@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        now = datetime.now(timezone.utc)
        _make_item(
            db_session,
            other_user,
            title="Other User Read",
            is_read=True,
            read_at=now,
            embedding=[0.5] * 1536,
        )
        unread = _make_item(
            db_session,
            test_user,
            title="My Unread",
            is_read=False,
            embedding=[0.5] * 1536,
        )
        result = _max_similarity_to_recent_reads(
            db_session, test_user.id, now - timedelta(days=7)
        )
        # Other user's read item must not contribute a similarity score.
        assert str(unread.id) not in result


class TestRecommendedEndpoint:
    def test_empty_unread_returns_empty_list(self, client, auth_headers):
        response = client.get("/content/recommended", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_returns_unread_items_scored(
        self, db_session, test_user, client, auth_headers
    ):
        now = datetime.now(timezone.utc)
        _make_item(
            db_session,
            test_user,
            title="Recent Read",
            is_read=True,
            read_at=now,
            embedding=[0.5] * 1536,
        )
        similar = _make_item(
            db_session,
            test_user,
            title="Similar Unread",
            is_read=False,
            embedding=[0.5] * 1536,
        )
        dissimilar = _make_item(
            db_session,
            test_user,
            title="Dissimilar Unread",
            is_read=False,
            embedding=[1.0] + [0.0] * 1535,
        )

        response = client.get("/content/recommended", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        ids = [item["id"] for item in data["items"]]
        assert str(similar.id) in ids
        assert str(dissimilar.id) in ids
        # The item embedding-similar to a recent read should rank above the
        # dissimilar one — same expected behavior as before the rewrite.
        assert ids.index(str(similar.id)) < ids.index(str(dissimilar.id))

    def test_items_without_embedding_still_scored_on_other_factors(
        self, db_session, test_user, client, auth_headers
    ):
        """An unread item with no embedding must not be dropped — it should
        still be scored on recency/tags/reading-time, just with similarity=0."""
        item = _make_item(
            db_session, test_user, title="No Embedding", is_read=False, embedding=None
        )
        response = client.get("/content/recommended", headers=auth_headers)
        assert response.status_code == 200
        ids = [i["id"] for i in response.json()["items"]]
        assert str(item.id) in ids
