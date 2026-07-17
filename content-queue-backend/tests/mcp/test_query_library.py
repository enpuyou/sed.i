"""
Tests for the query_library MCP tool.

Covers two layers:
1. SQL validation / security (pure unit — no DB, no LLM)
2. End-to-end execution against real DB with mocked LLM (no real API calls)

The security layer is most critical: query_library generates SQL from LLM output
and executes it. These tests verify the allow-list, read-only, and user-isolation
gates actually block malicious or incorrect SQL.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.mcp.tools.query import (
    _validate_sql,
    _enforce_user_isolation,
    query_library,
)


# ---------------------------------------------------------------------------
# SQL validation — allow-list and read-only enforcement
# ---------------------------------------------------------------------------


class TestValidateSql:
    def test_plain_select_is_allowed(self):
        sql = "SELECT id, title FROM content_items WHERE user_id = :user_id"
        result = _validate_sql(sql)
        assert "content_items" in result

    def test_disallowed_table_is_rejected(self):
        sql = "SELECT * FROM users WHERE id = :user_id"
        with pytest.raises(ValueError, match="disallowed"):
            _validate_sql(sql)

    def test_multiple_statements_rejected(self):
        # sqlglot parse returns > 1 statement → rejected
        sql = "SELECT 1; SELECT 2;"
        with pytest.raises(ValueError):
            _validate_sql(sql)

    def test_insert_rejected(self):
        sql = "INSERT INTO content_items (title) VALUES ('x')"
        with pytest.raises(ValueError):
            _validate_sql(sql)

    def test_update_rejected(self):
        sql = "UPDATE content_items SET title = 'x' WHERE user_id = :user_id"
        with pytest.raises(ValueError):
            _validate_sql(sql)

    def test_delete_rejected(self):
        sql = "DELETE FROM content_items WHERE user_id = :user_id"
        with pytest.raises(ValueError):
            _validate_sql(sql)

    def test_dml_cte_rejected(self):
        """DELETE hidden inside a CTE must be caught by AST walk."""
        sql = (
            "WITH d AS "
            "(DELETE FROM content_items WHERE user_id = :user_id RETURNING id) "
            "SELECT id FROM d"
        )
        with pytest.raises(ValueError, match="forbidden"):
            _validate_sql(sql)

    def test_join_allowed_tables_is_accepted(self):
        sql = """
        SELECT ci.title, h.text
        FROM content_items ci
        JOIN highlights h ON h.content_item_id = ci.id
        WHERE ci.user_id = :user_id AND h.user_id = :user_id
        """
        cleaned = _validate_sql(sql)
        assert cleaned  # returns non-empty string on success

    def test_internal_table_refresh_tokens_rejected(self):
        sql = "SELECT * FROM refresh_tokens WHERE user_id = :user_id"
        with pytest.raises(ValueError, match="disallowed"):
            _validate_sql(sql)

    def test_returns_stripped_sql(self):
        sql = "  SELECT id FROM content_items WHERE user_id = :user_id  "
        result = _validate_sql(sql)
        assert result == sql.strip()


# ---------------------------------------------------------------------------
# User isolation enforcement
# ---------------------------------------------------------------------------


class TestEnforceUserIsolation:
    def test_missing_user_id_rejected(self):
        sql = "SELECT * FROM content_items"
        with pytest.raises(ValueError, match="user_id"):
            _enforce_user_isolation(sql)

    def test_present_user_id_passes(self):
        sql = "SELECT * FROM content_items WHERE user_id = :user_id"
        _enforce_user_isolation(sql)  # must not raise

    def test_user_scoped_table_without_own_filter_rejected(self):
        """
        highlights is user-scoped but filtered only by ci.user_id here.
        The isolation check requires each user-scoped table to have its own filter.
        """
        sql = """
        SELECT ci.title, h.text
        FROM content_items ci
        JOIN highlights h ON h.content_item_id = ci.id
        WHERE ci.user_id = :user_id
        """
        with pytest.raises(ValueError, match="user-scoped"):
            _enforce_user_isolation(sql)

    def test_both_joined_tables_filtered_passes(self):
        sql = """
        SELECT ci.title, h.text
        FROM content_items ci
        JOIN highlights h ON h.content_item_id = ci.id
        WHERE ci.user_id = :user_id AND h.user_id = :user_id
        """
        _enforce_user_isolation(sql)  # must not raise

    def test_lists_uses_owner_id(self):
        sql = "SELECT name FROM lists WHERE owner_id = :user_id"
        _enforce_user_isolation(sql)  # must not raise


# ---------------------------------------------------------------------------
# End-to-end: mocked LLM, real DB execution
# ---------------------------------------------------------------------------


def _llm_response(text: str) -> MagicMock:
    """Build a mock object that looks like llm_client.chat() return value."""
    mock = MagicMock()
    mock.content = text
    return mock


class TestQueryLibraryExecution:
    """
    Full query_library invocation with LLM mocked to return known SQL.
    Uses real DB from the MCP conftest so SQL actually executes.
    """

    def test_returns_results_for_valid_query(self, db, user, article):
        safe_sql = "SELECT title FROM content_items WHERE user_id = :user_id LIMIT 10"
        # chat() is called twice: SQL gen + result summarization
        with patch("app.mcp.tools.query.llm_client") as mock_llm:
            mock_llm.chat.side_effect = [
                _llm_response(safe_sql),  # SQL generation call
                _llm_response("You have 1 article."),  # summarization call
            ]
            result = query_library(
                question="What articles do I have?", user=user, db=db
            )

        assert result["row_count"] >= 1
        assert result["sql"] == safe_sql
        assert isinstance(result["answer"], str)

    def test_does_not_return_other_users_rows(self, db, user, other_user):
        from app.models.content import ContentItem

        other_item = ContentItem(
            original_url="https://secret.com/article",
            title="Other User Secret Article",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_item)
        db.commit()

        safe_sql = "SELECT title FROM content_items WHERE user_id = :user_id"
        with patch("app.mcp.tools.query.llm_client") as mock_llm:
            mock_llm.chat.return_value = _llm_response(safe_sql)
            result = query_library(question="Show me all articles", user=user, db=db)

        # user has no articles; other_user's row must not appear
        assert result["row_count"] == 0
        assert "Other User Secret Article" not in result["answer"]

    def test_raises_on_disallowed_table_in_llm_sql(self, db, user):
        bad_sql = "SELECT * FROM users"
        with patch("app.mcp.tools.query.llm_client") as mock_llm:
            mock_llm.chat.return_value = _llm_response(bad_sql)
            with pytest.raises(ValueError, match="validation"):
                query_library(question="Give me all users", user=user, db=db)

    def test_strips_markdown_fences_from_llm_output(self, db, user, article):
        safe_sql = "SELECT title FROM content_items WHERE user_id = :user_id LIMIT 5"
        fenced = f"```sql\n{safe_sql}\n```"
        with patch("app.mcp.tools.query.llm_client") as mock_llm:
            mock_llm.chat.side_effect = [
                _llm_response(fenced),
                _llm_response("Your article is Test Article."),
            ]
            result = query_library(question="List articles", user=user, db=db)

        assert result["sql"] == safe_sql

    def test_raises_on_missing_user_id_in_llm_sql(self, db, user):
        """LLM forgot to add WHERE user_id — isolation check must block it."""
        bad_sql = "SELECT title FROM content_items LIMIT 10"
        with patch("app.mcp.tools.query.llm_client") as mock_llm:
            mock_llm.chat.return_value = _llm_response(bad_sql)
            with pytest.raises(ValueError, match="isolation"):
                query_library(question="Show everything", user=user, db=db)
