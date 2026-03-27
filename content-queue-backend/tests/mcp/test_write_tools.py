"""
TDD tests for MCP write tools:
  update_draft, add_content, create_list, add_to_list.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.mcp.tools.write import update_draft, add_content, create_list, add_to_list


class TestUpdateDraft:
    def test_raises_on_unknown_list(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            update_draft(
                list_id="00000000-0000-0000-0000-000000000000",
                content="hello",
                user=user,
                db=db,
            )

    def test_raises_on_other_users_list(self, db, user, other_user):
        from app.models.list import List

        other_list = List(name="Other", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            update_draft(list_id=str(other_list.id), content="hello", user=user, db=db)

    def test_creates_draft_when_none_exists(self, db, user, reading_list):
        result = update_draft(
            list_id=str(reading_list.id),
            content="# New draft\n\nContent here.",
            user=user,
            db=db,
        )
        assert result["content"] == "# New draft\n\nContent here."
        assert result["word_count"] > 0
        assert "updated_at" in result

    def test_updates_existing_draft(self, db, user, reading_list, draft):
        result = update_draft(
            list_id=str(reading_list.id),
            content="Updated content.",
            user=user,
            db=db,
        )
        assert result["content"] == "Updated content."

    def test_title_is_optional(self, db, user, reading_list):
        # Without title: existing title preserved or empty
        result = update_draft(
            list_id=str(reading_list.id),
            content="Just content.",
            user=user,
            db=db,
        )
        assert "title" in result

    def test_title_is_updated_when_provided(self, db, user, reading_list, draft):
        result = update_draft(
            list_id=str(reading_list.id),
            content=draft.content,
            title="New Title",
            user=user,
            db=db,
        )
        assert result["title"] == "New Title"

    def test_word_count_is_recomputed(self, db, user, reading_list):
        result = update_draft(
            list_id=str(reading_list.id),
            content="one two three four five",
            user=user,
            db=db,
        )
        assert result["word_count"] == 5

    def test_returns_required_fields(self, db, user, reading_list):
        result = update_draft(
            list_id=str(reading_list.id),
            content="test",
            user=user,
            db=db,
        )
        for field in ("title", "content", "word_count", "updated_at"):
            assert field in result, f"Missing field: {field}"


class TestAddContent:
    def test_raises_on_invalid_url(self, db, user):
        with pytest.raises(ValueError, match="url"):
            add_content(url="not-a-url", user=user, db=db)

    def test_returns_item_id_and_status(self, db, user):
        with patch("app.mcp.tools.write.process_url_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-id")
            result = add_content(url="https://example.com/article", user=user, db=db)
        assert "item_id" in result
        assert result["status"] in ("queued", "processing", "exists")

    def test_returns_exists_for_duplicate_url(self, db, user, article):
        result = add_content(url=article.original_url, user=user, db=db)
        assert result["status"] == "exists"
        assert str(result["item_id"]) == str(article.id)

    def test_queues_celery_task_for_new_url(self, db, user):
        with patch("app.mcp.tools.write.process_url_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-id")
            result = add_content(url="https://example.com/brand-new", user=user, db=db)
        mock_task.delay.assert_called_once()
        assert result["status"] == "queued"


class TestCreateList:
    def test_raises_on_empty_name(self, db, user):
        with pytest.raises(ValueError, match="name"):
            create_list(name="", user=user, db=db)

    def test_creates_list_and_returns_id(self, db, user):
        result = create_list(name="My New List", user=user, db=db)
        assert "id" in result
        assert result["name"] == "My New List"

    def test_description_is_optional(self, db, user):
        result = create_list(name="List Without Description", user=user, db=db)
        assert result.get("description") is None or result.get("description") == ""

    def test_description_is_stored_when_provided(self, db, user):
        result = create_list(
            name="My List", description="A great list", user=user, db=db
        )
        assert result["description"] == "A great list"

    def test_returns_required_fields(self, db, user):
        result = create_list(name="Check Fields", user=user, db=db)
        for field in ("id", "name", "description", "created_at"):
            assert field in result, f"Missing field: {field}"

    def test_user_is_owner(self, db, user):
        from app.models.list import List

        result = create_list(name="Owner Check", user=user, db=db)
        lst = db.query(List).filter(List.id == result["id"]).first()
        assert lst is not None
        assert lst.owner_id == user.id


class TestAddToList:
    def test_raises_on_unknown_list(self, db, user, article):
        with pytest.raises(ValueError, match="not found"):
            add_to_list(
                list_id="00000000-0000-0000-0000-000000000000",
                item_id=str(article.id),
                user=user,
                db=db,
            )

    def test_raises_on_other_users_list(self, db, user, other_user, article):
        from app.models.list import List

        other_list = List(name="Other", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            add_to_list(
                list_id=str(other_list.id),
                item_id=str(article.id),
                user=user,
                db=db,
            )

    def test_raises_on_unknown_item(self, db, user, reading_list):
        with pytest.raises(ValueError, match="item"):
            add_to_list(
                list_id=str(reading_list.id),
                item_id="00000000-0000-0000-0000-000000000000",
                user=user,
                db=db,
            )

    def test_adds_item_to_list(self, db, user, reading_list, article):
        result = add_to_list(
            list_id=str(reading_list.id),
            item_id=str(article.id),
            user=user,
            db=db,
        )
        assert result["status"] == "added"
        assert str(result["item_id"]) == str(article.id)
        assert str(result["list_id"]) == str(reading_list.id)

    def test_idempotent_on_duplicate_add(self, db, user, list_with_articles, article):
        # article is already in list_with_articles
        result = add_to_list(
            list_id=str(list_with_articles.id),
            item_id=str(article.id),
            user=user,
            db=db,
        )
        assert result["status"] in ("added", "already_in_list")
