"""
Golden-path integration tests.

Covers the 5 critical user flows end-to-end: real DB, real HTTP routes, no mocks
(except Celery dispatch which would require a running worker).

Run these first — they verify the app works for real users:
    pytest tests/test_golden_paths.py -v

A failing test here means a user-visible flow is broken, not just a unit.
All tests use real PostgreSQL via TestClient with the shared session fixture.
"""

import pytest
from unittest.mock import patch
from app.models.user import User
from app.core.security import get_password_hash, create_access_token


# ---------------------------------------------------------------------------
# Additional fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def other_user(db_session):
    """A second user — used to verify resource isolation."""
    user = User(
        email="other@example.com",
        username="otheruser",
        hashed_password=get_password_hash("password"),
        full_name="Other User",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def other_auth_headers(other_user):
    token = create_access_token(data={"sub": other_user.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def searchable_article(db_session, test_user):
    """A content item with title/author/description so search_vector is populated."""
    from app.models.content import ContentItem

    item = ContentItem(
        original_url="https://example.com/react-hooks-guide",
        title="A Complete Guide to React Hooks",
        author="Dan Abramov",
        description="Understanding useState, useEffect, and custom hooks in React.",
        tags=["react", "javascript"],
        full_text="Hooks let you use state without writing a class. " * 20,
        user_id=test_user.id,
        processing_status="completed",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Flow 1: Submit and retrieve
# ---------------------------------------------------------------------------


class TestSubmitAndRetrieve:
    """User submits a URL → item appears in their library and is retrievable."""

    @patch("app.tasks.extraction.extract_metadata")
    def test_submit_url_creates_pending_item(self, mock_task, client, auth_headers):
        resp = client.post(
            "/content",
            json={"url": "https://example.com/new-article"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["original_url"] == "https://example.com/new-article"
        assert data["processing_status"] == "pending"
        assert "id" in data

    @patch("app.tasks.extraction.extract_metadata")
    def test_submitted_item_is_retrievable(self, mock_task, client, auth_headers):
        post = client.post(
            "/content",
            json={"url": "https://example.com/article-to-retrieve"},
            headers=auth_headers,
        )
        assert post.status_code == 201
        item_id = post.json()["id"]

        get = client.get(f"/content/{item_id}", headers=auth_headers)
        assert get.status_code == 200
        assert get.json()["id"] == item_id

    @patch("app.tasks.extraction.extract_metadata")
    def test_duplicate_url_returns_409_with_existing_id(
        self, mock_task, client, auth_headers
    ):
        url = "https://example.com/duplicate-test"
        first = client.post("/content", json={"url": url}, headers=auth_headers)
        assert first.status_code == 201
        existing_id = first.json()["id"]

        second = client.post("/content", json={"url": url}, headers=auth_headers)
        assert second.status_code == 409
        detail = second.json()["detail"]
        assert existing_id in str(detail)

    def test_other_user_cannot_retrieve_item(
        self, client, auth_headers, other_auth_headers, test_content
    ):
        resp = client.get(f"/content/{test_content.id}", headers=other_auth_headers)
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Flow 2: Search
# ---------------------------------------------------------------------------


class TestSearch:
    """User searches their library → relevant results appear and stay user-scoped."""

    def test_keyword_search_finds_matching_article(
        self, client, auth_headers, searchable_article
    ):
        resp = client.get(
            "/search/semantic?query=React+Hooks",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response shape: {"articles": [{item: {...}, ...}], "highlights": [...]}
        assert "articles" in data
        ids = [r["item"]["id"] for r in data["articles"]]
        assert str(searchable_article.id) in ids

    def test_search_returns_200_for_authenticated_user(self, client, auth_headers):
        resp = client.get("/search/semantic?query=anything", headers=auth_headers)
        assert resp.status_code == 200
        assert "articles" in resp.json()

    def test_search_requires_authentication(self, client):
        resp = client.get("/search/semantic?query=test")
        assert resp.status_code == 401

    def test_search_does_not_return_other_users_content(
        self,
        client,
        auth_headers,
        other_auth_headers,
        searchable_article,
    ):
        """searchable_article belongs to test_user; other_user should not see it."""
        resp = client.get(
            "/search/semantic?query=React+Hooks",
            headers=other_auth_headers,
        )
        assert resp.status_code == 200
        ids = [r["item"]["id"] for r in resp.json()["articles"]]
        assert str(searchable_article.id) not in ids


# ---------------------------------------------------------------------------
# Flow 3: Highlights
# ---------------------------------------------------------------------------


class TestHighlights:
    """User highlights text → highlight saved and retrievable; user-scoped."""

    def test_create_highlight_on_article(self, client, auth_headers, test_content):
        resp = client.post(
            f"/content/{test_content.id}/highlights",
            json={
                "text": "test article with enough",
                "start_offset": 10,
                "end_offset": 35,
                "color": "yellow",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "test article with enough"
        assert data["color"] == "yellow"

    def test_get_highlights_for_article(self, client, auth_headers, test_content):
        # Create a highlight first
        client.post(
            f"/content/{test_content.id}/highlights",
            json={
                "text": "enough content",
                "start_offset": 18,
                "end_offset": 32,
                "color": "blue",
            },
            headers=auth_headers,
        )
        resp = client.get(
            f"/content/{test_content.id}/highlights",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1

    def test_other_user_cannot_see_highlights(
        self, client, auth_headers, other_auth_headers, test_content
    ):
        client.post(
            f"/content/{test_content.id}/highlights",
            json={
                "text": "enough content",
                "start_offset": 18,
                "end_offset": 32,
                "color": "green",
            },
            headers=auth_headers,
        )
        resp = client.get(
            f"/content/{test_content.id}/highlights",
            headers=other_auth_headers,
        )
        # Either 403/404 (can't access the article) or empty list
        if resp.status_code == 200:
            assert resp.json() == []
        else:
            assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Flow 4: Lists
# ---------------------------------------------------------------------------


class TestLists:
    """User creates a list and adds articles to it; lists are user-scoped."""

    def test_create_list(self, client, auth_headers):
        resp = client.post(
            "/lists",
            json={"name": "My Reading List", "description": "For later"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Reading List"
        assert "id" in data

    def test_add_article_to_list(self, client, auth_headers, test_content):
        list_resp = client.post(
            "/lists",
            json={"name": "Test List"},
            headers=auth_headers,
        )
        assert list_resp.status_code == 201
        list_id = list_resp.json()["id"]

        add_resp = client.post(
            f"/lists/{list_id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )
        assert add_resp.status_code == 200

    def test_list_content_not_visible_to_other_user(
        self, client, auth_headers, other_auth_headers
    ):
        list_resp = client.post(
            "/lists",
            json={"name": "Private List"},
            headers=auth_headers,
        )
        list_id = list_resp.json()["id"]

        resp = client.get(f"/lists/{list_id}", headers=other_auth_headers)
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Flow 5: Auth gates
# ---------------------------------------------------------------------------


class TestAuthGates:
    """All protected endpoints require a valid auth token."""

    def test_content_list_requires_auth(self, client):
        assert client.get("/content").status_code == 401

    def test_content_create_requires_auth(self, client):
        assert client.post("/content", json={"url": "https://x.com"}).status_code == 401

    def test_lists_requires_auth(self, client):
        assert client.get("/lists").status_code == 401

    def test_invalid_token_is_rejected(self, client):
        resp = client.get(
            "/content",
            headers={"Authorization": "Bearer this.is.not.valid"},
        )
        assert resp.status_code == 401
