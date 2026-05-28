"""
TDD tests for query intent classifier.

The classifier routes search queries to the most efficient search path
without any LLM call. It uses regex heuristics + user data matching.
"""

from app.core.search_router import classify_query, parse_filter_query


class TestOperatorDetection:
    """Explicit operators (power-user feature) are always respected."""

    def test_author_operator(self):
        result, meta = classify_query("author:Paul Graham")
        assert result == "filter"
        assert meta["author"] == "Paul Graham"

    def test_tag_operator(self):
        result, meta = classify_query("tag:AI")
        assert result == "filter"
        assert meta["tag"] == "AI"

    def test_site_operator(self):
        result, meta = classify_query("site:nytimes.com")
        assert result == "filter"
        assert meta["site"] == "nytimes.com"

    def test_is_unread_operator(self):
        result, meta = classify_query("is:unread")
        assert result == "filter"
        assert meta["is"] == "unread"

    def test_is_read_operator(self):
        result, meta = classify_query("is:read")
        assert result == "filter"
        assert meta["is"] == "read"

    def test_is_archived_operator(self):
        result, meta = classify_query("is:archived")
        assert result == "filter"
        assert meta["is"] == "archived"

    def test_before_operator(self):
        result, meta = classify_query("before:2026-01-01")
        assert result == "filter"
        assert meta["before"] == "2026-01-01"

    def test_after_operator(self):
        result, meta = classify_query("after:2026-03-01")
        assert result == "filter"
        assert meta["after"] == "2026-03-01"

    def test_combined_operators(self):
        result, meta = classify_query("tag:AI is:unread after:2026-01-01")
        assert result == "filter"
        assert meta["tag"] == "AI"
        assert meta["is"] == "unread"
        assert meta["after"] == "2026-01-01"

    def test_quoted_operator_value(self):
        """author:"Paul Graham" should capture the full quoted name."""
        result, meta = classify_query('author:"Paul Graham"')
        assert result == "filter"
        assert meta["author"] == "Paul Graham"


class TestInferredFilters:
    """Queries that match user's known authors/tags/domains without operators."""

    def test_known_author_match(self):
        result, meta = classify_query(
            "Paul Graham",
            user_authors={"paul graham"},
        )
        assert result == "filter"
        assert meta["author"] == "Paul Graham"

    def test_known_author_case_insensitive(self):
        result, meta = classify_query(
            "paul graham",
            user_authors={"paul graham"},
        )
        assert result == "filter"
        assert meta["author"] == "paul graham"

    def test_known_tag_routes_to_keyword(self):
        """Tag names route to keyword — tags are in tsvector; use tag: operator for explicit filtering."""
        result, _ = classify_query("AI")
        assert result == "keyword"

    def test_domain_pattern(self):
        """nytimes.com looks like a domain -> site filter."""
        result, meta = classify_query("nytimes.com")
        assert result == "filter"
        assert meta["site"] == "nytimes.com"

    def test_substack_domain(self):
        result, meta = classify_query("stratechery.substack.com")
        assert result == "filter"
        assert meta["site"] == "stratechery.substack.com"

    def test_unknown_short_query_not_inferred_as_filter(self):
        """Short query that doesn't match known authors falls to keyword."""
        result, meta = classify_query(
            "react hooks",
            user_authors={"paul graham"},
        )
        assert result == "keyword"

    def test_empty_user_data_defaults(self):
        """When no user data provided, no inference happens."""
        result, meta = classify_query("Paul Graham")
        # Without user_authors set, this is a 2-word keyword query
        assert result == "keyword"


class TestExactPhrase:
    """Quoted queries always go to keyword search."""

    def test_quoted_phrase(self):
        result, _ = classify_query('"attention economy"')
        assert result == "keyword"

    def test_quoted_phrase_with_surrounding_text(self):
        result, _ = classify_query('articles about "attention economy"')
        assert result == "keyword"


class TestShortKeyword:
    """1-3 word non-question queries go to keyword search."""

    def test_single_word(self):
        result, _ = classify_query("RLHF")
        assert result == "keyword"

    def test_two_words(self):
        result, _ = classify_query("react hooks")
        assert result == "keyword"

    def test_three_words(self):
        result, _ = classify_query("AI ethics paper")
        assert result == "keyword"

    def test_four_words_is_hybrid(self):
        """4 words without question words -> hybrid (threshold is ≤3 for keyword)."""
        result, _ = classify_query("machine learning transformer attention")
        assert result == "hybrid"


