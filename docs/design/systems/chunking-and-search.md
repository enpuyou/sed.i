---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# Chunking, Embeddings, and Search

How sed.i indexes articles and finds them again — from raw HTML to a search result you can click.

---

## The problem this solves

Every article is stored as a single HTML blob in `content_items.full_text`. Early versions embedded that entire blob as one vector using OpenAI. That breaks in two ways:

1. **Long articles get truncated.** OpenAI's embedding model has an 8k token limit. A 5,000-word essay has its last 3,000 words silently dropped before embedding.
2. **Averaging destroys signal.** Even within the limit, embedding one 8k-token passage produces a single vector that is the statistical average of everything in it. If an article is about "Python decorators" but also has a section on "async/await", a query for "async Python" may not surface it — the decorator content dilutes the signal.

The fix: split each article into overlapping chunks, embed each chunk separately, and at query time find articles where *any* chunk is similar to the query.

---

## Step 1 — Splitting (chunking)

**Code:** `content-queue-backend/app/tasks/chunk_embeddings.py` → `split_article_into_chunks(html)`

The function takes the stored HTML and returns a list of plain-text strings. Strategy, in order:

### 1a. Header boundaries

HTML headers (`<h1>–<h4>`) are structural signals that a new topic is starting. The function inserts sentinels before each header tag, then splits on those sentinels. Each split produces one "section."

```
<h2>Introduction</h2><p>Context engineering is…</p>
<h2>Methods</h2><p>We tested three chunking strategies…</p>
```

becomes two sections:

```
section 0: "Introduction  Context engineering is…"
section 1: "Methods  We tested three chunking strategies…"
```

### 1b. Within-section splitting

Target size: **~350 tokens** (~467 words). If a section exceeds that, it's split at sentence boundaries (`.`, `!`, `?` followed by whitespace).

Each split carries a **40-word overlap** into the next chunk. Overlap means the last 40 words of chunk N become the first 40 words of chunk N+1. This prevents a sentence that falls exactly on a boundary from being semantically isolated in one chunk.

### 1c. Short section handling

Sections under 20 words are too small to embed meaningfully. They get merged into the previous chunk. If there's no previous chunk (it's the very first content), the short section is kept as-is rather than discarded.

### Concrete example

Article titled **"Building Better RAG Pipelines"** with this HTML:

```html
<h2>What is RAG?</h2>
<p>Retrieval-augmented generation grounds LLM responses in a document corpus.
   Instead of relying on parametric memory, the model retrieves relevant
   passages at query time and conditions its generation on them.</p>

<h2>Chunking Strategies</h2>
<p>Fixed-size chunking is the simplest approach — split every N tokens.
   It ignores document structure, often splitting sentences mid-thought.</p>
<p>Structure-aware chunking splits at semantic boundaries: headers, paragraphs,
   and sentence endings. This preserves meaning within each chunk and
   produces embeddings with tighter semantic focus.</p>
<p>Contextual retrieval (Anthropic, 2024) prepends a brief article-context
   summary before each chunk prior to embedding. This anchors isolated
   chunks to their source, dramatically improving retrieval precision for
   short or ambiguous passages.</p>
```

Both sections are under 467 words, so no further splitting. Result: **2 chunks**.

```
chunk 0: "What is RAG? Retrieval-augmented generation grounds LLM responses in a document corpus. Instead of relying on parametric memory, the model retrieves relevant passages at query time and conditions its generation on them."

chunk 1: "Chunking Strategies  Fixed-size chunking is the simplest approach — split every N tokens. It ignores document structure, often splitting sentences mid-thought. Structure-aware chunking splits at semantic boundaries: headers, paragraphs, and sentence endings. This preserves meaning within each chunk and produces embeddings with tighter semantic focus. Contextual retrieval (Anthropic, 2024) prepends a brief article-context summary before each chunk prior to embedding. This anchors isolated chunks to their source, dramatically improving retrieval precision for short or ambiguous passages."
```

---

## Step 2 — Contextual prefix (before embedding)

**Code:** `contextual_prefix(chunk_text, article_title, chunk_index, total_chunks)`

Before calling OpenAI, each chunk text is wrapped with a context header:

