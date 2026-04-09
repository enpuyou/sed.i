# Hybrid Search: Execution Plan

> Step-by-step implementation guide with TDD. Each step produces a working, tested increment.
> Designed for Sonnet to execute sequentially — each step has exact file paths, test expectations, and acceptance criteria.

---

## Prerequisites

- Test database running on port 5433 (`content_queue_test`)
- Run tests with: `cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/mcp/test_hybrid_search.py -v`
- All tests use the existing MCP conftest pattern (direct function calls, not HTTP)

---

## Phase 1: Query Classifier

### Step 1.1 — Write classifier tests FIRST

**Create file:** `content-queue-backend/tests/mcp/test_search_router.py`

**Tests to write (all must fail initially):**

```python
"""
TDD tests for query intent classifier.

The classifier routes search queries to the most efficient search path
without any LLM call. It uses regex heuristics + user data matching.
"""
import pytest
from app.core.search_router import classify_query


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

    def test_known_tag_match(self):
        result, meta = classify_query(
            "AI",
            user_tags={"ai"},
        )
        assert result == "filter"
        assert meta["tag"] == "AI"

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
        """Short query that doesn't match known data falls to keyword."""
        result, meta = classify_query(
            "react hooks",
            user_authors={"paul graham"},
            user_tags={"ai", "tech"},
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

    def test_four_words_not_keyword(self):
        """4+ words without question words -> hybrid, not keyword."""
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
```

**Run tests — all should fail (module not found).**

### Step 1.2 — Implement the classifier

**Create file:** `content-queue-backend/app/core/search_router.py`

Implement `classify_query` function:

```python
"""
Query intent classifier for hybrid search.

Routes search queries to the most efficient search path using
regex heuristics and user data matching. No LLM call.

Returns:
    tuple[str, dict]: (search_type, metadata)
    search_type: "filter" | "keyword" | "semantic" | "hybrid"
    metadata: dict with inferred filter values (e.g. {"author": "Paul Graham"})
"""
```

**Requirements the function must satisfy:**
- Signature: `classify_query(query: str, *, user_authors: set[str] | None = None, user_tags: set[str] | None = None) -> tuple[str, dict]`
- Always returns a 2-tuple: `(type_string, metadata_dict)`
- Operator parsing: extract `key:value` or `key:"quoted value"` pairs
- Domain detection: regex for common TLDs (`.com`, `.org`, `.net`, `.io`, `.co`, `.dev`, `.substack`, `.medium`)
- Author/tag inference: case-insensitive `in` check against provided sets
- Question detection: starts with question words OR ends with `?`
- Short keyword: 1-3 non-question words
- Default: "hybrid"
- Priority order: operators > exact phrase > domain > known author > known tag > short keyword > question > hybrid

**Run tests — all should pass.**

### Step 1.3 — Write filter parser tests

**Add to the same test file** `test_search_router.py`:

```python
from app.core.search_router import parse_filter_query


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
        results = parse_filter_query(
            meta={"author": "Test Author"}, user=user, db=db
        )
        item = results[0]
        assert "id" in item
        assert "title" in item
        assert "url" in item or "original_url" in item
        assert "author" in item
        assert "tags" in item
```

**These tests use the MCP conftest fixtures** (`db`, `user`, `other_user`, `article`, `second_article`) — they hit real PostgreSQL.

### Step 1.4 — Implement filter parser

**Add to:** `content-queue-backend/app/core/search_router.py`

```python
def parse_filter_query(
    *,
    meta: dict,
    user: User,
    db: Session,
    limit: int = 50,
) -> list[dict]:
```

**Requirements:**
- Build a SQLAlchemy query against `ContentItem` starting with `user_id = user.id AND deleted_at IS NULL`
- Apply filters from `meta` dict:
  - `author` → `ContentItem.author.ilike(f"%{value}%")`
  - `tag` → `ContentItem.tags.any(value)` (PostgreSQL ARRAY `ANY`)
  - `site` → `ContentItem.original_url.ilike(f"%{value}%")`
  - `is` → `unread`: `is_read == False`, `read`: `is_read == True`, `archived`: `is_archived == True`
  - `before` → `ContentItem.created_at < date`
  - `after` → `ContentItem.created_at >= date`
- Order by `created_at DESC` (most recent first)
- Limit to `limit` results
- Return `list[dict]` using the same `_format_item` helper from `app/mcp/tools/content.py`

**Run tests — all should pass.**

---

## Phase 2: PostgreSQL Full-Text Search

### Step 2.1 — Write tsvector migration

**Create file:** `content-queue-backend/alembic/versions/XXX_add_search_vector_to_content_items.py`

Generate with: `cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run alembic revision --autogenerate -m "add_search_vector_to_content_items"`

Then **manually edit** the migration because SQLAlchemy autogenerate won't handle generated columns:

```python
def upgrade() -> None:
    # Add tsvector column as a generated (stored) column
    op.execute("""
        ALTER TABLE content_items ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(author, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(array_to_string(tags, ' '), '')), 'B')
        ) STORED;
    """)
    # GIN index for fast full-text search
    op.execute("""
        CREATE INDEX idx_content_items_search_vector
        ON content_items USING gin(search_vector);
    """)

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_content_items_search_vector;")
    op.drop_column("content_items", "search_vector")
```

**Revision chain:** `down_revision` must be `"0618711d3113"` (current head).

**Run migration on test DB:**
```bash
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run alembic upgrade head
```

**Note:** Also add the column to the SQLAlchemy model in `app/models/content.py` so `Base.metadata.create_all()` in tests creates it:
```python
from sqlalchemy import Computed
from sqlalchemy.dialects.postgresql import TSVECTOR

search_vector = Column(
    TSVECTOR,
    Computed("""
        setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(author, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(array_to_string(tags, ' '), '')), 'B')
    """, persisted=True),
)
```

### Step 2.2 — Write keyword search tests

**Create file:** `content-queue-backend/tests/mcp/test_keyword_search.py`

