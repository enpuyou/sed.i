"""
TDD tests for MCP lists tools: list_lists, get_list_content.
"""

import pytest
from app.mcp.tools.lists import list_lists, get_list_content
from app.models.list import List


class TestListLists:
    def test_returns_user_lists(self, db, user, reading_list):
        result = list_lists(user=user, db=db)
        assert len(result) == 1
        assert result[0]["name"] == "My List"
        assert result[0]["description"] == "Test list"
        assert "id" in result[0]
        assert "item_count" in result[0]

    def test_returns_empty_when_no_lists(self, db, user):
        result = list_lists(user=user, db=db)
        assert result == []

    def test_does_not_return_other_users_lists(
        self, db, user, other_user, reading_list
    ):
        other_list = List(name="Other's List", owner_id=other_user.id)
        db.add(other_list)
        db.commit()

        result = list_lists(user=user, db=db)
        names = [r["name"] for r in result]
        assert "My List" in names
        assert "Other's List" not in names

    def test_returns_multiple_lists(self, db, user):
        for name in ["List A", "List B", "List C"]:
            db.add(List(name=name, owner_id=user.id))
        db.commit()

        result = list_lists(user=user, db=db)
        assert len(result) == 3

    def test_includes_item_count(self, db, user, list_with_articles):
        result = list_lists(user=user, db=db)
        assert result[0]["item_count"] == 2

    def test_item_count_excludes_deleted(self, db, user, list_with_articles, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()

        result = list_lists(user=user, db=db)
        assert result[0]["item_count"] == 1

    def test_result_contains_required_fields(self, db, user, reading_list):
        result = list_lists(user=user, db=db)
        item = result[0]
        for field in ("id", "name", "description", "item_count", "created_at"):
            assert field in item, f"Missing field: {field}"


class TestGetListContent:
    def test_returns_articles_in_list(self, db, user, list_with_articles):
        result = get_list_content(list_id=str(list_with_articles.id), user=user, db=db)
        assert len(result) == 2

    def test_raises_on_unknown_list(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            get_list_content(
                list_id="00000000-0000-0000-0000-000000000000", user=user, db=db
            )

    def test_raises_on_other_users_list(self, db, user, other_user, db_other_list=None):
        other_list = List(name="Theirs", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_list_content(list_id=str(other_list.id), user=user, db=db)

    def test_excludes_full_text_by_default(self, db, user, list_with_articles):
        result = get_list_content(list_id=str(list_with_articles.id), user=user, db=db)
        for item in result:
            assert "full_text" not in item

    def test_includes_full_text_when_requested(self, db, user, list_with_articles):
        result = get_list_content(
            list_id=str(list_with_articles.id),
            user=user,
            db=db,
            include_full_text=True,
        )
        for item in result:
            assert "full_text" in item
            assert item["full_text"] is not None

    def test_respects_limit(self, db, user, list_with_articles):
        result = get_list_content(
            list_id=str(list_with_articles.id), user=user, db=db, limit=1
        )
        assert len(result) == 1

    def test_excludes_deleted_articles(self, db, user, list_with_articles, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()

        result = get_list_content(list_id=str(list_with_articles.id), user=user, db=db)
        assert len(result) == 1
        assert result[0]["title"] != article.title

    def test_result_contains_required_fields(self, db, user, list_with_articles):
        result = get_list_content(list_id=str(list_with_articles.id), user=user, db=db)
        item = result[0]
        for field in (
            "id",
            "title",
            "url",
            "summary",
            "tags",
            "is_read",
            "reading_time_minutes",
        ):
            assert field in item, f"Missing field: {field}"

    def test_truncates_full_text_over_limit(
        self, db, user, list_with_articles, article
    ):
        # Assign a very long full_text
        article.full_text = "word " * 20000  # well over 8k tokens
        db.commit()

        result = get_list_content(
            list_id=str(list_with_articles.id),
            user=user,
            db=db,
            include_full_text=True,
        )
        long_item = next(r for r in result if r["id"] == str(article.id))
        assert "[truncated]" in long_item["full_text"]
