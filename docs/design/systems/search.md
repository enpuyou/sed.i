---
type: design
status: active
last_updated: 2026-07-08
consumer: both
---

# Search — Indexing and Retrieval

How content goes from a URL paste to a search result. Covers the ingestion pipeline,
every data structure used for retrieval, and how the four search lanes fuse results.

---

## Ingestion pipeline

Every saved article runs this sequence as a Celery chain:

```
User saves URL
    │
    ▼
Phase 1 — Metadata fetch (fast, returns 200 immediately)
    • requests + BeautifulSoup: title, description, author, thumbnail
    • Sets processing_status = "processing"
    │
    ▼
Phase 2 — Full content extraction
    • trafilatura re-fetches URL, extracts clean article HTML
    • PDF: PyMuPDF + YOLO layout detection
    • Extension-submitted content: already has full text, step is no-op
    │
    ▼
Phase 3 — Item-level embedding
    • Input: title + description + full_text (stripped to plain text, ≤8000 tokens)
    • Model: text-embedding-3-small (OpenAI)
    • Output: one 1536-dim vector stored in content_items.embedding
    │
    ▼
Phase 4 — analyze_article (single gpt-4o call)
    • Domain tags (1-2): field, e.g. "AI safety research"
    • Concept tags (3-4): specific ideas, e.g. "scalable oversight"
    • Entities (3-8): named people, orgs, tools, concepts, papers
    • Relations (0-4): directed edges between entity pairs with predicate text
    • Tags written to content_items.tags; DB trigger rebuilds search_vector
    • Entities upserted into entities table; mentions linked via entity_mentions
    │
    ▼
Phase 5 — Chunk embeddings (parallel with Phase 4)
    • Splits full_text into ~350-token plain-text sections
    • Structure-aware: splits at HTML header boundaries first, then sentence boundaries
    • 40-word overlap between adjacent chunks
    • Each chunk prefixed before embedding:
        "From the article 'Title' (section 2 of 7): [chunk text]"
      (Anthropic contextual retrieval — anchors isolated chunks to their source)
    • Batch-embeds all chunks in one API call
    • Stored: content_chunks table (content_item_id, chunk_index, text, embedding)
    │
    ▼
Phase 6 — Entity embedding (async, after Phase 4)
    • Any entities written in Phase 4 without embeddings get embedded
    • embed_new_entities_task runs hourly as a beat task as a catch-all
```

---

## Data structures

Five tables participate in search. They share the same embedding space
(`text-embedding-3-small`, 1536 dims) so cosine distance is meaningful across them.

| Table | What it stores | Embedding of |
|---|---|---|
| `content_items` | Full article: title, text, tags, metadata | Whole article text (item-level) |
| `content_chunks` | 350-token sections of each article | Each chunk with contextual prefix |
| `tag_embeddings` | Unique tag label strings | The tag string itself |
| `entities` | Named nodes per user (people, orgs, tools, concepts) | The entity name string |
| `entity_mentions` | Article → entity links with context sentence | — |
| `entity_relations` | Entity → entity directed edges with predicate text | — |

Tags and entities are separate systems. A tag is a string label on an article. An entity
is a named node in a graph with edges to articles (mentions) and to other entities
(relations). There is currently no automatic link between the two.

### Why two levels of article embedding (item + chunk)

Item embedding answers "is this article topically relevant?" It breaks for long articles
(capped at 8k tokens) and for queries that only match one buried section.

Chunk embedding answers "does this specific passage answer the query?" At query time,
an article's score is `MAX(similarity across all its chunks)` — one highly relevant chunk
beats an article that's vaguely on-topic throughout.

Semantic search uses chunk embeddings when available, falling back to item-level for
articles not yet chunked.

---

## Search: four lanes + RRF fusion

Every search runs up to four parallel lookups. `mode="full"` (search modal) runs all
four. `mode="auto"` (navbar) classifies the query first and skips the embed API call
for filter-only queries.

