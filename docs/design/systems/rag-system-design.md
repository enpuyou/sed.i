---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# sed.i — RAG System Design

How content goes from a URL paste to a searchable, queryable knowledge base. This is a technical system design overview, not an API reference.

---

## The two problems RAG solves

**Problem 1:** "Find articles about X" — the user wants to search their library.
**Problem 2:** "Answer a question using my library" — the MCP `query_library` tool generates SQL and summarizes results.

Both depend on the same indexing pipeline. The difference is what happens at query time.

---

## Ingestion pipeline

Every saved article goes through this sequence. The pipeline is orchestrated by Prefect (when `PREFECT_ENABLED=true`) or a Celery chain (default).

```
User saves URL
    │
    ▼
Phase 1 — Metadata fetch (Celery, synchronous to HTTP request)
    • requests + BeautifulSoup: title, description, author, thumbnail
    • OEmbed / opengraph parsing
    • Sets processing_status = "processing"
    • Returns 200 to user immediately
    │
    ▼
Phase 2 — Full content extraction
    • trafilatura re-fetches URL, extracts clean article HTML
    • PDF: PyMuPDF + YOLO layout detection for figure-aware extraction
    • Extension-submitted content: already has full text, step is no-op
    │
    ▼
Phase 3 — Item-level embedding
    • Input: title + description + full_text (stripped to plain text, ≤8000 tokens)
    • Model: text-embedding-3-small via OpenAI (always — EMBED_PROVIDER=openai)
    • Output: one 1536-dim vector per article
    • Stored: content_items.embedding (pgvector column)
    │
    ▼
Phase 4a — Semantic tagging (parallel with 4b)
    • structured_chat() → TagResponse(tags: list[str])
    • Model: gpt-4o-mini (OpenAI) or nova-micro (Bedrock)
    • Output: 4-6 multi-word labels at two levels:
        - Domain: "distributed systems", "sleep science"
        - Concept: "context window limits", "circadian rhythm disruption"
    • Stored: content_items.tags (text[])
    • After writing tags: upserts each label into tag_embeddings (used for tag similarity queries)
    │
    ▼
Phase 4b — Chunk-level embeddings (parallel with 4a)
    • Splits full_text into ~350-token plain-text sections
    • Structure-aware: splits at HTML header boundaries first, then at sentence boundaries
    • 40-word overlap between adjacent chunks (preserves context at boundaries)
    • Each chunk is prefixed before embedding:
        "From the article 'Title' (section 2 of 7): [chunk text]"
      (Anthropic contextual retrieval — prevents isolated chunks from losing meaning)
    • Batch-embeds all chunks in one API call
    • Stored: content_chunks table (content_item_id, chunk_index, text, embedding vector(1536))
```

---

## What gets stored where

| Table | What | Embedding? |
|---|---|---|
| `content_items` | Full article metadata + full_text | One 1536-dim vector (whole article) |
| `content_chunks` | Plain-text chunks of the article | One 1536-dim vector per chunk |
| `tag_embeddings` | Unique tag labels | One 1536-dim vector per label |
| `highlights` | User-selected text passages | One 1536-dim vector per highlight |

All vectors use the same model (`text-embedding-3-small`) and the same 1536-dim space. This means you can compute cosine distance across all four tables — e.g., finding tags similar to a highlight, or articles similar to a chunk.

---

## Why two levels of embedding (item + chunk)?

**Item embedding** answers: "is this article topically relevant?"
- Works well for broad relevance matching
- Fails when an article is long and only one section is relevant
- Capped at 8000 tokens — a 20,000-word article loses most of its content

**Chunk embedding** answers: "does this specific passage answer the query?"
- Finds articles where a buried section matches, not just the opening
- MAX similarity across all chunks = the article's score
- An article with one highly relevant chunk beats an article that's vaguely on-topic throughout

At query time, semantic search uses chunk embeddings when available, falling back to item-level for articles that haven't been chunked yet.

---

## Query-time: hybrid search

Every search query goes through three signals combined by Reciprocal Rank Fusion (RRF).

```
Query: "attention mechanisms in transformers"
    │
    ├─► Keyword search (tsvector)
    │       • PostgreSQL full-text search on search_vector column
    │       • Weighted: title/author = A, description/tags = B
    │       • Prefix matching for single tokens ("llm" matches "llms")
    │       • Returns ranked list of article IDs
    │
    ├─► Semantic search (pgvector)
    │       • Embed query via text-embedding-3-small
    │       • Query cached in Redis (TTL 1h) by sha256(normalized_query)[:16]
    │       • pgvector cosine distance against content_chunks.embedding
    │       • SQL: score = MAX(1 - (embedding <=> :query_vec)) across chunks per article
    │       • Falls back to content_items.embedding if no chunks exist
    │       • Returns ranked list of article IDs
    │
    └─► Filter layer (applied before RRF)
            • Tag filter: matches items where tag_embeddings has cosine distance < threshold
            • Date filter: after:/before: operators parsed from query string
            • Author filter: detected from user's known author set
```

**RRF fusion** (`k=60`, from the original RRF paper):

```
score(article) = Σ 1 / (60 + rank_in_list)
```

An article ranked #1 in keyword and #5 in semantic beats an article ranked #1 in only one list. This prevents any single signal from dominating.

---

## Query-time: MCP natural language query

`query_library` (MCP tool) is separate from search — it generates and executes SQL rather than doing vector search.

```
User: "What did I read about sleep last month?"
    │
    ▼
LLM generates SQL (gpt-4o / claude-sonnet — the smart model)
    • Given schema description of allowed tables
    • Always produces SELECT with WHERE user_id = :user_id placeholder
    • Never accesses tag_embeddings, users, or refresh_tokens
    │
    ▼
sqlglot validation
    • Parses the SQL — rejects DDL, DML, multi-statement, disallowed tables
    • 500ms statement_timeout set at execution
    │
    ▼
Execute against Postgres
    │
    ▼
If ≤20 rows: LLM summarizes results in plain English (gpt-4o-mini / nova-lite)
If >20 rows: returns raw table
```

This is not RAG in the traditional sense — it's text-to-SQL. The LLM doesn't see document content, only query results.

---

## Similarity connections

Highlights have their own embeddings (`highlights.embedding`). The connections panel uses these to find cross-article links:

```
Highlight embedding → pgvector cosine search → similar highlights in other articles
→ _call_insight(): one-sentence LLM explanation of the connection
→ cached in Redis for 7 days
```

---

## The full embedding dependency graph

```
Article saved
    └─► item embedding ──────────────────────────────► similar articles
    └─► chunk embeddings ─────────────────────────────► precise search
    └─► tag labels ──► tag embeddings ───────────────► tag similarity queries
    └─► (later) highlight embeddings ────────────────► cross-article connections
```

Every arrow is a pgvector cosine query. The model is always the same (`text-embedding-3-small` / OpenAI) so all vectors live in the same space and can be compared across tables.

---

## What this is NOT

- **Not a document QA system.** sed.i doesn't stuff article content into a prompt and ask an LLM to answer questions about it. The LLM only sees: tag extraction prompts, summarization prompts, and SQL generation prompts. Article content is retrieved, not reasoned over.
- **Not a vector-only system.** Keyword search runs alongside semantic search. Pure vector search misses exact matches on author names, specific terms, and recent content.
- **Not a re-ranking system (yet).** RRF is the final ranking step. A cross-encoder re-ranker could be added between RRF output and the final result list if precision matters more than latency.
