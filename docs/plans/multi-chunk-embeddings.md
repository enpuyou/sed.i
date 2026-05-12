# Plan: Multi-Chunk Embeddings + Better Tagging
Date: 2026-05-08
Status: Draft

## Goal
Replace the single-vector-per-article embedding with multi-chunk embeddings so that search can recall concepts from anywhere in a long article, not just the first 8k tokens. As a downstream improvement, use chunked text to make tagging more accurate by sampling from the full article rather than only the intro 800 words.

## Non-goals
- Does not change the embedding model (text-embedding-3-small stays; model upgrade is a separate decision)
- Does not change highlight embeddings (those are already per-highlight — fine)
- Does not implement ColBERT-style late interaction — uses simpler max-of-chunks similarity
- Does not change the tagging UI (auto-accept behavior stays; confidence gating is a separate initiative)
- Does not add a new ANN index in Phase 1 (add hnsw in Phase 2 once chunk table is populated)

## Current state
One `ContentItem.embedding` vector (1536 dims) per article. Text assembled from title + description + html_to_plain(full_text), truncated at 8,000 tokens. Articles >~6,000 words lose their second half entirely. No `content_chunks` table exists. Embedding indexing: none (full table scan on `<=>` for every semantic search). Tagging LLM prompt uses only first 800 words.

---

## Architecture decisions

**Decision**: Chunking strategy
**Options considered**:

1. Fixed 512-token windows with overlap — simple but ignores structure; cuts mid-sentence
2. Structure-aware recursive splitting — splits at HTML headers first, then paragraphs, then sentences; never cuts mid-word
3. Semantic chunking — embeds every sentence to find topic boundaries; 2-3% better recall than recursive, not worth the ingestion cost
4. Late chunking (embed full doc, then apply boundaries) — overkill for personal library scale

**Recommendation**: Option 2 (structure-aware recursive). Articles have HTML headers — respecting those boundaries is the single biggest quality win per benchmarks (2026 RAG studies). For sections longer than ~400 tokens, recursively split at paragraph then sentence boundaries. Target chunk size: 256-400 tokens, 10-15% overlap (~40 tokens).