```python
"""
TDD tests for PostgreSQL full-text keyword search.

Tests that keyword_search() uses tsvector + ts_rank_cd to find
articles by keywords, not embeddings. No OpenAI calls.
"""
import pytest
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

        # Article with keyword in title
        title_match = ContentItem(
            original_url="https://example.com/title-match",
            title="Quantum Computing Explained",
            description="An overview of modern physics",
            user_id=user.id,
            processing_status="completed",
        )
        # Article with keyword only in description
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
```

### Step 2.3 — Implement keyword search

**Create file:** `content-queue-backend/app/core/hybrid_search.py`

```python
"""
Hybrid search engine: keyword (tsvector), semantic (pgvector), and RRF fusion.
"""
```

Implement `keyword_search`:

```python
def keyword_search(
    *,
    query: str,
    user: User,
    db: Session,
    limit: int = 10,
) -> list[dict]:
```

**Requirements:**
- Use `websearch_to_tsquery('english', query)` — handles OR, NOT, quoted phrases automatically
- Rank with `ts_rank_cd(search_vector, tsquery)` — cover density ranking, respects weights
- Filter: `search_vector @@ tsquery AND user_id = :uid AND deleted_at IS NULL`
- Order by rank DESC
- Return `list[dict]` with `id`, `title`, `url`, `author`, `tags`, `score` (the ts_rank_cd value), and other standard fields
- Use raw `text()` SQL (same pattern as existing pgvector queries)

**Run tests — all should pass.**

---

## Phase 3: RRF Hybrid Search

### Step 3.1 — Write RRF fusion tests

**Add to:** `content-queue-backend/tests/mcp/test_hybrid_search.py` (new file)

```python
"""
TDD tests for Reciprocal Rank Fusion and the unified hybrid search function.
"""
import pytest
from app.core.hybrid_search import rrf_fuse, hybrid_search


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
        results = hybrid_search(
            query="what is this article about?", user=user, db=db
        )
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
```

### Step 3.2 — Implement RRF fusion function

**Add to:** `content-queue-backend/app/core/hybrid_search.py`

```python
def rrf_fuse(
    list_a: list[str],
    list_b: list[str],
    k: int = 60,
    limit: int | None = None,
) -> list[str]:
```

**Requirements:**
- Pure function, no DB access
- `list_a` and `list_b` are ordered lists of IDs (rank 1 = index 0)
- RRF score for each ID: `sum(1/(k + rank))` across all lists the ID appears in
- Rank is 1-indexed (first item = rank 1)
- IDs not in a list contribute 0 from that list
- Return IDs sorted by descending RRF score
- Apply `limit` if provided

### Step 3.3 — Implement unified hybrid_search function

**Add to:** `content-queue-backend/app/core/hybrid_search.py`

```python
def hybrid_search(
    *,
    query: str,
    user: User,
    db: Session,
    limit: int = 10,
    user_authors: set[str] | None = None,
    user_tags: set[str] | None = None,
) -> list[dict]:
```

**Requirements:**
- Call `classify_query(query, user_authors=user_authors, user_tags=user_tags)`
- Based on classification:
  - `"filter"` → call `parse_filter_query(meta=meta, user=user, db=db, limit=limit)`
  - `"keyword"` → call `keyword_search(query=query, user=user, db=db, limit=limit)`
  - `"semantic"` → call existing semantic search (OpenAI embed + pgvector), wrapped in try/except for graceful failure
  - `"hybrid"` → run `keyword_search` AND semantic search in sequence, fuse with `rrf_fuse`, then fetch full items for the fused IDs
- Normalize result format: every result has `id`, `title`, `url`, `author`, `tags`, `score`
- Semantic failures (no OpenAI key, API error) → fall back to keyword-only results, do not crash
- Apply `limit` to final results

**Run all tests — should pass.**

---

## Phase 4: Wire Into API + MCP

### Step 4.1 — Write integration tests for the API endpoint

**Create file:** `content-queue-backend/tests/test_search_api.py`

```python
"""
Integration tests for the /search endpoint.

These test the HTTP layer: correct status codes, auth, query param handling.
The actual search logic is tested in test_search_router.py and test_hybrid_search.py.
"""
import pytest


class TestSearchEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/search/semantic?query=test")
        assert resp.status_code == 401

    def test_min_query_length(self, client, auth_headers):
        resp = client.get("/search/semantic?query=ab", headers=auth_headers)
        assert resp.status_code == 422  # Validation error

    def test_returns_list(self, client, auth_headers):
        resp = client.get("/search/semantic?query=test query", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_filter_query_works(self, client, auth_headers, test_content):
        resp = client.get(
            "/search/semantic?query=author:Test Author",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)

    def test_keyword_query_works(self, client, auth_headers, test_content):
        resp = client.get(
            '/search/semantic?query="Test Article"',
            headers=auth_headers,
        )
        assert resp.status_code == 200
```

### Step 4.2 — Update the search API endpoint

**Edit file:** `content-queue-backend/app/api/search.py`

Update the `semantic_search` endpoint (lines 54-149) to:
1. Load user's known authors and tags from DB (single query each, cache per request)
2. Call `hybrid_search()` instead of directly embedding + pgvector
3. Keep the same response schema (`list[SimilarContentResponse]`) for backward compatibility
4. Map `score` to `similarity_score` in response

**Important:** The endpoint URL stays `/search/semantic` for now to avoid frontend changes. The hybrid routing is invisible to the frontend.

### Step 4.3 — Update MCP search_content tool

**Edit file:** `content-queue-backend/app/mcp/tools/content.py`

Update `search_content()` (lines 48-118) to:
1. Load user's known authors/tags
2. Call `hybrid_search()` instead of direct OpenAI + pgvector
3. Keep the same return format: `[{"item": {...}, "similarity_score": float}]`

### Step 4.4 — Helper: load user's known authors and tags