```
"From the article "Building Better RAG Pipelines" (section 1 of 2): What is RAG? Retrieval-augmented generation grounds LLM…"
```

This is the **Anthropic contextual retrieval** pattern. Without it, chunk 0 is just:

> "Retrieval-augmented generation grounds LLM responses in a document corpus."

That's fine for a query like "what is RAG". But chunk 1 might contain:

> "Fixed-size chunking is the simplest approach — split every N tokens."

If a user queries "chunking approaches", this chunk is highly relevant. But the embedding model sees it as an isolated sentence — it doesn't know it came from an article about RAG pipelines. The prefix anchors it: "this is section 2 of 2 of an article called *Building Better RAG Pipelines*", which makes the embedding much more precise.

**Important:** the prefix is only used when computing the embedding. The stored `text` column in `content_chunks` contains the plain chunk without the prefix. The prefix is ephemeral — used once at indexing time.

---

## Step 3 — Embedding

**Code:** `generate_chunk_embeddings(content_item_id)`

The full batch flow:

1. Load the `ContentItem` from DB — needs `full_text` and `title`.
2. Call `split_article_into_chunks(full_text)` → list of plain-text chunks.
3. Build `texts_to_embed` by applying `contextual_prefix` to each chunk.
4. Call `OpenAI.embeddings.create(model="text-embedding-3-small", input=texts_to_embed)` — **one API call for all chunks**, batch mode.
5. Delete any existing `ContentChunk` rows for this item (idempotency — re-running replaces, never duplicates).
6. Insert one `ContentChunk` row per chunk.

### What gets stored

Table: `content_chunks`

| column | type | example |
|---|---|---|
| `id` | UUID | `a3f1...` |
| `content_item_id` | UUID (FK → content_items) | `b7c2...` |
| `user_id` | UUID (FK → users) | `d9e4...` |
| `chunk_index` | integer | `0`, `1`, `2` |
| `text` | text | `"What is RAG? Retrieval-augmented generation…"` |
| `embedding` | vector(1536) | `[0.023, -0.114, 0.087, … 1536 floats]` |
| `created_at` | timestamp | `2026-05-09T14:22:00Z` |

For the example article above, you get:

```
content_chunks
────────────────────────────────────────────────────────
chunk_index=0  text="What is RAG? Retrieval-augmented…"   embedding=[…]
chunk_index=1  text="Chunking Strategies  Fixed-size…"    embedding=[…]
```

---

## Step 4 — When does this run?

**New articles:** The `generate_embedding` Celery task (which runs after every successful extraction) dispatches `generate_chunk_embeddings_task.delay(content_item_id)` automatically. So every newly saved article gets chunked.

**Old articles (backfill):** `process_all_missing_chunks` is a scanner Celery task that finds items with an item-level embedding but no chunks:

```sql
SELECT DISTINCT ci.id
FROM content_items ci
LEFT JOIN content_chunks cc ON cc.content_item_id = ci.id
WHERE ci.embedding IS NOT NULL
  AND ci.full_text IS NOT NULL
  AND ci.deleted_at IS NULL
  AND cc.id IS NULL          -- no chunks yet
LIMIT 100
```

It dispatches `generate_chunk_embeddings_task.delay` for each found item. Run it once after deploying to backfill older articles. It processes 100 at a time — run again if the library is large.

---

## Step 5 — Search

**Code:** `app/core/hybrid_search.py` → `hybrid_search()` and `_semantic_search()`

There are three search engines. Which ones run depends on the query mode.

### Keyword search (tsvector)

Uses PostgreSQL full-text search. A `search_vector` column on `content_items` is maintained by a trigger — it stores a weighted tsvector combining title, author, description, and tags. Query terms are matched against this index using `websearch_to_tsquery`.

This is **fast** (uses a GIN index, no API calls) and good for exact terms: "transformers", "Andrew Karpathy", tagged:ml.

### Semantic search (pgvector)

1. Embed the query string using `OpenAI.embeddings.create` (same model: `text-embedding-3-small`). Result is a 1536-float vector.
2. Run this SQL:

