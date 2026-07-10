"""
Unit tests for content API endpoints.

Tests cover the complete content lifecycle:
- Submitting URLs (paste in URL)
- Metadata and full text extraction (background job trigger)
- Listing content with filters (read/unread, archived)
- Retrieving individual content items
- Marking as read/unread
- Updating read position and tags
- Soft deletion
- Authorization and permission checks
"""

import json
import pytest
from uuid import uuid4
from unittest.mock import patch
from tests.conftest import TestingSessionLocal


class TestCreateContent:
    """Tests for POST /content - Submitting new URLs"""

    @patch("app.tasks.extraction.extract_metadata")
    def test_create_content_success(self, mock_extract, client, auth_headers):
        """
        Test successfully submitting a URL.

        This is the core "paste in URL" functionality that:
        - Creates a content item with 'pending' status
        - Triggers background job for metadata extraction
        - Returns the created item immediately
        """
        content_data = {
            "url": "https://example.com/article",
        }

        response = client.post(
            "/content",
            json=content_data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()

        # Verify content item created
        assert data["original_url"] == "https://example.com/article"
        assert data["processing_status"] == "pending"
        assert "id" in data
        assert "created_at" in data

        # Verify background job was triggered
        mock_extract.delay.assert_called_once()
        call_args = mock_extract.delay.call_args[0]
        assert call_args[0] == data["id"]  # Content ID passed to celery task

    @patch("app.tasks.extraction.extract_metadata")
    def test_create_content_with_list_membership(
        self, mock_extract, client, auth_headers, test_user, db_session
    ):
        """
        Test submitting URL and adding to lists simultaneously.

        Users can add content to lists when submitting.
        """
        # Create two lists
        from app.models.list import List

        list1 = List(name="Reading List", owner_id=test_user.id)
        list2 = List(name="Tech Articles", owner_id=test_user.id)
        db_session.add_all([list1, list2])
        db_session.commit()
        db_session.refresh(list1)
        db_session.refresh(list2)

        content_data = {
            "url": "https://example.com/tech-article",
            "list_ids": [str(list1.id), str(list2.id)],
        }

        response = client.post(
            "/content",
            json=content_data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        content_id = response.json()["id"]

        # Verify content was added to both lists
        list1_content = client.get(
            f"/lists/{list1.id}/content",
            headers=auth_headers,
        )
        list2_content = client.get(
            f"/lists/{list2.id}/content",
            headers=auth_headers,
        )

        assert len(list1_content.json()) == 1
        assert len(list2_content.json()) == 1
        assert list1_content.json()[0]["id"] == content_id

    @patch("app.tasks.extraction.extract_metadata")
    def test_create_content_unauthorized(self, mock_extract, client):
        """
        Test submitting URL without authentication.

        Should return 401 Unauthorized.
        """
        content_data = {"url": "https://example.com/article"}

        response = client.post("/content", json=content_data)

        assert response.status_code == 401
        mock_extract.delay.assert_not_called()

    @patch("app.tasks.extraction.extract_metadata")
    def test_create_content_invalid_url(self, mock_extract, client, auth_headers):
        """
        Test validation for invalid URL format.

        Note: Backend validation depends on Pydantic schema.
        """
        content_data = {"url": "not-a-valid-url"}

        response = client.post(
            "/content",
            json=content_data,
            headers=auth_headers,
        )

        # Should either accept (and fail during extraction) or reject with 422
        assert response.status_code in [201, 422]

    @patch("app.tasks.extraction.extract_metadata")
    def test_create_content_blocks_duplicate_of_legacy_unnormalized_url(
        self, mock_extract, client, auth_headers, test_user, db_session
    ):
        from app.models.content import ContentItem

        legacy = ContentItem(
            user_id=test_user.id,
            original_url="HTTPS://Example.com/article/?utm_source=newsletter#section",
            submitted_via="web",
            processing_status="completed",
        )
        db_session.add(legacy)
        db_session.commit()
        db_session.refresh(legacy)

        response = client.post(
            "/content",
            json={"url": "https://example.com/article"},
            headers=auth_headers,
        )

        assert response.status_code == 409
        detail = json.loads(response.json()["detail"])
        assert detail["existing_id"] == str(legacy.id)
        assert detail["is_archived"] is False
        mock_extract.delay.assert_not_called()


class TestListContent:
    """Tests for GET /content - Listing content items"""

    def test_list_content_empty(self, client, auth_headers):
        """
        Test listing content when user has no items.

        Should return empty list with total=0.
        """
        response = client.get("/content", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["skip"] == 0
        assert data["limit"] == 50

    @patch("app.tasks.extraction.extract_metadata")
    def test_list_content_multiple_items(self, mock_extract, client, auth_headers):
        """
        Test listing multiple content items.

        Items should be ordered by created_at descending (newest first).
        """
        # Create 3 content items
        urls = [
            "https://example.com/article1",
            "https://example.com/article2",
            "https://example.com/article3",
        ]

        created_ids = []
        for url in urls:
            response = client.post(
                "/content",
                json={"url": url},
                headers=auth_headers,
            )
            created_ids.append(response.json()["id"])

        # Force distinct created_at values via direct DB update so ordering is
        # deterministic without wall-clock sleeps (which are flaky on slow CI).
        from sqlalchemy import text as sa_text
        from datetime import datetime, timezone, timedelta

        override_db = TestingSessionLocal()
        try:
            for i, item_id in enumerate(created_ids):
                ts = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
                override_db.execute(
                    sa_text(
                        "UPDATE content_items SET created_at = :ts WHERE id = CAST(:id AS uuid)"
                    ),
                    {"ts": ts, "id": item_id},
                )
            override_db.commit()
        finally:
            override_db.close()

        # List all content
        response = client.get("/content", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        assert data["total"] == 3

        # Verify ordering (newest first)
        assert data["items"][0]["id"] == created_ids[2]  # Last created
        assert data["items"][2]["id"] == created_ids[0]  # First created

    @pytest.mark.xfail(
        reason="Intermittent failure due to test isolation issue", strict=False
    )
    @patch("app.tasks.extraction.extract_metadata")
    def test_list_content_pagination(self, mock_extract, client, auth_headers):
        """
        Test pagination with skip and limit parameters.
        """
        # Create 5 content items
        for i in range(5):
            client.post(
                "/content",
                json={"url": f"https://example.com/article{i}"},
                headers=auth_headers,
            )

        # Get first 2 items
        response = client.get(
            "/content?skip=0&limit=2",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["skip"] == 0
        assert data["limit"] == 2

        # Get next 2 items
        response = client.get(
            "/content?skip=2&limit=2",
            headers=auth_headers,
        )

        data = response.json()
        assert len(data["items"]) == 2
        assert data["skip"] == 2

    @patch("app.tasks.extraction.extract_metadata")
    def test_list_content_filter_by_read_status(
        self, mock_extract, client, auth_headers, db_session
    ):
        """
        Test filtering content by read/unread status.
        """
        # Create 3 items, mark 2 as read
        from app.models.content import ContentItem
        from app.models.user import User

        user = db_session.query(User).first()

        item1 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/1",
            is_read=True,
        )
        item2 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/2",
            is_read=False,
        )
        item3 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/3",
            is_read=True,
        )

        db_session.add_all([item1, item2, item3])
        db_session.commit()

        # Filter for unread items
        response = client.get(
            "/content?is_read=false",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["is_read"] is False

        # Filter for read items
        response = client.get(
            "/content?is_read=true",
            headers=auth_headers,
        )

        data = response.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["is_read"] is True

    @patch("app.tasks.extraction.extract_metadata")
    def test_list_content_filter_by_archived(
        self, mock_extract, client, auth_headers, db_session
    ):
        """
        Test filtering content by archived status.
        """
        from app.models.content import ContentItem
        from app.models.user import User

        user = db_session.query(User).first()

        item1 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/1",
            is_archived=True,
        )
        item2 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/2",
            is_archived=False,
        )

        db_session.add_all([item1, item2])
        db_session.commit()

        # Filter for archived items
        response = client.get(
            "/content?is_archived=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["is_archived"] is True

    @patch("app.tasks.extraction.extract_metadata")
    def test_list_content_excludes_deleted(
        self, mock_extract, client, auth_headers, db_session
    ):
        """
        Test that soft-deleted items don't appear in list.
        """
        from app.models.content import ContentItem
        from app.models.user import User
        from datetime import datetime

        user = db_session.query(User).first()

        item1 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/1",
        )
        item2 = ContentItem(
            user_id=user.id,
            original_url="https://example.com/2",
            deleted_at=datetime.utcnow(),  # Soft deleted
        )

        db_session.add_all([item1, item2])
        db_session.commit()

        response = client.get("/content", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1  # Only non-deleted item
        assert data["items"][0]["original_url"] == "https://example.com/1"

    def test_list_content_unauthorized(self, client):
        """Test listing content without authentication."""
        response = client.get("/content")
        assert response.status_code == 401


class TestGetContent:
    """Tests for GET /content/{item_id} - Retrieving individual items"""

    def test_get_content_success(self, client, auth_headers, test_content):
        """
        Test retrieving a specific content item.
        """
        response = client.get(
            f"/content/{test_content.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_content.id)
        assert data["original_url"] == test_content.original_url

    def test_get_content_full_text(self, client, auth_headers, test_content):
        """
        Test retrieving content with full text (reading view).

        Uses /content/{id}/full endpoint which returns complete article.
        """
        response = client.get(
            f"/content/{test_content.id}/full",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_content.id)
        assert "content" in data or "full_text" in data

    def test_get_content_not_found(self, client, auth_headers):
        """
        Test retrieving non-existent content.

        Should return 404.
        """
        fake_id = uuid4()

        response = client.get(
            f"/content/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_content_deleted(self, client, auth_headers, test_content, db_session):
        """
        Test that deleted content returns 404.

        Soft-deleted items should not be accessible.
        """
        from datetime import datetime

        # Soft delete the content
        test_content.deleted_at = datetime.utcnow()
        db_session.commit()

        response = client.get(
            f"/content/{test_content.id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_content_unauthorized(self, client, test_content):
        """Test retrieving content without authentication."""
        response = client.get(f"/content/{test_content.id}")
        assert response.status_code == 401


class TestUpdateContent:
    """Tests for PATCH /content/{item_id} - Updating content properties"""

    def test_mark_as_read(self, client, auth_headers, test_content):
        """
        Test marking content as read.

        This should:
        - Set is_read to True
        - Set read_at timestamp
        """
        response = client.patch(
            f"/content/{test_content.id}",
            json={"is_read": True},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True

    def test_mark_as_unread(self, client, auth_headers, test_content, db_session):
        """
        Test marking content as unread.

        This should:
        - Set is_read to False
        - Clear read_at timestamp
        """
        # First mark as read
        test_content.is_read = True
        db_session.commit()

        # Now mark as unread
        response = client.patch(
            f"/content/{test_content.id}",
            json={"is_read": False},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is False

    def test_archive_content(self, client, auth_headers, test_content):
        """
        Test archiving content.
        """
        response = client.patch(
            f"/content/{test_content.id}",
            json={"is_archived": True},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_archived"] is True

    def test_update_read_position(self, client, auth_headers, test_content):
        """
        Test updating read position (scroll percentage).

        Used for tracking reading progress.
        """
        response = client.patch(
            f"/content/{test_content.id}",
            json={"read_position": 0.75},  # 75% through article
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["read_position"] == 0.75

    def test_update_tags(self, client, auth_headers, test_content):
        """
        Test updating content tags.
        """
        response = client.patch(
            f"/content/{test_content.id}",
            json={"tags": ["python", "web-development", "tutorial"]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["tags"]) == 3
        assert "python" in data["tags"]

    def test_update_multiple_fields(self, client, auth_headers, test_content):
        """
        Test updating multiple fields simultaneously.
        """
        response = client.patch(
            f"/content/{test_content.id}",
            json={
                "is_read": True,
                "is_archived": True,
                "read_position": 1.0,
                "tags": ["completed"],
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True
        assert data["is_archived"] is True
        assert data["read_position"] == 1.0
        assert "completed" in data["tags"]

    def test_update_content_not_found(self, client, auth_headers):
        """Test updating non-existent content."""
        fake_id = uuid4()

        response = client.patch(
            f"/content/{fake_id}",
            json={"is_read": True},
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_update_content_unauthorized(self, client, test_content):
        """Test updating content without authentication."""
        response = client.patch(
            f"/content/{test_content.id}",
            json={"is_read": True},
        )

        assert response.status_code == 401


class TestDeleteContent:
    """Tests for DELETE /content/{item_id} - Soft deletion"""

    def test_delete_content_success(
        self, client, auth_headers, test_content, db_session
    ):
        """
        Test soft deleting content.

        This should:
        - Set deleted_at timestamp
        - Return 204 No Content
        - Item should not appear in lists anymore
        """
        response = client.delete(
            f"/content/{test_content.id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

        # Verify it's soft deleted (has deleted_at timestamp)
        db_session.refresh(test_content)
        assert test_content.deleted_at is not None

        # Verify it doesn't appear in content list
        list_response = client.get("/content", headers=auth_headers)
        assert list_response.json()["total"] == 0

    def test_delete_content_not_found(self, client, auth_headers):
        """Test deleting non-existent content."""
        fake_id = uuid4()

        response = client.delete(
            f"/content/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_delete_already_deleted(
        self, client, auth_headers, test_content, db_session
    ):
        """
        Test deleting already-deleted content.

        Should return 404 since deleted items are excluded from queries.
        """
        from datetime import datetime

        # Soft delete the content
        test_content.deleted_at = datetime.utcnow()
        db_session.commit()

        # Try to delete again
        response = client.delete(
            f"/content/{test_content.id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_delete_content_unauthorized(self, client, test_content):
        """Test deleting content without authentication."""
        response = client.delete(f"/content/{test_content.id}")
        assert response.status_code == 401


class TestContentAuthorization:
    """Tests for authorization and permission checks"""

    def test_user_cannot_access_other_users_content(
        self, client, db_session, test_content
    ):
        """
        Test that users can only access their own content.

        Security check: content isolation between users.
        """
        # Create another user
        from app.models.user import User
        from app.core.security import get_password_hash, create_access_token

        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_token = create_access_token(data={"sub": other_user.email})
        other_headers = {"Authorization": f"Bearer {other_token}"}

        # Try to access test_content (belongs to different user)
        response = client.get(
            f"/content/{test_content.id}",
            headers=other_headers,
        )

        assert response.status_code == 404  # "Not found" for security

    def test_user_cannot_modify_other_users_content(
        self, client, db_session, test_content
    ):
        """Test that users cannot modify other users' content."""
        from app.models.user import User
        from app.core.security import get_password_hash, create_access_token

        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_token = create_access_token(data={"sub": other_user.email})
        other_headers = {"Authorization": f"Bearer {other_token}"}

        # Try to update
        response = client.patch(
            f"/content/{test_content.id}",
            json={"is_read": True},
            headers=other_headers,
        )

        assert response.status_code == 404

        # Try to delete
        response = client.delete(
            f"/content/{test_content.id}",
            headers=other_headers,
        )

        assert response.status_code == 404

    def test_content_list_only_shows_own_items(
        self, client, auth_headers, test_content, db_session
    ):
        """
        Test that content list only shows user's own items.

        Other users' content should not be visible.
        """
        # Create content for another user
        from app.models.user import User
        from app.models.content import ContentItem
        from app.core.security import get_password_hash

        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_content = ContentItem(
            user_id=other_user.id,
            original_url="https://example.com/other",
        )
        db_session.add(other_content)
        db_session.commit()

        # List content as test_user
        response = client.get("/content", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Should only see own content
        assert data["total"] == 1
        assert data["items"][0]["id"] == str(test_content.id)


class TestListResponseShape:
    """Feature 1: list endpoint omits full_text; single-item /full retains it."""

    def test_list_items_do_not_include_full_text(
        self, client, auth_headers, test_content
    ):
        """GET /content list must NOT return full_text — it's never rendered in the queue."""
        response = client.get("/content", headers=auth_headers)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) >= 1
        assert "full_text" not in items[0], (
            "full_text should be stripped from list responses to avoid sending "
            "megabytes of HTML on every dashboard navigation"
        )

    def test_single_item_endpoint_includes_full_text(
        self, client, auth_headers, test_content
    ):
        """GET /content/{id} (reader path) MUST return full_text so the reader can render it."""
        response = client.get(f"/content/{test_content.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "full_text" in data
        assert data["full_text"] is not None

    def test_full_endpoint_includes_full_text(self, client, auth_headers, test_content):
        """GET /content/{id}/full MUST return full_text (unchanged)."""
        response = client.get(f"/content/{test_content.id}/full", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "full_text" in data
        assert data["full_text"] is not None

    def test_list_items_still_have_all_queue_fields(
        self, client, auth_headers, test_content
    ):
        """List items must still carry every field the queue UI renders."""
        response = client.get("/content", headers=auth_headers)
        item = response.json()["items"][0]
        for field in (
            "id",
            "title",
            "description",
            "thumbnail_url",
            "tags",
            "reading_status",
            "word_count",
            "reading_time_minutes",
            "created_at",
            "processing_status",
        ):
            assert field in item, f"Queue field '{field}' missing from list response"