class TestQuestionDetection:
    """Questions and natural language queries go to semantic search."""

    def test_what_question(self):
        result, _ = classify_query("what have I read about habit formation?")
        assert result == "semantic"

    def test_how_question(self):
        result, _ = classify_query("how do neural networks learn")
        assert result == "semantic"

    def test_why_question(self):
        result, _ = classify_query("why is social media addictive")
        assert result == "semantic"

    def test_explain_prefix(self):
        result, _ = classify_query("explain attention mechanisms")
        assert result == "semantic"

    def test_find_me_prefix(self):
        result, _ = classify_query("find me articles about dopamine")
        assert result == "semantic"

    def test_show_me_prefix(self):
        result, _ = classify_query("show me what I saved about climate")
        assert result == "semantic"

    def test_question_mark_suffix(self):
        result, _ = classify_query("anything about stoicism?")
        assert result == "semantic"

    def test_who_question(self):
        result, _ = classify_query("who wrote about effective altruism")
        assert result == "semantic"


class TestHybridDefault:
    """Longer non-question phrases default to hybrid."""

    def test_conceptual_phrase(self):
        result, _ = classify_query("articles about attention and dopamine")
        assert result == "hybrid"

    def test_topic_exploration(self):
        """4-word conceptual phrase -> hybrid, not keyword."""
        result, _ = classify_query("effective altruism criticism arguments")
        assert result == "hybrid"

    def test_medium_length_non_question(self):
        result, _ = classify_query("deep learning computer vision applications")
        assert result == "hybrid"


class TestEdgeCases:
    """Whitespace, empty-ish, and unusual inputs."""

    def test_whitespace_stripped(self):
        result, _ = classify_query("  RLHF  ")
        assert result == "keyword"

    def test_single_character(self):
        """Very short input still classifies without crashing."""
        result, _ = classify_query("a")
        assert result == "keyword"

    def test_operator_with_extra_spaces(self):
        result, meta = classify_query("  tag:AI  ")
        assert result == "filter"

    def test_mixed_operator_and_freetext(self):
        """Operator present -> filter, even if other text exists."""
        result, meta = classify_query("tag:AI machine learning")
        assert result == "filter"
        assert meta["tag"] == "AI"


class TestParseFilterQuery:
    """parse_filter_query turns metadata dict into SQLAlchemy filter conditions."""

    def test_author_filter(self, db, user, article):
        """article fixture has author='Test Author'. Querying should find it."""
        results = parse_filter_query(
            meta={"author": "Test Author"},
            user=user,
            db=db,
        )
        assert len(results) == 1
        assert results[0]["id"] == str(article.id)

    def test_author_partial_match(self, db, user, article):
        """author filter uses ILIKE, so partial matches work."""
        results = parse_filter_query(
            meta={"author": "Test"},
            user=user,
            db=db,
        )
        assert len(results) == 1

    def test_tag_filter(self, db, user, article):
        """article fixture has tags=['tech', 'ai']. Querying 'tech' should find it."""
        results = parse_filter_query(
            meta={"tag": "tech"},
            user=user,
            db=db,
        )
        assert len(results) >= 1
        assert any(r["id"] == str(article.id) for r in results)

    def test_site_filter(self, db, user, article):
        """article fixture has original_url='https://example.com/article'."""
        results = parse_filter_query(
            meta={"site": "example.com"},
            user=user,
            db=db,
        )
        assert len(results) == 1

    def test_is_unread_filter(self, db, user, article):
        """article is not read by default."""
        results = parse_filter_query(meta={"is": "unread"}, user=user, db=db)
        assert any(r["id"] == str(article.id) for r in results)

    def test_is_read_filter(self, db, user, article):
        """article.is_read=False, so is:read should NOT find it."""
        results = parse_filter_query(meta={"is": "read"}, user=user, db=db)
        assert not any(r["id"] == str(article.id) for r in results)

    def test_combined_filters(self, db, user, article):
        """Multiple filters AND together."""
        results = parse_filter_query(
            meta={"author": "Test Author", "tag": "tech"},
            user=user,
            db=db,
        )
        assert len(results) == 1

    def test_user_isolation(self, db, user, other_user, article):
        """other_user should not see user's articles."""
        results = parse_filter_query(
            meta={"author": "Test Author"},
            user=other_user,
            db=db,
        )
        assert len(results) == 0

    def test_excludes_deleted(self, db, user, article):
        """Deleted articles are excluded."""
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        results = parse_filter_query(
            meta={"author": "Test Author"},
            user=user,
            db=db,
        )
        assert len(results) == 0

    def test_empty_meta_returns_all(self, db, user, article, second_article):
        """No filters -> return all non-deleted articles for user."""
        results = parse_filter_query(meta={}, user=user, db=db)
        assert len(results) == 2

    def test_result_format(self, db, user, article):
        """Results have the same shape as existing search results."""
        results = parse_filter_query(meta={"author": "Test Author"}, user=user, db=db)
        item = results[0]
        assert "id" in item
        assert "title" in item
        assert "url" in item or "original_url" in item
        assert "author" in item
        assert "tags" in item