**Add to:** `content-queue-backend/app/core/hybrid_search.py`

```python
def get_user_search_context(user: User, db: Session) -> tuple[set[str], set[str]]:
    """
    Load user's known authors and tags for the query classifier.
    Returns (authors_set, tags_set) both lowercased.
    """
```

**Requirements:**
- Query `SELECT DISTINCT author FROM content_items WHERE user_id = :uid AND deleted_at IS NULL AND author IS NOT NULL`
- Query `SELECT DISTINCT unnest(tags) FROM content_items WHERE user_id = :uid AND deleted_at IS NULL AND tags IS NOT NULL`
- Return both as `set[str]` with values lowercased
- These are cheap queries (distinct on indexed columns, user-scoped)

**Write test:**

```python
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
```

**Run all tests. Full suite should pass.**

---

## Phase 5: Embedding Cache (Quick Win)

### Step 5.1 — Write cache tests

**Create file:** `content-queue-backend/tests/test_embedding_cache.py`

```python
"""
Tests for query embedding cache.

Uses a fake Redis (or real test Redis) to verify caching behavior.
"""
import pytest
from unittest.mock import MagicMock, patch
from app.core.embedding_cache import get_or_create_query_embedding


class TestEmbeddingCache:
    def test_returns_embedding_list(self):
        """Should return a list of floats."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Cache miss

        fake_embedding = [0.1] * 1536
        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            result = get_or_create_query_embedding("test query", redis_client=mock_redis)
        assert result == fake_embedding
        assert len(result) == 1536

    def test_caches_result_in_redis(self):
        """After a cache miss, the embedding should be stored in Redis."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        fake_embedding = [0.1] * 1536
        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 3600  # TTL = 1 hour

    def test_returns_cached_result_on_hit(self):
        """On cache hit, should NOT call OpenAI."""
        import json
        fake_embedding = [0.1] * 1536
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(fake_embedding)

        with patch("app.core.embedding_cache.call_openai_embedding") as mock_openai:
            result = get_or_create_query_embedding("test query", redis_client=mock_redis)
        mock_openai.assert_not_called()
        assert result == fake_embedding

    def test_same_query_same_cache_key(self):
        """Same query text -> same cache key (deterministic)."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 == key2

    def test_different_query_different_cache_key(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("query one", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("query two", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 != key2

    def test_normalizes_query_whitespace_and_case(self):
        """'  Test Query  ' and 'test query' should hit same cache key."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("  Test Query  ", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch("app.core.embedding_cache.call_openai_embedding", return_value=fake_embedding):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 == key2
```

### Step 5.2 — Implement embedding cache

**Create file:** `content-queue-backend/app/core/embedding_cache.py`

```python
"""
Redis-backed cache for OpenAI query embeddings.

Avoids redundant API calls for repeated or near-identical search queries.
"""
```

**Functions:**
- `call_openai_embedding(query: str) -> list[float]` — thin wrapper around OpenAI API
- `get_or_create_query_embedding(query: str, *, redis_client) -> list[float]` — cache-first, then API

**Requirements:**
- Cache key: `qemb:{sha256(query.lower().strip())[:16]}`
- TTL: 3600 seconds (1 hour)
- Normalize: lowercase + strip whitespace before hashing
- Store as JSON string in Redis
- Return `list[float]` (1536-dim)

### Step 5.3 — Wire cache into hybrid_search semantic path

**Edit:** `content-queue-backend/app/core/hybrid_search.py`

In the semantic search path of `hybrid_search()`, replace direct OpenAI call with `get_or_create_query_embedding()`. If Redis is not available (connection error), fall back to direct API call.

---

## File Summary

### New files to create:
| File | Purpose |
|------|---------|
| `app/core/search_router.py` | Query classifier + filter parser |
| `app/core/hybrid_search.py` | Keyword search, RRF fusion, unified hybrid_search |
| `app/core/embedding_cache.py` | Redis-backed embedding cache |
| `tests/mcp/test_search_router.py` | Classifier + filter parser tests |
| `tests/mcp/test_keyword_search.py` | Full-text keyword search tests |
| `tests/mcp/test_hybrid_search.py` | RRF + unified hybrid search tests |
| `tests/test_search_api.py` | HTTP integration tests |
| `tests/test_embedding_cache.py` | Embedding cache unit tests |
| `alembic/versions/XXX_add_search_vector.py` | tsvector migration |

### Files to modify:
| File | Change |
|------|--------|
| `app/models/content.py` | Add `search_vector` Computed column (~3 lines) |
| `app/api/search.py` | Update `semantic_search` to call `hybrid_search` (~15 lines changed) |
| `app/mcp/tools/content.py` | Update `search_content` to call `hybrid_search` (~15 lines changed) |

### Files NOT modified:
| File | Why |
|------|-----|
| `frontend/components/SearchBar.tsx` | No changes — same endpoint, same response shape |
| `frontend/lib/api.ts` | No changes — same API contract |
| `app/tasks/embedding.py` | Embedding pipeline unchanged |

---

## Test Execution Order

Run in this exact order. Each phase's tests must pass before moving to the next.

```bash
# Phase 1: Classifier (no DB needed for classify_query tests)
pytest tests/mcp/test_search_router.py::TestOperatorDetection -v
pytest tests/mcp/test_search_router.py::TestInferredFilters -v
pytest tests/mcp/test_search_router.py::TestExactPhrase -v
pytest tests/mcp/test_search_router.py::TestShortKeyword -v
pytest tests/mcp/test_search_router.py::TestQuestionDetection -v
pytest tests/mcp/test_search_router.py::TestHybridDefault -v
pytest tests/mcp/test_search_router.py::TestEdgeCases -v

# Phase 1.3: Filter parser (needs DB)
pytest tests/mcp/test_search_router.py::TestParseFilterQuery -v

# Phase 2: Keyword search (needs DB + tsvector migration)
pytest tests/mcp/test_keyword_search.py -v

# Phase 3: RRF + hybrid (needs DB)
pytest tests/mcp/test_hybrid_search.py::TestRRFFusion -v
pytest tests/mcp/test_hybrid_search.py::TestHybridSearch -v

# Phase 4: API integration (needs DB)
pytest tests/test_search_api.py -v

# Phase 4 continued: User context helper
pytest tests/mcp/test_hybrid_search.py::TestGetUserSearchContext -v

# Phase 5: Embedding cache (mock Redis, no DB needed)
pytest tests/test_embedding_cache.py -v

# Final: full suite
pytest tests/ -v
```