**Decision**: Contextual prefix per chunk (Anthropic's contextual retrieval)
**Options**:

1. Embed raw chunk text only
2. Prepend a short LLM-generated context sentence before embedding each chunk: "From article '{title}' about {topic}: this section discusses {what this chunk covers}"

**Recommendation**: Option 2. Reduces retrieval errors ~49% per Anthropic's benchmarks. Adds ~1 Claude Haiku call per chunk at ingest — cheap. Without context, isolated chunks lose meaning ("The company's revenue grew 3% last quarter" — which company?). This addresses the core recall problem.
**Reversibility**: Easy — context prefix is prepended only at embedding time; stored chunk text is unchanged.

**Decision**: How to use chunks at query time
**Options considered**:

1. Max similarity: score an article by the best-matching chunk (`MAX(cosine_sim)`)
2. Weighted average across all chunks
3. Full late interaction (ColBERT-style matrix multiply) — most accurate, much more complex

**Recommendation**: Option 1 (max similarity). Matches the mental model "find an article that contains a passage about X." Simple SQL aggregate.
**Reversibility**: Easy to change the fusion strategy without re-embedding.

**Decision**: Impact on existing `ContentItem.embedding`
**Options**: Keep it as a fast fallback; deprecate it when chunks are full; remove it.
**Recommendation**: Keep it. Search falls back to single-vector if an article has no chunks yet (during migration). Remove in a future cleanup once all items have chunks.

**Decision**: ANN index type
**Options**: ivfflat (approximate, fast to build, less accurate at scale), hnsw (accurate, slower to build, better recall)
**Recommendation**: hnsw in Phase 2. At personal-library scale (<10k chunks) exact scan is fast enough in Phase 1. Add hnsw once the table is populated.

---

## Dependency mapping
- Phase 1 (DB + chunking task) must complete before Phase 2 (search uses chunks)
- Phase 2 (search) must be validated before Phase 3 (tagging uses chunks)
- Phase 3 (tagging) is independent of Phase 2 other than chunked text being available

---

## Phases

### Phase 1 — DB schema + chunk generation task (Priority: P0)

**Goal**: Create `content_chunks` table and a Celery task that splits and embeds new articles.

**Entry criteria**: None.

**Changes**:
1. New Alembic migration: create `content_chunks` table:
   ```sql
   CREATE TABLE content_chunks (
     id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     content_item_id UUID NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
     user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
     chunk_index INTEGER NOT NULL,
     text        TEXT NOT NULL,
     embedding   vector(1536),
     created_at  TIMESTAMPTZ DEFAULT now()
   );
   CREATE INDEX idx_content_chunks_content_item ON content_chunks(content_item_id);
   CREATE INDEX idx_content_chunks_user ON content_chunks(user_id);
   ```
2. `content-queue-backend/app/tasks/embedding.py`: Add `generate_chunk_embeddings(content_item_id)` task:
   - Loads `item.full_text` → `html_to_plain()` → split with tiktoken into 512-token windows, 50-token overlap
   - Batches up to 20 chunks per OpenAI API call (already have batch path from highlight embedding)
   - Deletes any existing chunks for this `content_item_id` first (idempotent)
   - Inserts `ContentChunk` rows
3. `content-queue-backend/app/tasks/embedding.py`: Chain `generate_chunk_embeddings.delay(id)` after `generate_embedding` completes (existing single-vector task continues unchanged).
4. `content-queue-backend/app/models/content.py`: Add `ContentChunk` ORM model.

**Exit criteria**:
- [ ] Migration runs cleanly
- [ ] Saving a new article creates `content_chunks` rows (verify count > 1 for an article > 512 tokens)
- [ ] Each chunk row has a non-null `embedding`
- [ ] Existing `ContentItem.embedding` still populated (no regression)
- [ ] `make lint` passes
- [ ] `pytest tests/test_embedding.py -x -q` passes (add test for chunk count)

**Estimated scope**: 1 migration (~30 lines), ~80 lines in embedding.py, ~20 lines in models.

---

### Phase 2 — Chunk-based semantic search (Priority: P1)

**Goal**: `GET /search/semantic` uses chunk embeddings for recall, falling back to item embeddings if no chunks exist.

**Entry criteria**: Phase 1 complete, at least some articles have chunks.

**Changes**:
1. `content-queue-backend/app/core/hybrid_search.py`: Modify `_semantic_search()` to query `content_chunks` instead of (or in addition to) `content_items.embedding`:
   ```sql
   SELECT c.content_item_id AS id,
          MAX(1 - (c.embedding <=> CAST(:q AS vector))) AS similarity
   FROM content_chunks c
   JOIN content_items ci ON ci.id = c.content_item_id
   WHERE c.user_id = :uid
     AND ci.deleted_at IS NULL
     AND c.embedding IS NOT NULL
   GROUP BY c.content_item_id
   ORDER BY similarity DESC
   LIMIT :lim
   ```
   Fall back to item-level embedding query for items with no chunks.
2. Add hnsw index on `content_chunks.embedding`:
   ```sql
   CREATE INDEX ON content_chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
   ```
   Add as a second migration; run after chunk table is populated.
3. Run a backfill task to generate chunks for all existing articles (`process_all_missing_chunks` scanner, similar to `process_all_missing_embeddings`).

**Exit criteria**:
- [ ] Searching for a phrase from the second half of a long article surfaces that article
- [ ] Search latency is not measurably slower than before (log query time)
- [ ] `make lint` passes
- [ ] `pytest tests/test_search_api.py -x -q` passes

**Estimated scope**: ~60 lines in hybrid_search.py, 1 new migration for hnsw index, ~20 lines for backfill task.

---

### Phase 3 — Better tagging via chunked content (Priority: P2)

**Goal**: LLM tagging samples from representative chunks across the full article rather than only the first 800 words.

**Entry criteria**: Phase 1 complete (chunks exist).

**Changes**:
1. `content-queue-backend/app/tasks/tagging.py`: Update `generate_tags_with_llm()` to, if chunks exist for the item, select the 3 highest-embedding-norm chunks (proxy for information density) and use their text instead of `first 800 words of html_to_plain(full_text)`. Cap total tokens at 1,500 (3 × 500 words).
2. Preserve the existing 800-word fallback for items without chunks.
3. `content-queue-backend/app/tasks/embedding.py`: Chain `generate_tags.delay(id)` after `generate_chunk_embeddings` rather than after `generate_embedding`, so tagging fires once chunks are available.

**Exit criteria**:
- [ ] An article about "context engineering" gets the tag "context engineering" not just "AI"
- [ ] `make lint` passes
- [ ] `pytest tests/test_tagging.py -x -q` passes

**Estimated scope**: ~30 lines in tagging.py.

---

## Risks

**Risk**: Chunk table grows very large; exact scan becomes slow
**Likelihood**: Low at personal scale; Medium at multi-user scale
**Mitigation**: Phase 2 hnsw index; exact scan is acceptable for <50k chunks
**Detection**: Monitor query time in logs; add timing instrumentation

**Risk**: OpenAI cost increase from chunk batching
**Likelihood**: Medium — 10 chunks/article means 10x more embedding API calls
**Mitigation**: Batch 20 chunks per API call (already in highlight path); total tokens per article stays the same (same text, just split differently)

**Risk**: Backfill task saturates Celery worker queue
**Likelihood**: Medium for large existing libraries
**Mitigation**: Backfill task runs at low priority with a `countdown` delay between batches; rate-limit to N items/minute

---

## Verification

### Automated
- `pytest tests/test_embedding.py` — chunk count, chunk overlap, embedding shape
- `pytest tests/test_search_api.py` — semantic search returns results from second half of long article
- `pytest tests/test_tagging.py` — tags reflect content from non-intro sections

### Manual
- Save the Anthropic "context window" article. Search "context window scaling" — should surface it
- Check tags assigned to it — should include something more specific than just "AI"

---

## Open questions
- Chunk size tuning: 512 tokens is a starting point. Should be validated against a test set of your saved articles.
- Backfill priority: schedule it only for articles saved after a certain date, or all?
- Phase 2: Consider storing chunk text for reuse in tagging and future features (avoids re-splitting)
