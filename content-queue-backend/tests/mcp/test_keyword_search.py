"""
TDD tests for PostgreSQL full-text keyword search.

Tests that keyword_search() uses tsvector + ts_rank_cd to find
articles by keywords, not embeddings. No OpenAI calls.
"""

from app.core.hybrid_search import keyword_search


class TestKeywordSearch:
    """keyword_search queries the search_vector tsvector column."""

    def test_finds_by_title_word(self, db, user, article):
        """article.title = 'Test Article' -> searching 'article' should find it."""
        results = keyword_search(query="article", user=user, db=db)
        assert len(results) >= 1
        assert any(r["id"] == str(article.id) for r in results)

    def test_finds_by_author(self, db, user, article):
        """article.author = 'Test Author' -> weighted 'A', should rank high."""
        results = keyword_search(query="Test Author", user=user, db=db)
        assert len(results) >= 1
        assert results[0]["id"] == str(article.id)

    def test_finds_by_tag(self, db, user, article):
        """article.tags = ['tech', 'ai'] -> 'tech' should match."""
        results = keyword_search(query="tech", user=user, db=db)
        assert any(r["id"] == str(article.id) for r in results)

    def test_finds_by_description(self, db, user, article):
        """article.description = 'A test article'."""
        results = keyword_search(query="test article", user=user, db=db)
        assert len(results) >= 1

    def test_no_results_for_unrelated_query(self, db, user, article):
        """Query that has no keyword overlap returns empty."""
        results = keyword_search(query="quantum computing", user=user, db=db)
        assert not any(r["id"] == str(article.id) for r in results)

    def test_user_isolation(self, db, user, other_user, article):
        """other_user cannot see user's articles via keyword search."""
        results = keyword_search(query="Test Article", user=other_user, db=db)
        assert len(results) == 0

    def test_excludes_deleted(self, db, user, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        results = keyword_search(query="Test Article", user=user, db=db)
        assert len(results) == 0

    def test_respects_limit(self, db, user, article, second_article):
        results = keyword_search(query="article", user=user, db=db, limit=1)
        assert len(results) <= 1

    def test_title_ranks_higher_than_description(self, db, user):
        """An article with the keyword in title should rank above one with it only in description."""
        from app.models.content import ContentItem

        # Article with keyword in title (weight A)
        title_match = ContentItem(
            original_url="https://example.com/title-match",
            title="Quantum Computing Explained",
            description="An overview of modern physics",
            user_id=user.id,
            processing_status="completed",
        )
        # Article with keyword only in description (weight B)
        desc_match = ContentItem(
            original_url="https://example.com/desc-match",
            title="Physics Overview",
            description="Includes a section on quantum computing",
            user_id=user.id,
            processing_status="completed",
        )
        db.add_all([title_match, desc_match])
        db.commit()

        results = keyword_search(query="quantum computing", user=user, db=db)
        assert len(results) == 2
        # Title match (weight A) should come first
        assert results[0]["id"] == str(title_match.id)

    def test_result_format(self, db, user, article):
        results = keyword_search(query="Test Article", user=user, db=db)
        if results:
            r = results[0]
            assert "id" in r
            assert "title" in r
            assert "score" in r
            assert isinstance(r["score"], float)

    def test_websearch_syntax_or(self, db, user, article, second_article):
        """websearch_to_tsquery supports OR syntax."""
        results = keyword_search(query="tech OR science", user=user, db=db)
        ids = {r["id"] for r in results}
        # article has tag 'tech', second_article has tag 'science'
        assert str(article.id) in ids or str(second_article.id) in ids

    def test_exact_phrase_in_quotes(self, db, user, article):
        """websearch_to_tsquery treats quoted strings as phrase search."""
        results = keyword_search(query='"Test Article"', user=user, db=db)
        assert any(r["id"] == str(article.id) for r in results)
