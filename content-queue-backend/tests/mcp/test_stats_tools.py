"""
TDD tests for MCP stats tool: get_reading_stats.
"""

from app.mcp.tools.stats import get_reading_stats
from app.models.content import ContentItem


class TestGetReadingStats:
    def test_returns_zero_stats_for_new_user(self, db, user):
        result = get_reading_stats(user=user, db=db)
        assert result["total_items"] == 0
        assert result["read_count"] == 0
        assert result["unread_count"] == 0

    def test_counts_total_items(self, db, user, article, second_article):
        result = get_reading_stats(user=user, db=db)
        assert result["total_items"] == 2

    def test_counts_read_and_unread(self, db, user, article, second_article):
        article.is_read = True
        db.commit()
        result = get_reading_stats(user=user, db=db)
        assert result["read_count"] == 1
        assert result["unread_count"] == 1

    def test_excludes_deleted_items(self, db, user, article, second_article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        result = get_reading_stats(user=user, db=db)
        assert result["total_items"] == 1

    def test_counts_archived_items(self, db, user, article, second_article):
        article.is_archived = True
        db.commit()
        result = get_reading_stats(user=user, db=db)
        assert result["archived_count"] == 1

    def test_does_not_include_other_users_items(self, db, user, other_user):
        other_item = ContentItem(
            original_url="https://other.com",
            title="Other's",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_item)
        db.commit()
        result = get_reading_stats(user=user, db=db)
        assert result["total_items"] == 0

    def test_result_contains_required_fields(self, db, user):
        result = get_reading_stats(user=user, db=db)
        for field in ("total_items", "read_count", "unread_count", "archived_count"):
            assert field in result, f"Missing field: {field}"
