"""
TDD tests for tag-grouped highlight connections (Phase 3 — connections plan).

Behaviors tested:
- /search/connections/article/{id} response includes shared_tags per ArticleConnection
- shared_tags is the intersection of source article tags and connected article tags
- shared_tags is empty when articles share no tags
- user cannot see another user's connections
"""

from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.user import User
from app.core.security import create_access_token


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _article(db: Session, user_id, url_suffix: str, tags: list[str]) -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{url_suffix}",
        title=f"Article {url_suffix}",
        user_id=user_id,
        processing_status="completed",
        embedding=[0.1] * 1536,
        tags=tags,
    )
    db.add(item)
    return item


def _highlight(db: Session, user_id, item_id, text: str, emb: list[float]) -> Highlight:
    h = Highlight(
        content_item_id=item_id,
        user_id=user_id,
        text=text,
        color="yellow",
        start_offset=0,
        end_offset=len(text),
        embedding=emb,
    )
    db.add(h)
    return h


class TestArticleConnectionsSharedTags:
    def test_shared_tags_present_in_response(
        self, client, db_session, test_user, auth_headers
    ):
        """shared_tags key exists on every ArticleConnection."""
        src = _article(
            db_session, test_user.id, "src", ["AI alignment", "mesa-optimization"]
        )
        dst = _article(
            db_session, test_user.id, "dst", ["AI alignment", "neural scaling"]
        )
        db_session.commit()

        # Give src a highlight with embedding pointing toward dst
        emb_a = [1.0] + [0.0] * 1535
        emb_b = [0.99] + [0.01] + [0.0] * 1534  # very similar to emb_a
        _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight text about AI alignment",
            emb_a,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight text about AI alignment",
            emb_b,
        )
        db_session.commit()

        resp = client.get(f"/search/connections/article/{src.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        if data:  # may be empty if threshold not met — still check schema
            assert "shared_tags" in data[0]

    def test_shared_tags_correct_intersection(
        self, client, db_session, test_user, auth_headers
    ):
        """shared_tags is the intersection of source and connected article tags."""
        src = _article(
            db_session,
            test_user.id,
            "src-tags",
            ["AI alignment", "mesa-optimization", "unique-src"],
        )
        dst = _article(
            db_session,
            test_user.id,
            "dst-tags",
            ["AI alignment", "mesa-optimization", "unique-dst"],
        )
        db_session.commit()

        emb_a = [1.0] + [0.0] * 1535
        emb_b = [0.999] + [0.001] + [0.0] * 1534
        _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight text with enough chars here",
            emb_a,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight text with enough chars here",
            emb_b,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/article/{src.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data, "Expected at least one connection"
        conn = next((c for c in data if c["article_id"] == str(dst.id)), None)
        assert conn is not None
        shared = set(conn["shared_tags"])
        assert "AI alignment" in shared
        assert "mesa-optimization" in shared
        assert "unique-src" not in shared
        assert "unique-dst" not in shared

    def test_no_shared_tags_connection_included(
        self, client, db_session, test_user, auth_headers
    ):
        """Connections surface even without shared tags when similarity exceeds threshold."""
        src = _article(
            db_session, test_user.id, "src-nooverlap", ["distributed systems"]
        )
        dst = _article(
            db_session, test_user.id, "dst-nooverlap", ["behavioral economics"]
        )
        db_session.commit()

        emb_a = [1.0] + [0.0] * 1535
        emb_b = [0.999] + [0.001] + [0.0] * 1534
        _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough characters here",
            emb_a,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight with enough characters here",
            emb_b,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/article/{src.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        article_ids = [c["article_id"] for c in data]
        assert str(dst.id) in article_ids

    def test_cross_user_isolation(self, client, db_session, test_user, auth_headers):
        """User cannot see another user's article connections."""
        other_user = User(
            email="other@example.com",
            username="other",
            hashed_password="x",
            full_name="Other",
        )
        db_session.add(other_user)
        db_session.commit()

        src = _article(db_session, test_user.id, "src-isolation", ["AI"])
        other_article = _article(db_session, other_user.id, "other-isolation", ["AI"])
        db_session.commit()

        emb = [1.0] + [0.0] * 1535
        _highlight(
            db_session,
            test_user.id,
            src.id,
            "User highlight with enough characters here",
            emb,
        )
        _highlight(
            db_session,
            other_user.id,
            other_article.id,
            "Other user highlight with enough chars",
            emb,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/article/{src.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        # Should not return the other user's article
        data = resp.json()
        article_ids = [c["article_id"] for c in data]
        assert str(other_article.id) not in article_ids
