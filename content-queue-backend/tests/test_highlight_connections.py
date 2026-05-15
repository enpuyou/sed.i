"""
TDD tests for the two-mode connections panel endpoints (connections-panel-rewrite plan).

Behaviors tested (6 + 2 extra):
  Per-highlight endpoint (GET /search/connections/{highlight_id}):
    1. Returns wrapper with source_note + connections list
    2. Each connection has article metadata (id, title, author, domain, shared_tags, passages)
    3. Connections with no shared tags are included; shared_tags is empty list
    4. source_note is included from highlight.note
    5. Cross-user isolation

  Highlights-grouped endpoint (GET /search/connections/article/{content_id}/highlights):
    6. Returns list of {highlight_id, highlight_text, connections}
    7. Highlights with zero similarity connections are omitted
"""

from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.user import User
from app.core.security import create_access_token


# ─── helpers ────────────────────────────────────────────────────────────────


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _article(
    db: Session,
    user_id,
    url_suffix: str,
    tags: list[str],
    author: str | None = None,
) -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{url_suffix}",
        title=f"Article {url_suffix}",
        author=author,
        user_id=user_id,
        processing_status="completed",
        embedding=[0.1] * 1536,
        tags=tags,
    )
    db.add(item)
    return item


def _highlight(
    db: Session,
    user_id,
    item_id,
    text: str,
    emb: list[float],
    note: str | None = None,
) -> Highlight:
    h = Highlight(
        content_item_id=item_id,
        user_id=user_id,
        text=text,
        color="yellow",
        start_offset=0,
        end_offset=len(text),
        embedding=emb,
        note=note,
    )
    db.add(h)
    return h


# ─── shared embedding pairs ──────────────────────────────────────────────────

_EMB_A = [1.0] + [0.0] * 1535
_EMB_B = [0.999] + [0.001] + [0.0] * 1534  # cosine similarity ≈ 0.9999 with _EMB_A


# ─── Phase 1 Behavior 1: wrapper response shape ───────────────────────────────


