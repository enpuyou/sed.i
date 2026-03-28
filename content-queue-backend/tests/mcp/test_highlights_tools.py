"""
TDD tests for MCP highlights tool: get_highlights.
Covers per-item, per-list, and global modes.
"""

import pytest
from app.mcp.tools.highlights import get_highlights
from app.models.highlight import Highlight


class TestGetHighlightsByItem:
    def test_returns_highlights_for_item(self, db, user, article, highlight):
        result = get_highlights(item_id=str(article.id), user=user, db=db)
        assert len(result) == 1
        assert result[0]["text"] == highlight.text

    def test_returns_empty_when_no_highlights(self, db, user, article):
        result = get_highlights(item_id=str(article.id), user=user, db=db)
        assert result == []

    def test_raises_on_other_users_item(self, db, user, other_user):
        from app.models.content import ContentItem

        other_item = ContentItem(
            original_url="https://other.com",
            title="Other",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_item)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_highlights(item_id=str(other_item.id), user=user, db=db)

    def test_does_not_return_other_users_highlights(
        self, db, user, other_user, article
    ):
        other_h = Highlight(
            content_item_id=article.id,
            user_id=other_user.id,
            text="other user highlight",
            start_offset=0,
            end_offset=5,
            color="blue",
        )
        db.add(other_h)
        db.commit()
        result = get_highlights(item_id=str(article.id), user=user, db=db)
        texts = [r["text"] for r in result]
        assert "other user highlight" not in texts

    def test_result_contains_required_fields(self, db, user, article, highlight):
        result = get_highlights(item_id=str(article.id), user=user, db=db)
        item = result[0]
        for field in ("id", "text", "note", "color", "article_id", "article_title"):
            assert field in item, f"Missing field: {field}"

    def test_includes_note(self, db, user, article, highlight):
        result = get_highlights(item_id=str(article.id), user=user, db=db)
        assert result[0]["note"] == highlight.note


class TestGetHighlightsByList:
    def test_returns_highlights_from_all_list_articles(
        self, db, user, list_with_articles, article, second_article
    ):
        h1 = Highlight(
            content_item_id=article.id,
            user_id=user.id,
            text="h1",
            start_offset=0,
            end_offset=2,
            color="yellow",
        )
        h2 = Highlight(
            content_item_id=second_article.id,
            user_id=user.id,
            text="h2",
            start_offset=0,
            end_offset=2,
            color="yellow",
        )
        db.add_all([h1, h2])
        db.commit()

        result = get_highlights(list_id=str(list_with_articles.id), user=user, db=db)
        assert len(result) == 2

    def test_raises_on_other_users_list(self, db, user, other_user):
        from app.models.list import List

        other_list = List(name="Other", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            get_highlights(list_id=str(other_list.id), user=user, db=db)

    def test_returns_empty_for_list_with_no_highlights(
        self, db, user, list_with_articles
    ):
        result = get_highlights(list_id=str(list_with_articles.id), user=user, db=db)
        assert result == []


class TestGetHighlightsGlobal:
    def test_returns_all_user_highlights(self, db, user, article, second_article):
        h1 = Highlight(
            content_item_id=article.id,
            user_id=user.id,
            text="h1",
            start_offset=0,
            end_offset=2,
            color="yellow",
        )
        h2 = Highlight(
            content_item_id=second_article.id,
            user_id=user.id,
            text="h2",
            start_offset=0,
            end_offset=2,
            color="yellow",
        )
        db.add_all([h1, h2])
        db.commit()

        result = get_highlights(user=user, db=db)
        assert len(result) == 2

    def test_global_capped_at_100(self, db, user, article):
        for i in range(110):
            db.add(
                Highlight(
                    content_item_id=article.id,
                    user_id=user.id,
                    text=f"highlight {i}",
                    start_offset=i,
                    end_offset=i + 5,
                    color="yellow",
                )
            )
        db.commit()
        result = get_highlights(user=user, db=db)
        assert len(result) <= 100

    def test_does_not_include_other_users_highlights(
        self, db, user, other_user, article
    ):
        own_h = Highlight(
            content_item_id=article.id,
            user_id=user.id,
            text="mine",
            start_offset=0,
            end_offset=4,
            color="yellow",
        )
        other_h = Highlight(
            content_item_id=article.id,
            user_id=other_user.id,
            text="theirs",
            start_offset=5,
            end_offset=11,
            color="blue",
        )
        db.add_all([own_h, other_h])
        db.commit()

        result = get_highlights(user=user, db=db)
        texts = [r["text"] for r in result]
        assert "mine" in texts
        assert "theirs" not in texts
