"""
TDD tests for multi-chunk embeddings (Feature 3).

Behaviors tested:
- split_article_into_chunks: structure-aware splitting at HTML headers then paragraphs
- Chunks respect target size (~256-400 tokens) with overlap
- Short articles produce a single chunk (no over-splitting)
- generate_chunk_embeddings task creates ContentChunk rows for a content item
- Each chunk gets an embedding via OpenAI batch call
- Re-running the task is idempotent (old chunks replaced)
- contextual_prefix: prepends article title + chunk summary for richer embeddings
"""

from unittest.mock import patch

from app.tasks.chunk_embeddings import split_article_into_chunks, contextual_prefix
from app.models.content import ContentItem


# ---------------------------------------------------------------------------
# Unit tests for the chunking logic (pure function, no DB)
# ---------------------------------------------------------------------------


class TestSplitArticleIntoChunks:
    """split_article_into_chunks splits HTML at structural boundaries."""

    def test_single_short_article_produces_one_chunk(self):
        """An article shorter than target size stays as one chunk."""
        html = "<p>Short article about context engineering.</p>"
        chunks = split_article_into_chunks(html)
        assert len(chunks) == 1
        assert "context engineering" in chunks[0]

    def test_splits_at_header_boundaries(self):
        """Sections separated by <h2> headers are split into chunks; short sections merge."""
        # Use sections long enough that they stay separate (each > _MIN_CHUNK_WORDS)
        long_intro = "This section introduces the topic of context engineering. " * 5
        long_methods = (
            "This section describes the methods used in the empirical study. " * 5
        )
        html = f"<h2>Introduction</h2><p>{long_intro}</p><h2>Methods</h2><p>{long_methods}</p>"
        chunks = split_article_into_chunks(html)
        assert len(chunks) >= 2
        combined = " ".join(chunks)
        assert "introduces" in combined.lower()
        assert "methods" in combined.lower()

    def test_long_section_splits_at_paragraphs(self):
        """A section too long for one chunk splits at paragraph boundaries."""
        # ~600 tokens per paragraph × 3 = well over 400-token target
        long_para = (
            "The context engineering paradigm requires careful consideration of information structure. "
            * 30
        )
        html = "<h2>Deep Dive</h2>" + "".join(f"<p>{long_para}</p>" for _ in range(3))
        chunks = split_article_into_chunks(html)
        assert len(chunks) >= 2

    def test_chunks_are_plain_text(self):
        """Chunks contain plain text, not HTML tags."""
        html = "<h2>Section</h2><p>Content <strong>with</strong> markup.</p>"
        chunks = split_article_into_chunks(html)
        for chunk in chunks:
            assert "<" not in chunk, f"Chunk still contains HTML: {chunk}"

    def test_empty_html_returns_empty_list(self):
        """Empty or whitespace-only HTML returns no chunks."""
        assert split_article_into_chunks("") == []
        assert split_article_into_chunks("   ") == []
        assert split_article_into_chunks("<p></p>") == []


class TestContextualPrefix:
    """contextual_prefix enriches chunk text with article context."""

    def test_prefix_includes_title(self):
        """The prefix mentions the article title."""
        result = contextual_prefix(
            chunk_text="Retrieval augmented generation improves accuracy.",
            article_title="Building Better RAG Pipelines",
            chunk_index=2,
            total_chunks=10,
        )
        assert "Building Better RAG Pipelines" in result
        assert "Retrieval augmented generation" in result

    def test_prefix_does_not_duplicate_text(self):
        """The chunk text appears exactly once in the prefixed result."""
        chunk = "Context engineering is the practice of structuring information."
        result = contextual_prefix(
            chunk_text=chunk,
            article_title="Context Engineering",
            chunk_index=0,
            total_chunks=3,
        )
        assert result.count(chunk) == 1


# ---------------------------------------------------------------------------
# Integration tests: task creates ContentChunk rows
# ---------------------------------------------------------------------------


class TestGenerateChunkEmbeddingsTask:
    """generate_chunk_embeddings Celery task writes ContentChunk rows to DB."""

    def test_task_creates_chunk_rows(self, db_session, test_user):
        """Task produces ContentChunk rows for a content item with full_text."""
        from app.models.chunk import ContentChunk
        from app.tasks.chunk_embeddings import generate_chunk_embeddings

        item = ContentItem(
            original_url="https://example.com/long-article",
            title="Context Engineering for LLMs",
            full_text="<h2>Introduction</h2><p>Context engineering is the art of structuring information for LLM consumption. "
            * 20
            + "</p>"
            "<h2>Methods</h2><p>We evaluate multiple chunking strategies across diverse document types. "
            * 20
            + "</p>",
            user_id=test_user.id,
            processing_status="completed",
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        fake_embedding = [0.1] * 1536
        from app.core.llm_client import EmbedResult

        def fake_embed(texts, **kwargs):
            return EmbedResult(
                embeddings=[fake_embedding] * len(texts),
                model="text-embedding-3-small",
                prompt_tokens=10,
            )

        with patch("app.core.llm_client.llm_client.embed", side_effect=fake_embed):
            generate_chunk_embeddings(str(item.id), db=db_session)

        chunks = (
            db_session.query(ContentChunk)
            .filter(ContentChunk.content_item_id == item.id)
            .all()
        )
        assert len(chunks) >= 1
        assert all(c.embedding is not None for c in chunks)
        assert all(len(c.embedding) == 1536 for c in chunks)
        assert all(c.user_id == test_user.id for c in chunks)

    def test_task_is_idempotent(self, db_session, test_user):
        """Running task twice replaces old chunks, doesn't duplicate them."""
        from app.models.chunk import ContentChunk
        from app.tasks.chunk_embeddings import generate_chunk_embeddings

        item = ContentItem(
            original_url="https://example.com/idempotent",
            title="Idempotency Test",
            full_text="<p>Short content for idempotency test.</p>",
            user_id=test_user.id,
            processing_status="completed",
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        fake_embedding = [0.1] * 1536
        from app.core.llm_client import EmbedResult

        def fake_embed(texts, **kwargs):
            return EmbedResult(
                embeddings=[fake_embedding] * len(texts),
                model="text-embedding-3-small",
                prompt_tokens=10,
            )

        with patch("app.core.llm_client.llm_client.embed", side_effect=fake_embed):
            generate_chunk_embeddings(str(item.id), db=db_session)
            count_after_first = (
                db_session.query(ContentChunk)
                .filter(ContentChunk.content_item_id == item.id)
                .count()
            )
            generate_chunk_embeddings(str(item.id), db=db_session)
            count_after_second = (
                db_session.query(ContentChunk)
                .filter(ContentChunk.content_item_id == item.id)
                .count()
            )

        assert (
            count_after_second == count_after_first
        ), "Re-running task must replace chunks, not append new ones"

    def test_task_skips_items_without_full_text(self, db_session, test_user):
        """Task is a no-op for items that have no full_text yet."""
        from app.models.chunk import ContentChunk
        from app.tasks.chunk_embeddings import generate_chunk_embeddings

        item = ContentItem(
            original_url="https://example.com/no-text",
            title="Pending Article",
            full_text=None,
            user_id=test_user.id,
            processing_status="pending",
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        with patch("app.core.llm_client.llm_client.embed") as mock_embed:
            generate_chunk_embeddings(str(item.id), db=db_session)
            mock_embed.assert_not_called()

        chunks = (
            db_session.query(ContentChunk)
            .filter(ContentChunk.content_item_id == item.id)
            .all()
        )
        assert len(chunks) == 0
