"""
Unit tests for lists API endpoints.

Tests cover complete list management functionality:
- Creating lists
- Getting all lists with content counts
- Getting specific list details
- Getting content items in a list
- Adding content to lists
- Removing content from lists
- Updating list properties
- Deleting lists
- Authorization and permission checks
"""

from uuid import uuid4


class TestCreateList:
    """Tests for POST /lists - Creating new lists"""

    def test_create_list_success(self, client, auth_headers):
        """
        Test successfully creating a list.

        Lists are user-created collections to organize content.
        """
        list_data = {
            "name": "Reading List",
            "description": "Articles to read",
            "is_shared": False,
        }

        response = client.post(
            "/lists",
            json=list_data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Reading List"
        assert data["description"] == "Articles to read"
        assert data["is_shared"] is False
        assert "id" in data
        assert "created_at" in data

    def test_create_list_minimal(self, client, auth_headers):
        """
        Test creating a list with minimal fields (just name).
        """
        list_data = {"name": "Minimal List"}

        response = client.post(
            "/lists",
            json=list_data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal List"
        assert data["description"] is None
        assert data["is_shared"] is False  # Default value

    def test_create_shared_list(self, client, auth_headers):
        """
        Test creating a shared list.

        Shared lists can potentially be viewed by other users.
        """
        list_data = {
            "name": "Public Resources",
            "is_shared": True,
        }

        response = client.post(
            "/lists",
            json=list_data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        assert response.json()["is_shared"] is True

    def test_create_list_unauthorized(self, client):
        """Test creating a list without authentication."""
        list_data = {"name": "Test List"}

        response = client.post("/lists", json=list_data)

        assert response.status_code == 401


class TestGetLists:
    """Tests for GET /lists - Listing all user's lists"""

    def test_get_lists_empty(self, client, auth_headers):
        """
        Test getting lists when user has none.

        Should return empty array.
        """
        response = client.get("/lists", headers=auth_headers)

        assert response.status_code == 200
        assert response.json() == []

    def test_get_lists_with_content_counts(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test getting lists with item counts.

        Each list should include a count of items it contains.
        """
        from app.models.list import List, content_list_membership
        from app.models.content import ContentItem

        # Create 2 lists
        list1 = List(name="List 1", owner_id=test_user.id)
        list2 = List(name="List 2", owner_id=test_user.id)
        db_session.add_all([list1, list2])
        db_session.commit()
        db_session.refresh(list1)
        db_session.refresh(list2)

        # Create 3 content items
        content1 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        content2 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/2"
        )
        content3 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/3"
        )
        db_session.add_all([content1, content2, content3])
        db_session.commit()

        # Add content to lists: list1 has 2 items, list2 has 1 item
        stmt1 = content_list_membership.insert().values(
            content_item_id=content1.id, list_id=list1.id, added_by=test_user.id
        )
        stmt2 = content_list_membership.insert().values(
            content_item_id=content2.id, list_id=list1.id, added_by=test_user.id
        )
        stmt3 = content_list_membership.insert().values(
            content_item_id=content3.id, list_id=list2.id, added_by=test_user.id
        )
        db_session.execute(stmt1)
        db_session.execute(stmt2)
        db_session.execute(stmt3)
        db_session.commit()

        # Get lists
        response = client.get("/lists", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Find lists and verify counts
        list1_data = next(lst for lst in data if lst["name"] == "List 1")
        list2_data = next(lst for lst in data if lst["name"] == "List 2")

        assert list1_data["content_count"] == 2
        assert list2_data["content_count"] == 1

    def test_get_lists_unauthorized(self, client):
        """Test getting lists without authentication."""
        response = client.get("/lists")
        assert response.status_code == 401


class TestGetList:
    """Tests for GET /lists/{list_id} - Getting specific list"""

    def test_get_list_includes_content_count_excluding_deleted(
        self, client, auth_headers, test_user, db_session
    ):
        """The list detail carries the total item count (the UI pages the
        items, so it cannot count them itself); soft-deleted items excluded."""
        from datetime import datetime, timezone

        from app.models.content import ContentItem
        from app.models.list import List, content_list_membership

        test_list = List(name="Counted", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)
        for i in range(3):
            item = ContentItem(
                user_id=test_user.id,
                original_url=f"https://example.com/count-{i}",
                deleted_at=datetime.now(timezone.utc) if i == 2 else None,
            )
            db_session.add(item)
            db_session.commit()
            db_session.refresh(item)
            db_session.execute(
                content_list_membership.insert().values(
                    content_item_id=item.id,
                    list_id=test_list.id,
                    added_by=test_user.id,
                )
            )
        db_session.commit()

        response = client.get(f"/lists/{test_list.id}", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["content_count"] == 2
        # Existing fields unchanged.
        assert data["name"] == "Counted"
        assert {"id", "owner_id", "is_shared", "created_at", "updated_at"} <= set(data)

    def test_get_list_content_count_zero_for_empty_list(
        self, client, auth_headers, test_user, db_session
    ):
        from app.models.list import List

        test_list = List(name="Empty", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.get(f"/lists/{test_list.id}", headers=auth_headers)

        assert response.json()["content_count"] == 0

    def test_get_list_success(self, client, auth_headers, test_user, db_session):
        """
        Test getting a specific list's details.
        """
        from app.models.list import List

        test_list = List(
            name="Test List",
            description="A test list",
            owner_id=test_user.id,
        )
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.get(
            f"/lists/{test_list.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_list.id)
        assert data["name"] == "Test List"
        assert data["description"] == "A test list"

    def test_get_list_not_found(self, client, auth_headers):
        """Test getting non-existent list."""
        fake_id = uuid4()

        response = client.get(
            f"/lists/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_list_unauthorized(self, client, test_user, db_session):
        """Test getting list without authentication."""
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()

        response = client.get(f"/lists/{test_list.id}")
        assert response.status_code == 401


class TestGetListContent:
    """Tests for GET /lists/{list_id}/content - Getting items in a list"""

    def test_get_list_content_empty(self, client, auth_headers, test_user, db_session):
        """
        Test getting content from an empty list.

        Should return empty array.
        """
        from app.models.list import List

        test_list = List(name="Empty List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_get_list_content_multiple_items(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test getting multiple content items from a list.

        Items should be ordered by created_at descending.
        """
        from app.models.list import List, content_list_membership
        from app.models.content import ContentItem

        # Create list and content
        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        content1 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        content2 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/2"
        )
        content3 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/3"
        )
        db_session.add_all([content1, content2, content3])
        db_session.commit()

        # Add all to list
        for content in [content1, content2, content3]:
            stmt = content_list_membership.insert().values(
                content_item_id=content.id,
                list_id=test_list.id,
                added_by=test_user.id,
            )
            db_session.execute(stmt)
        db_session.commit()

        # Get list content
        response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

        # Verify all items present
        urls = [item["original_url"] for item in data]
        assert "https://example.com/1" in urls
        assert "https://example.com/2" in urls
        assert "https://example.com/3" in urls

    def test_get_list_content_excludes_deleted(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test that deleted content doesn't appear in list.

        Even if content is in a list, soft-deleted items are excluded.
        """
        from app.models.list import List, content_list_membership
        from app.models.content import ContentItem
        from datetime import datetime

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        content1 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        content2 = ContentItem(
            user_id=test_user.id,
            original_url="https://example.com/2",
            deleted_at=datetime.utcnow(),  # Soft deleted
        )
        db_session.add_all([content1, content2])
        db_session.commit()

        # Add both to list
        for content in [content1, content2]:
            stmt = content_list_membership.insert().values(
                content_item_id=content.id,
                list_id=test_list.id,
                added_by=test_user.id,
            )
            db_session.execute(stmt)
        db_session.commit()

        # Get list content
        response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1  # Only non-deleted item
        assert data[0]["original_url"] == "https://example.com/1"


class TestAddContentToList:
    """Tests for POST /lists/{list_id}/content - Adding items to list"""

    def test_add_single_content_to_list(
        self, client, auth_headers, test_user, test_content, db_session
    ):
        """
        Test adding a single content item to a list.
        """
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        # Add content to list
        response = client.post(
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "Added 1 items to list" in response.json()["message"]

        # Verify content is in list
        get_response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )
        assert len(get_response.json()) == 1
        assert get_response.json()[0]["id"] == str(test_content.id)

    def test_add_multiple_content_to_list(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test adding multiple content items to a list in one request.
        """
        from app.models.list import List
        from app.models.content import ContentItem

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)

        content1 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        content2 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/2"
        )
        db_session.add_all([content1, content2])
        db_session.commit()
        db_session.refresh(test_list)
        db_session.refresh(content1)
        db_session.refresh(content2)

        # Add both to list
        response = client.post(
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(content1.id), str(content2.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "Added 2 items to list" in response.json()["message"]

        # Verify both are in list
        get_response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )
        assert len(get_response.json()) == 2

    def test_add_content_already_in_list(
        self, client, auth_headers, test_user, test_content, db_session
    ):
        """
        Test adding content that's already in the list.

        Should be idempotent - no duplicate entries.
        """
        from app.models.list import List, content_list_membership

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        # Add content to list first time
        stmt = content_list_membership.insert().values(
            content_item_id=test_content.id,
            list_id=test_list.id,
            added_by=test_user.id,
        )
        db_session.execute(stmt)
        db_session.commit()

        # Try to add again
        response = client.post(
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200

        # Verify no duplicate
        get_response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )
        assert len(get_response.json()) == 1

    def test_add_content_list_not_found(self, client, auth_headers, test_content):
        """Test adding content to non-existent list."""
        fake_id = uuid4()

        response = client.post(
            f"/lists/{fake_id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_add_content_not_found(self, client, auth_headers, test_user, db_session):
        """Test adding non-existent content to list."""
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        fake_content_id = uuid4()

        response = client.post(
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(fake_content_id)]},
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestRemoveContentFromList:
    """Tests for DELETE /lists/{list_id}/content - Removing items from list"""

    def test_remove_content_from_list(
        self, client, auth_headers, test_user, test_content, db_session
    ):
        """
        Test removing content from a list.

        Content item itself is not deleted, just the membership.
        """
        from app.models.list import List, content_list_membership

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        # Add content to list
        stmt = content_list_membership.insert().values(
            content_item_id=test_content.id,
            list_id=test_list.id,
            added_by=test_user.id,
        )
        db_session.execute(stmt)
        db_session.commit()

        # Remove from list
        response = client.request(
            "DELETE",
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "Removed 1 items from list" in response.json()["message"]

        # Verify removed
        get_response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )
        assert len(get_response.json()) == 0

        # Verify content itself still exists
        content_response = client.get(
            f"/content/{test_content.id}",
            headers=auth_headers,
        )
        assert content_response.status_code == 200

    def test_remove_multiple_content_from_list(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test removing multiple items from a list.
        """
        from app.models.list import List, content_list_membership
        from app.models.content import ContentItem

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)

        content1 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        content2 = ContentItem(
            user_id=test_user.id, original_url="https://example.com/2"
        )
        db_session.add_all([content1, content2])
        db_session.commit()
        db_session.refresh(test_list)

        # Add both to list
        for content in [content1, content2]:
            stmt = content_list_membership.insert().values(
                content_item_id=content.id,
                list_id=test_list.id,
                added_by=test_user.id,
            )
            db_session.execute(stmt)
        db_session.commit()

        # Remove both
        response = client.request(
            "DELETE",
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(content1.id), str(content2.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "Removed 2 items from list" in response.json()["message"]

        # Verify list is empty
        get_response = client.get(
            f"/lists/{test_list.id}/content",
            headers=auth_headers,
        )
        assert len(get_response.json()) == 0

    def test_remove_content_not_in_list(
        self, client, auth_headers, test_user, test_content, db_session
    ):
        """
        Test removing content that's not in the list.

        Should succeed (idempotent operation).
        """
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        # Try to remove (content was never added)
        response = client.request(
            "DELETE",
            f"/lists/{test_list.id}/content",
            json={"content_item_ids": [str(test_content.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200


class TestUpdateList:
    """Tests for PATCH /lists/{list_id} - Updating list properties"""

    def test_update_list_name(self, client, auth_headers, test_user, db_session):
        """
        Test updating a list's name.
        """
        from app.models.list import List

        test_list = List(name="Old Name", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.patch(
            f"/lists/{test_list.id}",
            json={"name": "New Name"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    def test_update_list_description(self, client, auth_headers, test_user, db_session):
        """
        Test updating a list's description.
        """
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.patch(
            f"/lists/{test_list.id}",
            json={"description": "Updated description"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["description"] == "Updated description"

    def test_update_list_sharing_status(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test changing a list's sharing status.
        """
        from app.models.list import List

        test_list = List(name="Test List", owner_id=test_user.id, is_shared=False)
        db_session.add(test_list)
        db_session.commit()
        db_session.refresh(test_list)

        response = client.patch(
            f"/lists/{test_list.id}",
            json={"is_shared": True},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["is_shared"] is True

    def test_update_list_not_found(self, client, auth_headers):
        """Test updating non-existent list."""
        fake_id = uuid4()

        response = client.patch(
            f"/lists/{fake_id}",
            json={"name": "New Name"},
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestDeleteList:
    """Tests for DELETE /lists/{list_id} - Deleting lists"""

    def test_delete_list_success(self, client, auth_headers, test_user, db_session):
        """
        Test deleting a list.

        This should:
        - Delete the list
        - Delete list memberships (cascade)
        - NOT delete the content items themselves
        """
        from app.models.list import List, content_list_membership
        from app.models.content import ContentItem

        test_list = List(name="Test List", owner_id=test_user.id)
        db_session.add(test_list)

        content = ContentItem(
            user_id=test_user.id, original_url="https://example.com/1"
        )
        db_session.add(content)
        db_session.commit()
        db_session.refresh(test_list)
        db_session.refresh(content)

        # Add content to list
        stmt = content_list_membership.insert().values(
            content_item_id=content.id, list_id=test_list.id, added_by=test_user.id
        )
        db_session.execute(stmt)
        db_session.commit()

        # Delete list
        response = client.delete(
            f"/lists/{test_list.id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

        # Verify list is gone
        get_response = client.get(
            f"/lists/{test_list.id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

        # Verify content still exists
        content_response = client.get(
            f"/content/{content.id}",
            headers=auth_headers,
        )
        assert content_response.status_code == 200

    def test_delete_list_not_found(self, client, auth_headers):
        """Test deleting non-existent list."""
        fake_id = uuid4()

        response = client.delete(
            f"/lists/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestListsAuthorization:
    """Tests for authorization and permission checks"""

    def test_user_cannot_access_other_users_lists(self, client, db_session, test_user):
        """
        Test that users can only access their own lists.
        """
        from app.models.user import User
        from app.models.list import List
        from app.core.security import get_password_hash, create_access_token

        # Create another user with a list
        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_list = List(name="Other's List", owner_id=other_user.id)
        db_session.add(other_list)
        db_session.commit()
        db_session.refresh(other_list)

        # Try to access as test_user
        test_token = create_access_token(data={"sub": test_user.email})
        test_headers = {"Authorization": f"Bearer {test_token}"}

        response = client.get(
            f"/lists/{other_list.id}",
            headers=test_headers,
        )

        assert response.status_code == 404

    def test_user_cannot_modify_other_users_lists(self, client, db_session, test_user):
        """
        Test that users cannot modify other users' lists.
        """
        from app.models.user import User
        from app.models.list import List
        from app.core.security import get_password_hash, create_access_token

        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_list = List(name="Other's List", owner_id=other_user.id)
        db_session.add(other_list)
        db_session.commit()
        db_session.refresh(other_list)

        test_token = create_access_token(data={"sub": test_user.email})
        test_headers = {"Authorization": f"Bearer {test_token}"}

        # Try to update
        response = client.patch(
            f"/lists/{other_list.id}",
            json={"name": "Hacked Name"},
            headers=test_headers,
        )

        assert response.status_code == 404

        # Try to delete
        response = client.delete(
            f"/lists/{other_list.id}",
            headers=test_headers,
        )

        assert response.status_code == 404

    def test_get_lists_only_shows_own(
        self, client, auth_headers, test_user, db_session
    ):
        """
        Test that listing lists only shows user's own lists.
        """
        from app.models.user import User
        from app.models.list import List
        from app.core.security import get_password_hash

        # Create list for test_user
        user_list = List(name="My List", owner_id=test_user.id)
        db_session.add(user_list)

        # Create another user with a list
        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password"),
        )
        db_session.add(other_user)
        db_session.commit()

        other_list = List(name="Other's List", owner_id=other_user.id)
        db_session.add(other_list)
        db_session.commit()

        # Get lists as test_user
        response = client.get("/lists", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "My List"
