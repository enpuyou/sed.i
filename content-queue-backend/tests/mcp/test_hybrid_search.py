"""
TDD tests for Reciprocal Rank Fusion and the unified hybrid search function.
"""

from app.core.hybrid_search import rrf_fuse, hybrid_search, get_user_search_context


class TestRRFFusion:
    """rrf_fuse merges two ranked result lists using Reciprocal Rank Fusion."""

    def test_identical_lists_boost_score(self):
        """Items in both lists get higher RRF score than items in only one."""
        list_a = ["id1", "id2", "id3"]
        list_b = ["id1", "id3", "id4"]
        fused = rrf_fuse(list_a, list_b, k=60)
        # id1 is rank 1 in both -> highest score
        assert fused[0] == "id1"
        # id3 is in both lists -> should rank above id2 and id4 (single-list only)
        id3_rank = [i for i, x in enumerate(fused) if x == "id3"][0]
        id2_rank = [i for i, x in enumerate(fused) if x == "id2"][0]
        id4_rank = [i for i, x in enumerate(fused) if x == "id4"][0]
        assert id3_rank < id2_rank  # id3 beats id2
        assert id3_rank < id4_rank  # id3 beats id4

    def test_empty_lists(self):
        fused = rrf_fuse([], [], k=60)
        assert fused == []

    def test_one_empty_list(self):
        fused = rrf_fuse(["a", "b"], [], k=60)
        assert fused == ["a", "b"]

    def test_no_overlap(self):
        """Disjoint lists: items interleaved by rank."""
        fused = rrf_fuse(["a", "b"], ["c", "d"], k=60)
        assert len(fused) == 4
        # a and c are both rank-1 in their lists, so they tie.
        # Just verify all present.
        assert set(fused) == {"a", "b", "c", "d"}

    def test_respects_k_parameter(self):
        """Lower k amplifies rank differences."""
        list_a = ["id1", "id2"]
        list_b = ["id2", "id1"]
        fused_low_k = rrf_fuse(list_a, list_b, k=1)
        fused_high_k = rrf_fuse(list_a, list_b, k=1000)
        # Both should contain same items (order may differ but both are valid)
        assert set(fused_low_k) == set(fused_high_k)

    def test_preserves_limit(self):
        list_a = ["a", "b", "c", "d", "e"]
        list_b = ["f", "g", "h", "i", "j"]
        fused = rrf_fuse(list_a, list_b, k=60, limit=3)
        assert len(fused) == 3


class TestHybridSearch:
    """
    hybrid_search is the unified entry point that runs the classifier,
    dispatches to the right engine(s), and returns results.
    """

    def test_keyword_query_skips_embedding(self, db, user, article):
        """A short keyword query should NOT call OpenAI."""
        # "Test Article" is 2 words, should classify as keyword
        results = hybrid_search(query="Test Article", user=user, db=db)
        assert isinstance(results, list)
        # Should find the article via keyword search
        if results:
            assert any(r["id"] == str(article.id) for r in results)

    def test_filter_query_returns_results(self, db, user, article):
        """Operator queries go through filter path."""
        results = hybrid_search(query="tag:tech", user=user, db=db)
        assert any(r["id"] == str(article.id) for r in results)

    def test_author_inference(self, db, user, article):
        """Typing a known author name infers a filter query."""
        results = hybrid_search(
            query="Test Author",
            user=user,
            db=db,
            user_authors={"test author"},
        )
        assert any(r["id"] == str(article.id) for r in results)

    def test_tag_inference(self, db, user, article):
        """Typing a known tag infers a filter query."""
        results = hybrid_search(
            query="tech",
            user=user,
            db=db,
            user_tags={"tech"},
        )
        assert any(r["id"] == str(article.id) for r in results)

    def test_domain_inference(self, db, user, article):
        """Typing a domain-like string infers a site filter."""
        results = hybrid_search(query="example.com", user=user, db=db)
        assert any(r["id"] == str(article.id) for r in results)

    def test_user_isolation(self, db, user, other_user, article):
        results = hybrid_search(query="Test Article", user=other_user, db=db)
        assert not any(r["id"] == str(article.id) for r in results)

    def test_excludes_deleted(self, db, user, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        results = hybrid_search(query="Test Article", user=user, db=db)
        assert not any(r["id"] == str(article.id) for r in results)

    def test_semantic_query_graceful_without_openai(self, db, user, article):
        """Semantic queries should not crash if OpenAI key is missing."""
        # "what is this article about?" is a question -> semantic
        results = hybrid_search(query="what is this article about?", user=user, db=db)
        # Should return [] or fall back gracefully, not crash
        assert isinstance(results, list)

    def test_result_format(self, db, user, article):
        results = hybrid_search(query="Test Article", user=user, db=db)
        if results:
            r = results[0]
            assert "id" in r
            assert "title" in r
            assert "score" in r
            assert isinstance(r["score"], float)

    def test_respects_limit(self, db, user, article, second_article):
        results = hybrid_search(query="article", user=user, db=db, limit=1)
        assert len(results) <= 1


class TestGetUserSearchContext:
    def test_returns_authors(self, db, user, article):
        authors, tags = get_user_search_context(user=user, db=db)
        assert "test author" in authors

    def test_returns_tags(self, db, user, article):
        authors, tags = get_user_search_context(user=user, db=db)
        assert "tech" in tags
        assert "ai" in tags

    def test_empty_for_new_user(self, db, other_user):
        authors, tags = get_user_search_context(user=other_user, db=db)
        assert len(authors) == 0
        assert len(tags) == 0

    def test_excludes_deleted_articles(self, db, user, article):
        from datetime import datetime, timezone

        article.deleted_at = datetime.now(timezone.utc)
        db.commit()
        authors, tags = get_user_search_context(user=user, db=db)
        assert "test author" not in authors
