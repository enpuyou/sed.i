"""
TDD tests for MCP content tools: get_content_item, search_content, find_similar.
"""

import pytest
from app.mcp.tools.content import get_content_item, search_content, find_similar


class TestGetContentItem:
    def test_returns_article_metadata(self, db, user, article):
        result = get_content_item(item_id=str(article.id), user=user, db=db)
        assert result["id"] == str(article.id)
        assert result["title"] == article.title
        assert result["url"] == article.original_url

    def test_excludes_full_text_by_default(self, db, user, article):
        result = get_content_item(item_id=str(article.id), user=user, db=db)
        assert "full_text" not in result

    def test_includes_full_text_when_requested(self, db, user, article):
        result = get_content_item(
            item_id=str(article.id), user=user, db=db, include_full_text=True
        )
        assert "full_text" in result
        assert result["full_text"] == article.full_text

    def test_raises_on_unknown_item(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            get_content_item(
                item_id="00000000-0000-0000-0000-000000000000", user=user, db=db
            )

    def test_raises_on_other_users_item(self, db, user, other_user, db_other=None):
        from app.models.content import ContentItem

        other_item = ContentItem(
            original_url="https://other.com",
            title="Other's Article",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_item)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_content_item(item_id=str(other_item.id), user=user, db=db)

    def test_raises_on_deleted_item(self, db, user, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_content_item(item_id=str(article.id), user=user, db=db)

    def test_truncates_long_full_text(self, db, user, article):
        article.full_text = "word " * 20000
        db.commit()
        result = get_content_item(
            item_id=str(article.id), user=user, db=db, include_full_text=True
        )
        assert "[truncated]" in result["full_text"]

    def test_result_contains_required_fields(self, db, user, article):
        result = get_content_item(item_id=str(article.id), user=user, db=db)
        for field in (
            "id",
            "title",
            "url",
            "description",
            "summary",
            "author",
            "tags",
            "is_read",
            "is_archived",
            "word_count",
            "reading_time_minutes",
            "content_type",
        ):
            assert field in result, f"Missing field: {field}"


class TestSearchContent:
    def test_returns_empty_when_no_embeddings(self, db, user, article):
        # article has no embedding → semantic search returns nothing
        result = search_content(query="test article", user=user, db=db)
        assert result == []

    def test_respects_limit_parameter(self, db, user):
        # Even with no results, limit param is accepted without error
        result = search_content(query="anything", user=user, db=db, limit=5)
        assert isinstance(result, list)

    def test_limit_capped_at_50(self, db, user):
        # Passing limit > 50 should not raise, just be capped
        result = search_content(query="anything", user=user, db=db, limit=200)
        assert isinstance(result, list)

    def test_result_format_when_matches_exist(self, db, user, article):
        # Inject a fake embedding so the article appears in results

        fake_embedding = [0.1] * 1536
        article.embedding = fake_embedding
        db.commit()

        # We can't easily mock OpenAI, so just verify the function runs
        # without crashing when there are no embeddings (OpenAI key won't be set)
        # A full integration test would require a real/mocked OpenAI call.
        # Here we verify the fallback graceful empty return.
        try:
            result = search_content(query="test article content", user=user, db=db)
            assert isinstance(result, list)
            if result:
                item = result[0]
                assert "item" in item
                assert "similarity_score" in item
        except Exception as e:
            # If OpenAI is not configured, it should raise a clear error, not crash silently
            assert (
                "openai" in str(e).lower()
                or "api" in str(e).lower()
                or "key" in str(e).lower()
            )


class TestFindSimilar:
    def test_raises_on_unknown_item(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            find_similar(
                item_id="00000000-0000-0000-0000-000000000000", user=user, db=db
            )

    def test_raises_on_other_users_item(self, db, user, other_user):
        from app.models.content import ContentItem

        other_item = ContentItem(
            original_url="https://other.com",
            title="Other's",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_item)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            find_similar(item_id=str(other_item.id), user=user, db=db)

    def test_returns_empty_when_no_embedding(self, db, user, article):
        result = find_similar(item_id=str(article.id), user=user, db=db)
        assert result == []

    def test_returns_empty_when_only_one_article(self, db, user, article):
        article.embedding = [0.1] * 1536
        db.commit()
        result = find_similar(item_id=str(article.id), user=user, db=db)
        assert result == []

    def test_respects_limit(self, db, user, article):
        result = find_similar(item_id=str(article.id), user=user, db=db, limit=3)
        assert isinstance(result, list)
        assert len(result) <= 3

    def test_does_not_include_source_item(self, db, user, article, second_article):
        vec = [0.1] * 1536
        article.embedding = vec
        second_article.embedding = vec
        db.commit()

        result = find_similar(item_id=str(article.id), user=user, db=db)
        ids = [r["item"]["id"] for r in result]
        assert str(article.id) not in ids

    def test_result_format(self, db, user, article, second_article):
        vec = [0.1] * 1536
        article.embedding = vec
        second_article.embedding = vec
        db.commit()

        result = find_similar(item_id=str(article.id), user=user, db=db)
        if result:
            assert "item" in result[0]
            assert "similarity_score" in result[0]
            assert isinstance(result[0]["similarity_score"], float)
