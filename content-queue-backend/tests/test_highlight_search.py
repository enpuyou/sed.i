"""
TDD tests for highlight + note search (Feature 2).

Behaviors tested:
- GET /search/semantic returns { articles: [...], highlights: [...] }
- Highlights matching query text appear in results
- Highlight note text is also searchable
- Results include parent article title and content_item_id for linking
- Articles that don't match don't appear in highlight results
- Unauthenticated requests are rejected
- User only sees their own highlights
"""

import pytest
from app.models.highlight import Highlight
from app.models.content import ContentItem


@pytest.fixture
def content_with_highlights(db_session, test_user):
    """A saved article with two highlights and one note."""
    item = ContentItem(
        original_url="https://example.com/rag-article",
        title="Context Engineering for LLMs",
        description="How to build better RAG pipelines",
        full_text="<p>Context engineering is the practice of structuring information.</p>",
        user_id=test_user.id,
        processing_status="completed",
    )
    db_session.add(item)
    db_session.flush()

    h1 = Highlight(
        content_item_id=item.id,
        user_id=test_user.id,
        text="context engineering is the practice of structuring information",
        start_offset=3,
        end_offset=66,
        color="yellow",
    )
    h2 = Highlight(
        content_item_id=item.id,
        user_id=test_user.id,
        text="retrieval augmented generation pipeline",
        note="important for my thesis on knowledge systems",
        start_offset=100,
        end_offset=140,
        color="blue",
    )
    db_session.add_all([h1, h2])
    db_session.commit()
    db_session.refresh(item)
    return item, h1, h2


class TestHighlightSearchResponseShape:
    """Search response must return a structured object with articles and highlights keys."""

    def test_search_returns_articles_and_highlights_keys(self, client, auth_headers):
        """GET /search/semantic now returns {articles, highlights} not a flat list."""
        resp = client.get("/search/semantic?query=test query", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "articles" in data, "Response must have 'articles' key"
        assert "highlights" in data, "Response must have 'highlights' key"
        assert isinstance(data["articles"], list)
        assert isinstance(data["highlights"], list)

    def test_unauthenticated_still_rejected(self, client):
        """Auth requirement unchanged."""
        resp = client.get("/search/semantic?query=test")
        assert resp.status_code == 401

    def test_min_query_length_still_enforced(self, client, auth_headers):
        """Validation unchanged."""
        resp = client.get("/search/semantic?query=ab", headers=auth_headers)
        assert resp.status_code == 422


class TestHighlightSearchResults:
    """Highlight text and notes are searchable; results link back to articles."""

    def test_highlight_text_appears_in_results(
        self, client, auth_headers, content_with_highlights
    ):
        """A word that appears in highlight text surfaces that highlight."""
        _, h1, _ = content_with_highlights
        resp = client.get(
            "/search/semantic?query=context+engineering", headers=auth_headers
        )
        assert resp.status_code == 200
        highlights = resp.json()["highlights"]
        ids = [h["highlight_id"] for h in highlights]
        assert str(h1.id) in ids, "Highlight matching query text must appear in results"

    def test_highlight_result_has_required_fields(
        self, client, auth_headers, content_with_highlights
    ):
        """Each highlight result carries article_title and content_item_id for linking."""
        item, _, _ = content_with_highlights
        resp = client.get(
            "/search/semantic?query=context+engineering", headers=auth_headers
        )
        results = resp.json()["highlights"]
        assert len(results) >= 1
        r = results[0]
        assert "highlight_id" in r
        assert "text" in r
        assert "content_item_id" in r
        assert "article_title" in r
        assert r["content_item_id"] == str(item.id)
        assert r["article_title"] == item.title

    def test_note_text_is_searchable(
        self, client, auth_headers, content_with_highlights
    ):
        """A word that only appears in a highlight note also surfaces that highlight."""
        _, _, h2 = content_with_highlights
        resp = client.get(
            "/search/semantic?query=thesis+knowledge+systems", headers=auth_headers
        )
        assert resp.status_code == 200
        highlights = resp.json()["highlights"]
        ids = [h["highlight_id"] for h in highlights]
        assert str(h2.id) in ids, "Note text must be searchable"

    def test_unrelated_query_returns_empty_highlights(
        self, client, auth_headers, content_with_highlights
    ):
        """A query with no matching highlights returns an empty highlights list."""
        resp = client.get(
            "/search/semantic?query=vinyl+records+discography", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["highlights"] == []

    def test_user_only_sees_own_highlights(
        self, client, auth_headers, content_with_highlights, db_session
    ):
        """User B's highlights don't appear in User A's search results."""
        from app.models.user import User
        from app.core.security import get_password_hash

        item, _, _ = content_with_highlights

        other_user = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=get_password_hash("pw"),
        )
        db_session.add(other_user)
        db_session.flush()

        other_item = ContentItem(
            original_url="https://example.com/other",
            title="Other article",
            user_id=other_user.id,
            processing_status="completed",
        )
        db_session.add(other_item)
        db_session.flush()

        other_highlight = Highlight(
            content_item_id=other_item.id,
            user_id=other_user.id,
            text="context engineering secret note from other user",
            start_offset=0,
            end_offset=50,
            color="yellow",
        )
        db_session.add(other_highlight)
        db_session.commit()

        # Search as test_user
        resp = client.get(
            "/search/semantic?query=context+engineering+secret",
            headers=auth_headers,
        )
        highlight_ids = [h["highlight_id"] for h in resp.json()["highlights"]]
        assert str(other_highlight.id) not in highlight_ids
