"""
TDD tests for the insight generation endpoint (Phase 2 — connections-panel-rewrite plan).

GET /search/connections/{highlight_id}/insight/{article_id}

Behaviors tested:
  1. Cache miss → calls generation, returns {insight: string}
  2. Cache hit → skips generation, returns cached value
  3. Generation failure → returns {insight: null}, not 500
  4. Unauthenticated → 401
"""

from unittest.mock import MagicMock, patch

from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.core.security import create_access_token
from app.models.user import User


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _article(db, user_id, url_suffix: str) -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{url_suffix}",
        title=f"Article {url_suffix}",
        user_id=user_id,
        processing_status="completed",
        embedding=[0.1] * 1536,
        tags=["AI alignment"],
    )
    db.add(item)
    return item


def _highlight(db, user_id, item_id, text: str, note: str | None = None) -> Highlight:
    h = Highlight(
        content_item_id=item_id,
        user_id=user_id,
        text=text,
        color="yellow",
        start_offset=0,
        end_offset=len(text),
        note=note,
    )
    db.add(h)
    return h


class TestInsightEndpoint:
    def _url(self, highlight_id, article_id) -> str:
        return f"/search/connections/{highlight_id}/insight/{article_id}"

    def test_cache_miss_returns_generated_insight(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 1: On cache miss, generates insight and returns it."""
        src = _article(db_session, test_user.id, "ins-src")
        dst = _article(db_session, test_user.id, "ins-dst")
        db_session.commit()

        src_h = _highlight(db_session, test_user.id, src.id, "Source highlight text")
        _highlight(db_session, test_user.id, dst.id, "Connected highlight text")
        db_session.commit()

        fake_redis = MagicMock()
        fake_redis.get.return_value = None  # cache miss

        fake_completion = MagicMock()
        fake_completion.choices = [
            MagicMock(message=MagicMock(content="Both explore mesa-optimization."))
        ]

        with (
            patch("app.api.search._get_redis_client", return_value=fake_redis),
            patch(
                "app.api.search._call_openai_insight",
                return_value="Both explore mesa-optimization.",
            ),
        ):
            resp = client.get(self._url(src_h.id, dst.id), headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "insight" in data
        assert data["insight"] == "Both explore mesa-optimization."

    def test_cache_hit_returns_cached_value_without_calling_openai(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 2: On cache hit, returns cached value, does not call OpenAI."""
        src = _article(db_session, test_user.id, "ins-hit-src")
        dst = _article(db_session, test_user.id, "ins-hit-dst")
        db_session.commit()

        src_h = _highlight(db_session, test_user.id, src.id, "Source highlight text")
        db_session.commit()

        fake_redis = MagicMock()
        fake_redis.get.return_value = b"Cached insight text."

        with (
            patch("app.api.search._get_redis_client", return_value=fake_redis),
            patch("app.api.search._call_openai_insight") as mock_openai,
        ):
            resp = client.get(self._url(src_h.id, dst.id), headers=auth_headers)
            mock_openai.assert_not_called()

        assert resp.status_code == 200
        assert resp.json()["insight"] == "Cached insight text."

    def test_generation_failure_returns_null_insight(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 3: OpenAI failure returns {insight: null}, not a 500."""
        src = _article(db_session, test_user.id, "ins-fail-src")
        dst = _article(db_session, test_user.id, "ins-fail-dst")
        db_session.commit()

        src_h = _highlight(db_session, test_user.id, src.id, "Source highlight text")
        db_session.commit()

        fake_redis = MagicMock()
        fake_redis.get.return_value = None

        with (
            patch("app.api.search._get_redis_client", return_value=fake_redis),
            patch(
                "app.api.search._call_openai_insight", side_effect=Exception("API down")
            ),
        ):
            resp = client.get(self._url(src_h.id, dst.id), headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json() == {"insight": None}

    def test_unauthenticated_returns_401(self, client, db_session, test_user):
        """Behavior 4: No auth header → 401."""
        resp = client.get(self._url("fake-id", "fake-article-id"))
        assert resp.status_code == 401