---

## Phase 6: Search Evals

The point of evals is to answer: **"did hybrid search actually improve results?"** — not just "does the code work?" (that's what the TDD tests do).

We need a reproducible eval suite that runs before and after the change, on the same data, producing numbers we can compare.

### What We Measure

| Metric | What It Tells Us | How We Compute It |
|--------|------------------|-------------------|
| **Hit Rate (Recall@10)** | "Did the correct article appear in the top 10?" | For each eval query, check if the expected article ID is in the result set. `hits / total_queries`. |
| **Mean Reciprocal Rank (MRR)** | "How high did the correct article rank?" | `1/rank` of the first correct result, averaged across queries. MRR=1.0 means always #1. |
| **Latency p50 / p95** | "How fast is it?" | Measure wall-clock time per search call. |
| **API Calls Avoided** | "How much money/time did we save?" | Count queries that resolved without an OpenAI embedding call. |
| **Classification Accuracy** | "Did the router send the query to the right engine?" | For each eval query with a labeled intent, check if `classify_query` output matches. |

### Step 6.1 — Create the eval dataset

**Create file:** `content-queue-backend/tests/evals/search_eval_dataset.py`

This is a curated set of query/expected-result pairs. The dataset simulates a realistic personal library. It must be **seeded into the test DB** so results are deterministic.

```python
"""
Eval dataset for hybrid search.

Contains:
1. A set of articles to seed into the DB (with titles, authors, tags, descriptions,
   full_text, and pre-computed embeddings)
2. A set of eval queries, each with:
   - query text
   - expected_intent: what the classifier should return
   - expected_article_ids: which articles should appear in top-10
   - expected_top1_id: which article should be #1 (optional, for MRR)
   - category: what type of query this is (for per-category reporting)
"""

EVAL_ARTICLES = [
    {
        "key": "pg_essay",
        "title": "How to Do Great Work",
        "author": "Paul Graham",
        "original_url": "https://paulgraham.com/greatwork.html",
        "description": "An essay on curiosity-driven work and doing what matters.",
        "tags": ["essay", "productivity", "startups"],
        "full_text": "The first step is to decide what to work on. The work you choose "
                     "needs to have three qualities: it has to be something you have a "
                     "natural aptitude for, that you have a deep interest in, and that "
                     "offers scope to do great work. The key to doing great work is to "
                     "be driven by curiosity rather than ambition...",
    },
    {
        "key": "attention_article",
        "title": "The Attention Economy and How It Exploits You",
        "author": "Nir Eyal",
        "original_url": "https://medium.com/attention-economy",
        "description": "How apps use dopamine loops to capture your attention.",
        "tags": ["psychology", "technology", "attention"],
        "full_text": "Variable reward schedules are the engine of addictive design. "
                     "The relationship between dopamine and variable rewards explains "
                     "why social media apps are structurally addictive. The notification "
                     "bell is not a feature, it is a slot machine lever...",
    },
    {
        "key": "deep_work_review",
        "title": "Deep Work by Cal Newport — A Review",
        "author": "Maria Popova",
        "original_url": "https://brainpickings.org/deep-work-review",
        "description": "Why sustained concentration is rare and valuable.",
        "tags": ["books", "productivity", "focus"],
        "full_text": "Newport argues that the attention economy has made sustained "
                     "concentration a scarce skill. Deep work is the ability to focus "
                     "without distraction on a cognitively demanding task...",
    },
    {
        "key": "rlhf_paper",
        "title": "Training Language Models with RLHF",
        "author": "Long Ouyang",
        "original_url": "https://arxiv.org/abs/2203.02155",
        "description": "Reinforcement learning from human feedback for LLM alignment.",
        "tags": ["ai", "machine-learning", "rlhf"],
        "full_text": "We fine-tune GPT-3 to follow instructions using reinforcement "
                     "learning from human feedback. Our labelers rank model outputs "
                     "and we use these rankings to train a reward model...",
    },
    {
        "key": "react_hooks",
        "title": "A Complete Guide to React Hooks",
        "author": "Dan Abramov",
        "original_url": "https://overreacted.io/react-hooks",
        "description": "Understanding useState, useEffect, and custom hooks.",
        "tags": ["react", "javascript", "frontend"],
        "full_text": "Hooks let you use state and other React features without "
                     "writing a class. useState returns a stateful value and a "
                     "function to update it...",
    },
    {
        "key": "nyt_climate",
        "title": "The Climate Crisis Demands Systemic Change",
        "author": "Elizabeth Kolbert",
        "original_url": "https://nytimes.com/climate-systemic-change",
        "description": "Individual action is not enough to address climate change.",
        "tags": ["climate", "politics", "environment"],
        "full_text": "The scale of the climate crisis means that individual choices "
                     "like recycling or driving less, while admirable, are insufficient. "
                     "What is needed is systemic policy change...",
    },
    {
        "key": "stoicism_guide",
        "title": "A Practical Guide to Stoicism",
        "author": "Ryan Holiday",
        "original_url": "https://dailystoic.com/practical-guide",
        "description": "How ancient philosophy applies to modern life.",
        "tags": ["philosophy", "stoicism", "self-improvement"],
        "full_text": "Stoicism teaches us to focus on what we can control and accept "
                     "what we cannot. Marcus Aurelius wrote in his Meditations about "
                     "the importance of present-moment awareness...",
    },
]

# ─────────────────────────────────────────────────────────────
# Eval queries: each tests a different search capability
# ─────────────────────────────────────────────────────────────

EVAL_QUERIES = [
    # ── KEYWORD: short exact terms ──
    {
        "query": "RLHF",
        "expected_intent": "keyword",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "keyword_exact",
    },
    {
        "query": "react hooks",
        "expected_intent": "keyword",
        "expected_article_keys": ["react_hooks"],
        "expected_top1_key": "react_hooks",
        "category": "keyword_exact",
    },
    {
        "query": '"attention economy"',
        "expected_intent": "keyword",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "expected_top1_key": "attention_article",
        "category": "keyword_phrase",
    },

    # ── FILTER: author inference ──
    {
        "query": "Paul Graham",
        "expected_intent": "filter",
        "expected_article_keys": ["pg_essay"],
        "expected_top1_key": "pg_essay",
        "category": "filter_author",
    },
    {
        "query": "Dan Abramov",
        "expected_intent": "filter",
        "expected_article_keys": ["react_hooks"],
        "expected_top1_key": "react_hooks",
        "category": "filter_author",
    },

    # ── FILTER: tag inference ──
    {
        "query": "stoicism",
        "expected_intent": "filter",
        "expected_article_keys": ["stoicism_guide"],
        "expected_top1_key": "stoicism_guide",
        "category": "filter_tag",
    },

    # ── FILTER: domain inference ──
    {
        "query": "nytimes.com",
        "expected_intent": "filter",
        "expected_article_keys": ["nyt_climate"],
        "expected_top1_key": "nyt_climate",
        "category": "filter_domain",
    },

    # ── FILTER: operators ──
    {
        "query": "tag:ai",
        "expected_intent": "filter",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "filter_operator",
    },
    {
        "query": "author:Newport",
        "expected_intent": "filter",
        "expected_article_keys": [],  # No exact author named "Newport" but partial match
        "category": "filter_operator",
    },

    # ── SEMANTIC: natural language questions ──
    {
        "query": "what have I read about habit formation and addiction?",
        "expected_intent": "semantic",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "category": "semantic_question",
    },
    {
        "query": "why is social media addictive?",
        "expected_intent": "semantic",
        "expected_article_keys": ["attention_article"],
        "expected_top1_key": "attention_article",
        "category": "semantic_question",
    },
    {
        "query": "how do I find meaningful work?",
        "expected_intent": "semantic",
        "expected_article_keys": ["pg_essay"],
        "expected_top1_key": "pg_essay",
        "category": "semantic_question",
    },
    {
        "query": "explain reinforcement learning from human feedback",
        "expected_intent": "semantic",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "semantic_question",
    },

    # ── HYBRID: conceptual multi-word queries ──
    {
        "query": "attention and focus productivity",
        "expected_intent": "hybrid",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "category": "hybrid_conceptual",
    },
    {
        "query": "ancient philosophy modern applications",
        "expected_intent": "hybrid",
        "expected_article_keys": ["stoicism_guide"],
        "category": "hybrid_conceptual",
    },

    # ── CROSS-CUTTING: queries that test hybrid advantage ──
    # These are cases where NEITHER pure keyword nor pure semantic alone
    # would get the best results, but together they should.
    {
        "query": "dopamine reward loops",
        "expected_intent": "keyword",  # 3 words, no question
        "expected_article_keys": ["attention_article"],
        "expected_top1_key": "attention_article",
        "category": "cross_cutting",
    },
    {
        "query": "climate policy systemic change individual action",
        "expected_intent": "hybrid",  # 6 words, not a question
        "expected_article_keys": ["nyt_climate"],
        "category": "cross_cutting",
    },
]
```

### Step 6.2 — Create the eval runner

**Create file:** `content-queue-backend/tests/evals/test_search_evals.py`

```python
"""
Search quality evals.

These are NOT unit tests — they measure search quality metrics.
Run with: pytest tests/evals/test_search_evals.py -v -s
(the -s flag prints the eval report to stdout)

Requires:
- Test database with pgvector
- OpenAI API key in env (for semantic/hybrid evals)
- Embeddings generated for eval articles (done in fixture)
"""
import pytest
import time
import json
from collections import defaultdict
from app.core.search_router import classify_query
from app.core.hybrid_search import hybrid_search, keyword_search, get_user_search_context
from app.models.content import ContentItem
from .search_eval_dataset import EVAL_ARTICLES, EVAL_QUERIES


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def eval_articles(db_module, user_module):
    """Seed all eval articles into the DB. Returns key->article mapping."""
    articles = {}
    for spec in EVAL_ARTICLES:
        item = ContentItem(
            original_url=spec["original_url"],
            title=spec["title"],
            author=spec["author"],
            description=spec["description"],
            tags=spec["tags"],
            full_text=spec["full_text"],
            user_id=user_module.id,
            processing_status="completed",
        )
        db_module.add(item)
        db_module.commit()
        db_module.refresh(item)
        articles[spec["key"]] = item
    return articles


@pytest.fixture(scope="module")
def eval_articles_with_embeddings(eval_articles, db_module):
    """
    Generate embeddings for all eval articles.
    Requires OPENAI_API_KEY in env. If not set, skip semantic evals.
    """
    import os
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — skipping embedding-dependent evals")

    from openai import OpenAI
    from app.core.config import settings
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    for key, article in eval_articles.items():
        text = f"{article.title}\n\n{article.description}\n\n{article.full_text}"
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
            encoding_format="float",
        )
        article.embedding = response.data[0].embedding
    db_module.commit()
    return eval_articles


@pytest.fixture(scope="module")
def user_context(db_module, user_module, eval_articles):
    """Load user's authors and tags for the classifier."""
    return get_user_search_context(user=user_module, db=db_module)


# ─────────────────────────────────────────────────────────────
# Eval 1: Classification Accuracy
# ─────────────────────────────────────────────────────────────

class TestClassificationAccuracy:
    """
    Measures whether the query classifier routes queries correctly.
    This eval does NOT need OpenAI — it's pure heuristic logic.
    """

    def test_classification_report(self, user_context, eval_articles):
        user_authors, user_tags = user_context
        correct = 0
        total = 0
        failures = []

        for eq in EVAL_QUERIES:
            result, _ = classify_query(
                eq["query"],
                user_authors=user_authors,
                user_tags=user_tags,
            )
            total += 1
            if result == eq["expected_intent"]:
                correct += 1
            else:
                failures.append(
                    f"  MISS: '{eq['query']}' → got '{result}', expected '{eq['expected_intent']}'"
                )

        accuracy = correct / total if total else 0
        report = [
            "",
            "=" * 60,
            "EVAL: Query Classification Accuracy",
            "=" * 60,
            f"Total queries:  {total}",
            f"Correct:        {correct}",
            f"Accuracy:       {accuracy:.1%}",
        ]
        if failures:
            report.append(f"Failures ({len(failures)}):")
            report.extend(failures)
        report.append("=" * 60)
        print("\n".join(report))

        # Threshold: classifier should get at least 80% right
        assert accuracy >= 0.80, (
            f"Classification accuracy {accuracy:.1%} below 80% threshold.\n"
            + "\n".join(failures)
        )


# ─────────────────────────────────────────────────────────────
# Eval 2: Keyword Search Quality (no OpenAI needed)
# ─────────────────────────────────────────────────────────────

class TestKeywordSearchQuality:
    """
    Measures hit rate and MRR for keyword-classified queries.
    Runs entirely on PostgreSQL tsvector — no API calls.
    """

    def test_keyword_eval_report(self, db_module, user_module, eval_articles):
        keyword_queries = [
            eq for eq in EVAL_QUERIES
            if eq["category"].startswith("keyword")
        ]
        if not keyword_queries:
            pytest.skip("No keyword eval queries")

        hits = 0
        reciprocal_ranks = []
        latencies = []

        article_map = {spec["key"]: eval_articles[spec["key"]] for spec in EVAL_ARTICLES}

        for eq in keyword_queries:
            expected_ids = {
                str(article_map[k].id)
                for k in eq.get("expected_article_keys", [])
                if k in article_map
            }

            start = time.perf_counter()
            results = keyword_search(
                query=eq["query"], user=user_module, db=db_module, limit=10
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

            result_ids = [r["id"] for r in results]

            # Hit rate: any expected article in results?
            if expected_ids and expected_ids & set(result_ids):
                hits += 1

            # MRR: rank of first expected article
            top1_key = eq.get("expected_top1_key")
            if top1_key and top1_key in article_map:
                target_id = str(article_map[top1_key].id)
                if target_id in result_ids:
                    rank = result_ids.index(target_id) + 1
                    reciprocal_ranks.append(1.0 / rank)
                else:
                    reciprocal_ranks.append(0.0)

        hit_rate = hits / len(keyword_queries) if keyword_queries else 0
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

        report = [
            "",
            "=" * 60,
            "EVAL: Keyword Search Quality",
            "=" * 60,
            f"Queries:        {len(keyword_queries)}",
            f"Hit Rate @10:   {hit_rate:.1%}",
            f"MRR:            {mrr:.3f}",
            f"Latency p50:    {p50:.1f}ms",
            f"Latency p95:    {p95:.1f}ms",
            "=" * 60,
        ]
        print("\n".join(report))

        assert hit_rate >= 0.70, f"Keyword hit rate {hit_rate:.1%} below 70%"


# ─────────────────────────────────────────────────────────────
# Eval 3: Hybrid Search Quality (needs OpenAI)
# ─────────────────────────────────────────────────────────────

class TestHybridSearchQuality:
    """
    Full end-to-end eval across ALL query types through hybrid_search().
    Compares hybrid results against expected results.
    Requires OpenAI API key for semantic/hybrid queries.
    """

    def test_hybrid_eval_report(
        self, db_module, user_module, eval_articles_with_embeddings, user_context
    ):
        user_authors, user_tags = user_context
        article_map = eval_articles_with_embeddings

        hits_by_category = defaultdict(lambda: {"hit": 0, "total": 0})
        reciprocal_ranks = []
        latencies = []
        api_calls_avoided = 0

        for eq in EVAL_QUERIES:
            expected_ids = {
                str(article_map[k].id)
                for k in eq.get("expected_article_keys", [])
                if k in article_map
            }
            category = eq["category"]

            start = time.perf_counter()
            results = hybrid_search(
                query=eq["query"],
                user=user_module,
                db=db_module,
                limit=10,
                user_authors=user_authors,
                user_tags=user_tags,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append({"query": eq["query"], "ms": elapsed_ms, "category": category})

            result_ids = [r["id"] for r in results]

            # Track API calls avoided (filter/keyword don't call OpenAI)
            intent, _ = classify_query(
                eq["query"], user_authors=user_authors, user_tags=user_tags
            )
            if intent in ("filter", "keyword"):
                api_calls_avoided += 1

            # Hit rate per category
            hits_by_category[category]["total"] += 1
            if expected_ids and expected_ids & set(result_ids):
                hits_by_category[category]["hit"] += 1

            # MRR
            top1_key = eq.get("expected_top1_key")
            if top1_key and top1_key in article_map:
                target_id = str(article_map[top1_key].id)
                if target_id in result_ids:
                    rank = result_ids.index(target_id) + 1
                    reciprocal_ranks.append(1.0 / rank)
                else:
                    reciprocal_ranks.append(0.0)

        # ── Compute aggregates ──
        total_queries = len(EVAL_QUERIES)
        total_hits = sum(c["hit"] for c in hits_by_category.values())
        total_expected = sum(
            c["total"] for c in hits_by_category.values()
        )
        overall_hit_rate = total_hits / total_expected if total_expected else 0
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0

        lat_values = sorted(l["ms"] for l in latencies)
        p50 = lat_values[len(lat_values) // 2] if lat_values else 0
        p95 = lat_values[int(len(lat_values) * 0.95)] if lat_values else 0

        # ── Print report ──
        report = [
            "",
            "=" * 70,
            "EVAL: Hybrid Search Quality (Full Pipeline)",
            "=" * 70,
            f"Total queries:       {total_queries}",
            f"Overall Hit Rate:    {overall_hit_rate:.1%}",
            f"MRR:                 {mrr:.3f}",
            f"API Calls Avoided:   {api_calls_avoided}/{total_queries} ({api_calls_avoided/total_queries:.0%})",
            f"Latency p50:         {p50:.1f}ms",
            f"Latency p95:         {p95:.1f}ms",
            "",
            "Per-category breakdown:",
        ]
        for cat in sorted(hits_by_category.keys()):
            c = hits_by_category[cat]
            cat_rate = c["hit"] / c["total"] if c["total"] else 0
            cat_latencies = [l["ms"] for l in latencies if l["category"] == cat]
            cat_avg = sum(cat_latencies) / len(cat_latencies) if cat_latencies else 0
            report.append(
                f"  {cat:25s}  hit={cat_rate:5.1%}  ({c['hit']}/{c['total']})  avg={cat_avg:.1f}ms"
            )
        report.append("=" * 70)
        print("\n".join(report))

        # ── Assertions ──
        assert overall_hit_rate >= 0.75, f"Overall hit rate {overall_hit_rate:.1%} below 75%"
        assert mrr >= 0.60, f"MRR {mrr:.3f} below 0.60"
        assert api_calls_avoided >= 5, (
            f"Expected at least 5 queries to avoid API calls, got {api_calls_avoided}"
        )


# ─────────────────────────────────────────────────────────────
# Eval 4: Before/After Comparison (the real eval)
# ─────────────────────────────────────────────────────────────

class TestBeforeAfterComparison:
    """
    Runs the SAME queries through the OLD search path (pure semantic)
    and the NEW search path (hybrid), and compares metrics.

    This is the eval that proves the feature is an improvement.
    """

    def test_comparison_report(
        self, db_module, user_module, eval_articles_with_embeddings, user_context
    ):
        user_authors, user_tags = user_context
        article_map = eval_articles_with_embeddings

        def run_old_search(query: str) -> list[dict]:
            """Simulate old behavior: always semantic, call OpenAI for every query."""
            from app.mcp.tools.content import search_content
            try:
                return search_content(
                    query=query, user=user_module, db=db_module, limit=10
                )
            except Exception:
                return []

        def run_new_search(query: str) -> list[dict]:
            """New behavior: hybrid routing."""
            return hybrid_search(
                query=query,
                user=user_module,
                db=db_module,
                limit=10,
                user_authors=user_authors,
                user_tags=user_tags,
            )

        old_metrics = {"hits": 0, "rr_sum": 0, "rr_count": 0, "latencies": []}
        new_metrics = {"hits": 0, "rr_sum": 0, "rr_count": 0, "latencies": []}

        queries_with_expected = [
            eq for eq in EVAL_QUERIES if eq.get("expected_article_keys")
        ]

        for eq in queries_with_expected:
            expected_ids = {
                str(article_map[k].id)
                for k in eq["expected_article_keys"]
                if k in article_map
            }
            top1_key = eq.get("expected_top1_key")
            target_id = str(article_map[top1_key].id) if top1_key and top1_key in article_map else None

            for label, search_fn, metrics in [
                ("old", run_old_search, old_metrics),
                ("new", run_new_search, new_metrics),
            ]:
                start = time.perf_counter()
                results = search_fn(eq["query"])
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics["latencies"].append(elapsed_ms)

                result_ids = [
                    r.get("id") or r.get("item", {}).get("id", "")
                    for r in results
                ]

                if expected_ids & set(result_ids):
                    metrics["hits"] += 1

                if target_id:
                    metrics["rr_count"] += 1
                    if target_id in result_ids:
                        rank = result_ids.index(target_id) + 1
                        metrics["rr_sum"] += 1.0 / rank

        total = len(queries_with_expected)
        old_hr = old_metrics["hits"] / total if total else 0
        new_hr = new_metrics["hits"] / total if total else 0
        old_mrr = old_metrics["rr_sum"] / old_metrics["rr_count"] if old_metrics["rr_count"] else 0
        new_mrr = new_metrics["rr_sum"] / new_metrics["rr_count"] if new_metrics["rr_count"] else 0

        old_lat = sorted(old_metrics["latencies"])
        new_lat = sorted(new_metrics["latencies"])
        old_p50 = old_lat[len(old_lat) // 2] if old_lat else 0
        new_p50 = new_lat[len(new_lat) // 2] if new_lat else 0
        old_p95 = old_lat[int(len(old_lat) * 0.95)] if old_lat else 0
        new_p95 = new_lat[int(len(new_lat) * 0.95)] if new_lat else 0

        report = [
            "",
            "=" * 70,
            "EVAL: Before/After Comparison",
            "=" * 70,
            f"Queries evaluated:  {total}",
            "",
            f"{'Metric':<25s} {'OLD (semantic-only)':>20s} {'NEW (hybrid)':>20s} {'Delta':>10s}",
            f"{'-'*25:<25s} {'-'*20:>20s} {'-'*20:>20s} {'-'*10:>10s}",
            f"{'Hit Rate @10':<25s} {old_hr:>19.1%} {new_hr:>19.1%} {'+' if new_hr>=old_hr else ''}{(new_hr-old_hr)*100:>+8.1f}pp",
            f"{'MRR':<25s} {old_mrr:>20.3f} {new_mrr:>20.3f} {new_mrr-old_mrr:>+10.3f}",
            f"{'Latency p50':<25s} {old_p50:>18.0f}ms {new_p50:>18.0f}ms {new_p50-old_p50:>+8.0f}ms",
            f"{'Latency p95':<25s} {old_p95:>18.0f}ms {new_p95:>18.0f}ms {new_p95-old_p95:>+8.0f}ms",
            "",
        ]

        # Verdict
        improved = new_hr >= old_hr and new_mrr >= old_mrr
        if improved:
            report.append("VERDICT: Hybrid search is EQUAL or BETTER on all quality metrics.")
        else:
            report.append("VERDICT: Hybrid search REGRESSED on some metrics. Investigate.")

        if new_p50 < old_p50 * 0.5:
            report.append(
                f"BONUS: p50 latency improved by {(1 - new_p50/old_p50)*100:.0f}%."
            )

        report.append("=" * 70)
        print("\n".join(report))

        # The new system should not be worse
        assert new_hr >= old_hr * 0.95, (
            f"Hybrid hit rate ({new_hr:.1%}) regressed vs semantic-only ({old_hr:.1%})"
        )
```

### Step 6.3 — Add eval conftest with module-scoped fixtures

**Create file:** `content-queue-backend/tests/evals/__init__.py` (empty)

**Create file:** `content-queue-backend/tests/evals/conftest.py`

```python
"""
Module-scoped fixtures for eval tests.

Evals need articles to persist across all test methods in a class,
so we use module scope instead of function scope.
"""
import pytest
import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import Base
from app.models.user import User
from app.core.security import get_password_hash


SQLALCHEMY_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue_test",
)

engine = create_engine(SQLALCHEMY_TEST_DATABASE_URL, poolclass=NullPool)


@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    with dbapi_conn.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        dbapi_conn.commit()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def setup_database_module():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="module")
def db_module(setup_database_module):
    session = TestingSessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(text(f"TRUNCATE TABLE {table.name} RESTART IDENTITY CASCADE;"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        with engine.connect() as conn:
            with conn.begin():
                for table in reversed(Base.metadata.sorted_tables):
                    conn.execute(text(f"TRUNCATE TABLE {table.name} RESTART IDENTITY CASCADE;"))


@pytest.fixture(scope="module")
def user_module(db_module):
    u = User(
        email="eval@example.com",
        username="evaluser",
        hashed_password=get_password_hash("password"),
        full_name="Eval User",
        is_active=True,
    )
    db_module.add(u)
    db_module.commit()
    db_module.refresh(u)
    return u
```

### Step 6.4 — How to run evals

```bash
# Classification eval only (no OpenAI needed, fast):
cd content-queue-backend && PYENV_VERSION=3.11.7 \
  /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest \
  tests/evals/test_search_evals.py::TestClassificationAccuracy -v -s

# Keyword search eval (no OpenAI needed):
cd content-queue-backend && PYENV_VERSION=3.11.7 \
  /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest \
  tests/evals/test_search_evals.py::TestKeywordSearchQuality -v -s

# Full eval suite (needs OPENAI_API_KEY):
cd content-queue-backend && PYENV_VERSION=3.11.7 \
  OPENAI_API_KEY=sk-... \
  /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest \
  tests/evals/test_search_evals.py -v -s

# Before/after comparison (the money eval):
cd content-queue-backend && PYENV_VERSION=3.11.7 \
  OPENAI_API_KEY=sk-... \
  /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest \
  tests/evals/test_search_evals.py::TestBeforeAfterComparison -v -s
```

### Example eval output

```
======================================================================
EVAL: Before/After Comparison
======================================================================
Queries evaluated:  17

Metric                    OLD (semantic-only)     NEW (hybrid)      Delta
-------------------------  --------------------  --------------------  ----------
Hit Rate @10                             64.7%                88.2%    +23.5pp
MRR                                      0.529                0.824     +0.295
Latency p50                              412ms                  3ms     -409ms
Latency p95                              583ms                437ms     -146ms

VERDICT: Hybrid search is EQUAL or BETTER on all quality metrics.
BONUS: p50 latency improved by 99%.
======================================================================
```

### Quality thresholds (assertion gates)

| Metric | Minimum | Why |
|--------|---------|-----|
| Classification accuracy | >= 80% | Classifier should correctly route 4 out of 5 queries |
| Keyword hit rate | >= 70% | Keyword queries should find the right article most of the time |
| Overall hybrid hit rate | >= 75% | Hybrid should do better than either engine alone |
| Hybrid MRR | >= 0.60 | Correct result should usually appear in top 2 |
| No regression vs old | new_hr >= old_hr * 0.95 | Hybrid must not make things worse |

### Adding evals to the test execution order

Evals run AFTER all implementation phases pass:

```bash
# ... (phases 1-5 from above) ...

# Phase 6: Evals (run last, after everything is wired up)
pytest tests/evals/test_search_evals.py::TestClassificationAccuracy -v -s
pytest tests/evals/test_search_evals.py::TestKeywordSearchQuality -v -s
pytest tests/evals/test_search_evals.py::TestHybridSearchQuality -v -s       # needs OPENAI_API_KEY
pytest tests/evals/test_search_evals.py::TestBeforeAfterComparison -v -s     # needs OPENAI_API_KEY
```

---

## Acceptance Criteria (How You Know It's Done)

1. **All tests pass** — `pytest tests/ -v` shows 0 failures
2. **Keyword queries are fast** — typing "Paul Graham" or "RLHF" does NOT call OpenAI (verify by checking no `openai` import is triggered in the keyword path)
3. **Filter queries work without operators** — typing a known author name returns that author's articles
4. **Semantic queries still work** — "what have I read about habit formation?" returns embedding-based results (requires OpenAI key in test env)
5. **Hybrid queries merge results** — a 4+ word non-question query returns results from both keyword and semantic search
6. **Frontend unchanged** — SearchBar.tsx makes the same API call and renders the same results, but faster for keyword queries
7. **MCP tool unchanged** — `search_content` in Claude returns the same format but with hybrid routing
8. **Embedding cache works** — repeated identical queries skip the OpenAI call (verify via mock)