```sql
WITH chunk_scores AS (
    -- Articles that have chunks: score = MAX similarity across all their chunks
    SELECT cc.content_item_id AS id,
           MAX(1 - (cc.embedding <=> CAST(:q AS vector))) AS similarity
    FROM content_chunks cc
    JOIN content_items ci ON ci.id = cc.content_item_id
    WHERE cc.user_id = :uid AND ci.deleted_at IS NULL AND cc.embedding IS NOT NULL
    GROUP BY cc.content_item_id
),
item_scores AS (
    -- Articles without chunks: fall back to their single item-level embedding
    SELECT ci.id,
           (1 - (ci.embedding <=> CAST(:q AS vector))) AS similarity
    FROM content_items ci
    WHERE ci.user_id = :uid AND ci.deleted_at IS NULL AND ci.embedding IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM content_chunks cc2
          WHERE cc2.content_item_id = ci.id AND cc2.embedding IS NOT NULL
      )
),
combined AS (
    SELECT id, similarity FROM chunk_scores
    UNION ALL
    SELECT id, similarity FROM item_scores
)
SELECT id, similarity FROM combined ORDER BY similarity DESC LIMIT :lim
```

Key insight: **`MAX(similarity)`** — an article scores as well as its *most relevant chunk*. If an article has 10 chunks and only chunk 7 is similar to your query, the article still surfaces. Without chunking, those 10 sections all average together and the relevant signal in chunk 7 gets diluted.

The `<=>` operator is pgvector cosine distance (0 = identical directions, 2 = opposite). `1 - distance` converts it to similarity (1 = perfect match, -1 = opposite).

### Reciprocal Rank Fusion (RRF)

In `mode="full"` (the search modal), all three engines run — keyword, filter (tag/author), and semantic — and their results are merged using RRF:

```
RRF score for article X = Σ  1 / (60 + rank_in_list)
                          across all lists where X appears
```

An article ranked #1 in semantic and #3 in keyword gets:
```
1/(60+1) + 1/(60+3) = 0.01639 + 0.01587 = 0.03226
```

An article only in semantic at #1 gets:
```
1/(60+1) = 0.01639
```

RRF rewards articles that appear in multiple engines without needing score normalization between them (keyword `ts_rank_cd` and cosine similarity have completely different scales — RRF uses only rank position, not magnitude).

### Query routing (mode="auto")

The navbar search (`mode="auto"`) classifies the query first to pick the cheapest path:

- `"unread"`, `"archived"`, `"by paul graham"`, `"tagged:ml"` → **filter only** (no API call, no embedding lookup)
- `"javascript closures"`, `"how neural networks learn"` → **keyword + semantic** (both run, RRF-fused)
- Single-word exact author or tag match → **filter**

The modal (`mode="full"`) skips classification and always runs all three.

---

## End-to-end example

**User saves:** "Building Better RAG Pipelines" (hypothetical article, ~800 words, 3 sections)

1. Extension extracts HTML, POSTs to `POST /content` with `pre_extracted_html`.
2. Backend stores `ContentItem`, dispatches `extract_metadata` task (skipped for extension path) and `generate_embedding` task.
3. `generate_embedding` calls OpenAI, stores a 1536-float vector in `content_items.embedding`.
4. `generate_embedding` dispatches `generate_chunk_embeddings_task`.
5. `generate_chunk_embeddings` splits the HTML into 3 chunks, builds contextual prefixes, calls OpenAI once for all 3, inserts 3 rows into `content_chunks`.

**User searches:** "chunking strategies for RAG"

1. Query is classified as semantic (not a filter/author/tag).
2. Query is embedded → `[0.031, -0.092, …]`.
3. `chunk_scores` CTE: computes cosine similarity between query vector and each of the 3 stored chunk embeddings. Chunk 1 ("Chunking Strategies…") scores 0.87, chunk 0 scores 0.61, chunk 2 scores 0.72. MAX = 0.87 → article gets score 0.87.
4. Article surfaces as the top result.
5. Response returns `ContentItemResponse` (no `full_text`) — just title, description, thumbnail, tags. Frontend renders the card.
6. User clicks → `GET /content/{id}/full` → returns `ContentItemDetail` with `full_text` → reader renders the article.