### Lane 1 — Keyword (tsvector)

PostgreSQL full-text search on `search_vector`. The column is maintained by a DB trigger
combining title (weight A), author (A), description (B), and tags (B). Fast — GIN index,
no API call. Good for exact terms and author names.

### Lane 2 — Semantic (pgvector)

Query is embedded via OpenAI (result cached in Redis by `sha256(query)[:16]`, TTL 1h).
The SQL finds the best-matching chunk per article:

```sql
WITH chunk_scores AS (
    SELECT cc.content_item_id AS id,
           MAX(1 - (cc.embedding <=> CAST(:q AS vector))) AS similarity
    FROM content_chunks cc
    JOIN content_items ci ON ci.id = cc.content_item_id
    WHERE cc.user_id = :uid AND ci.deleted_at IS NULL
    GROUP BY cc.content_item_id
),
item_scores AS (
    -- fallback for articles with no chunks yet
    SELECT ci.id, 1 - (ci.embedding <=> CAST(:q AS vector)) AS similarity
    FROM content_items ci
    WHERE ci.user_id = :uid AND ci.deleted_at IS NULL AND ci.embedding IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM content_chunks cc2 WHERE cc2.content_item_id = ci.id)
)
SELECT id, similarity FROM chunk_scores
UNION ALL
SELECT id, similarity FROM item_scores
ORDER BY similarity DESC LIMIT :lim
```

### Lane 3 — Filter

Handles explicit operators: `after:2025-01-01`, `tag:context engineering`, `author:X`.
Only path that does a direct `WHERE 'tag' = ANY(tags)` SQL lookup. Applied before RRF
as a pre-filter when present.

### Lane 4 — Entity

See [entity-graph-search.md](entity-graph-search.md) for the full algorithm. Summary:

1. Embed query
2. Find top-8 entity nodes by cosine similarity to query embedding
3. Gate: if top entity sim < 0.55 → return [] immediately
4. Score articles: Σ(anchor_sim / log2(2 + entity_article_count)) — IDF-like dampening
5. 1-hop expansion: strong anchors (sim ≥ 0.60, article_count ≤ 4) contribute neighbor articles at half weight

### Fusion — Reciprocal Rank Fusion

```
score(article) = Σ  1 / (k + rank_in_list)
                 across all lanes where article appears
```

`k=60` for keyword, semantic, and filter lanes. `k=120` for entity lane (half-weight —
entity results are additive signals, not primary ranking).

An article ranked #1 in two lanes beats one ranked #1 in only one. No score
normalization needed because RRF uses rank position only.

---

## MCP natural language query (`query_library`)

Separate from search — generates and executes SQL rather than doing vector search.

```
User asks: "What did I read about sleep last month?"
    │
    ▼
LLM generates SQL (gpt-4o)
    • Given schema description of allowed tables
    • Always includes WHERE user_id = :user_id placeholder
    │
    ▼
sqlglot validation
    • Parses AST — rejects DDL, DML, multi-statement, disallowed tables
    • Every user-scoped table must have its own direct filter (no cross-table propagation)
    │
    ▼
Execute with 500ms statement_timeout
    │
    ▼
≤20 rows → LLM summarizes in plain English
>20 rows → returns raw table
```

The LLM does not see article content — only query results. This is text-to-SQL, not RAG.

---

## Connections (highlight-to-highlight similarity)

Highlights have their own embeddings (`highlights.embedding`). The connections panel uses
pgvector cosine search to find cross-article links, then generates a one-sentence insight
via `_call_insight()`. Insights are cached in Redis for 7 days.

---

## Backfill

Old articles missing chunks: `process_all_missing_chunks` scans for items with an
item-level embedding but no chunk rows, dispatches `generate_chunk_embeddings_task` for
each. Processes 100 at a time — run again for large libraries.

Old articles missing entity analysis: `backfill_missing_entities` daily beat task scans
for items where `entities_analyzed_at IS NULL` and re-runs article analysis.