class TestHighlightConnectionsShape:
    def test_returns_wrapper_with_source_note_and_connections(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 1: GET /search/connections/{id} returns {source_note, connections:[]}."""
        src = _article(db_session, test_user.id, "shape-src", ["AI alignment"])
        dst = _article(db_session, test_user.id, "shape-dst", ["AI alignment"])
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight text long enough here",
            _EMB_A,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight text long enough here",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/{src_h.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Must be a dict with these keys — not a flat list
        assert isinstance(data, dict), "response must be a wrapper object, not a list"
        assert "source_note" in data
        assert "connections" in data
        assert isinstance(data["connections"], list)

    def test_connection_has_required_fields(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 2: Each connection has article_id, title, author, domain, shared_tags, passages."""
        src = _article(db_session, test_user.id, "fields-src", ["AI alignment"])
        dst = _article(
            db_session, test_user.id, "fields-dst", ["AI alignment"], author="Jane Doe"
        )
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough text here",
            _EMB_A,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight with enough text here",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/{src_h.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["connections"], "expected at least one connection"

        conn = data["connections"][0]
        assert "article_id" in conn
        assert "article_title" in conn
        assert "article_author" in conn  # may be None
        assert "article_domain" in conn  # extracted from original_url
        assert "shared_tags" in conn
        assert "passages" in conn
        assert conn["article_domain"] == "example.com"
        assert conn["article_author"] == "Jane Doe"
        assert isinstance(conn["passages"], list)
        assert len(conn["passages"]) >= 1

    def test_no_shared_tags_connection_included(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 3: Connections with no shared tags are still included; shared_tags is []."""
        src = _article(db_session, test_user.id, "filter-src", ["distributed systems"])
        dst = _article(db_session, test_user.id, "filter-dst", ["behavioral economics"])
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough text here",
            _EMB_A,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight with enough text here",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/{src_h.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        article_ids = [c["article_id"] for c in data["connections"]]
        assert (
            str(dst.id) in article_ids
        ), "similar highlight should appear even without shared tags"
        conn = next(c for c in data["connections"] if c["article_id"] == str(dst.id))
        assert conn["shared_tags"] == []

    def test_source_note_included(self, client, db_session, test_user, auth_headers):
        """Behavior 4: source_note reflects highlight.note."""
        src = _article(db_session, test_user.id, "note-src", ["AI alignment"])
        dst = _article(db_session, test_user.id, "note-dst", ["AI alignment"])
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough text here",
            _EMB_A,
            note="my annotation about this idea",
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight with enough text here",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/{src_h.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        assert resp.json()["source_note"] == "my annotation about this idea"

    def test_source_note_null_when_absent(
        self, client, db_session, test_user, auth_headers
    ):
        """source_note is null when the highlight has no note."""
        src = _article(db_session, test_user.id, "nonote-src", ["AI alignment"])
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough text here",
            _EMB_A,
        )
        db_session.commit()

        resp = client.get(f"/search/connections/{src_h.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["source_note"] is None

    def test_cross_user_isolation(self, client, db_session, test_user, auth_headers):
        """Behavior 5: Cannot see another user's highlights in connections."""
        other = User(
            email="other2@example.com",
            username="other2",
            hashed_password="x",
            full_name="Other",
        )
        db_session.add(other)
        db_session.commit()

        src = _article(db_session, test_user.id, "iso-src", ["AI alignment"])
        other_art = _article(db_session, other.id, "iso-other", ["AI alignment"])
        db_session.commit()

        src_h = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Source highlight with enough text here",
            _EMB_A,
        )
        _highlight(
            db_session,
            other.id,
            other_art.id,
            "Other user highlight with enough chars",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/{src_h.id}",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        article_ids = [c["article_id"] for c in resp.json()["connections"]]
        assert str(other_art.id) not in article_ids


# ─── Phase 1 Behavior 6-7: highlights-grouped endpoint ───────────────────────


class TestHighlightGroupedConnections:
    def test_returns_per_highlight_grouping(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 6: /article/{id}/highlights returns list grouped by source highlight."""
        src = _article(db_session, test_user.id, "grp-src", ["AI alignment"])
        dst = _article(db_session, test_user.id, "grp-dst", ["AI alignment"])
        db_session.commit()

        h1 = _highlight(
            db_session,
            test_user.id,
            src.id,
            "First highlight with enough text here to pass",
            _EMB_A,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Connected highlight with enough text here to pass",
            _EMB_B,
        )
        db_session.commit()

        resp = client.get(
            f"/search/connections/article/{src.id}/highlights",
            headers=auth_headers,
            params={"threshold": 0.1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        item = next((x for x in data if x["highlight_id"] == str(h1.id)), None)
        assert item is not None, "expected h1 to appear in response"
        assert "highlight_text" in item
        assert "connections" in item
        assert isinstance(item["connections"], list)
        assert len(item["connections"]) >= 1

    def test_highlight_with_no_connections_omitted(
        self, client, db_session, test_user, auth_headers
    ):
        """Behavior 7: Highlights with zero similar highlights in other articles are omitted."""
        # _EMB_ORTHO is orthogonal to _EMB_A — cosine similarity = 0
        emb_ortho = [0.0, 1.0] + [0.0] * 1534
        src = _article(db_session, test_user.id, "omit-src", ["unique-tag-xyz"])
        dst = _article(db_session, test_user.id, "omit-dst", ["unique-tag-xyz"])
        db_session.commit()

        disconnected = _highlight(
            db_session,
            test_user.id,
            src.id,
            "Highlight with no similarity to anything else in library",
            _EMB_A,
        )
        _highlight(
            db_session,
            test_user.id,
            dst.id,
            "Orthogonal highlight with zero cosine similarity",
            emb_ortho,
        )
        db_session.commit()

        # Use a high threshold so the orthogonal pair (similarity ≈ 0) is excluded
        resp = client.get(
            f"/search/connections/article/{src.id}/highlights",
            headers=auth_headers,
            params={"threshold": 0.9},
        )
        assert resp.status_code == 200
        data = resp.json()
        highlight_ids = [x["highlight_id"] for x in data]
        assert str(disconnected.id) not in highlight_ids
