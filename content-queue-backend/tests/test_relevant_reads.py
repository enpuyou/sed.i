"""
TDD tests for draft relevant-reads (Phase 4 — connections plan).

Behaviors tested:
- GET /lists/{list_id}/draft/relevant-reads returns items for a draft with ≥50 words
- Returns {items: []} for drafts with <50 words (no error)
- Returns {items: []} when draft has no content
- User A cannot receive User B's library items
- Requires authentication
"""

from app.models.content import ContentItem
from app.models.list import List
from app.models.draft import Draft
from app.core.security import create_access_token
from app.models.user import User


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _list_with_draft(db, user_id, draft_content: str) -> tuple:
    lst = List(name="Test List", owner_id=user_id)
    db.add(lst)
    db.flush()
    draft = Draft(list_id=lst.id, user_id=user_id, content=draft_content)
    db.add(draft)
    db.commit()
    db.refresh(lst)
    return lst, draft


def _article(db, user_id, title: str, tags: list[str]) -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{title.replace(' ', '-')}",
        title=title,
        user_id=user_id,
        processing_status="completed",
        embedding=[0.1] * 1536,
        tags=tags,
    )
    db.add(item)
    return item


class TestRelevantReads:
    def test_returns_items_key_in_response(
        self, client, db_session, test_user, auth_headers
    ):
        """Response always contains an 'items' key."""
        lst, _ = _list_with_draft(
            db_session,
            test_user.id,
            "machine learning " * 60,  # >50 words
        )
        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads", headers=auth_headers)
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_short_draft_returns_empty(
        self, client, db_session, test_user, auth_headers
    ):
        """Draft with fewer than 50 words returns {items: []}."""
        lst, _ = _list_with_draft(db_session, test_user.id, "Just a few words here.")
        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_empty_draft_returns_empty(
        self, client, db_session, test_user, auth_headers
    ):
        """Draft with no content returns {items: []}."""
        lst, _ = _list_with_draft(db_session, test_user.id, "")
        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_requires_auth(self, client, db_session, test_user):
        """Returns 401 without auth token."""
        lst, _ = _list_with_draft(db_session, test_user.id, "some content here")
        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads")
        assert resp.status_code == 401

    def test_cross_user_isolation(self, client, db_session, test_user, auth_headers):
        """User A cannot get User B's articles in relevant-reads."""
        other = User(
            email="other2@example.com",
            username="other2",
            hashed_password="x",
            full_name="Other2",
        )
        db_session.add(other)
        db_session.commit()

        # User B has a relevant article
        other_article = _article(
            db_session, other.id, "Other User Article", ["machine learning"]
        )
        # User A has a draft
        lst, _ = _list_with_draft(db_session, test_user.id, "machine learning " * 60)
        db_session.commit()

        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads", headers=auth_headers)
        assert resp.status_code == 200
        item_ids = [item["id"] for item in resp.json()["items"]]
        assert str(other_article.id) not in item_ids

    def test_result_items_have_required_fields(
        self, client, db_session, test_user, auth_headers
    ):
        """Each result item has id, title, and tags fields."""
        _article(
            db_session,
            test_user.id,
            "Deep Learning Fundamentals",
            ["machine learning", "gradient descent"],
        )
        lst, _ = _list_with_draft(
            db_session,
            test_user.id,
            "deep learning and machine learning concepts " * 15,
        )
        db_session.commit()

        resp = client.get(f"/lists/{lst.id}/draft/relevant-reads", headers=auth_headers)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "id" in item
            assert "title" in item
            assert "tags" in item
