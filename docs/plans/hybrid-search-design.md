# Hybrid Search Design: sed.i

> Generated 2026-04-02 from SOTA research across Notion, Obsidian, Supabase, ParadeDB, Qdrant, Pinecone, and Chroma.

---

## Problem Statement

sed.i's current search is **semantic-only**: every query goes through OpenAI embedding + pgvector cosine distance. This creates three problems:

1. **Slow for simple queries** — typing "Paul Graham" still costs a 300-500ms OpenAI API round trip, when a 2ms SQL `ILIKE` would be more accurate
2. **Wrong tool for structured queries** — searching by author, source domain, tag, or date can't work through embeddings
3. **Article-level granularity** — search returns whole articles, not the specific passage that matched. A 5,000-word article gets one embedding vector; the signal from a key paragraph gets averaged away

The goal: **make in-app search fast and accurate enough for 90% of queries, and delegate the remaining 10% (complex, multi-step, analytical) to Claude via MCP.**

---

## Architecture Overview

```
User types in SearchBar
        │
        ▼
┌─────────────────────┐
│  Query Classifier    │  < 0.1ms (heuristic, no LLM)
│  (regex + rules)     │
└─────┬───────────────┘
      │
      ├── "filter"   → Structured SQL (author, tag, date, source)     ~2ms
      ├── "keyword"  → PostgreSQL full-text search (tsvector + BM25)  ~2ms
      ├── "semantic" → OpenAI embedding + pgvector                    ~400ms
      └── "hybrid"   → Keyword + Semantic merged via RRF              ~400ms
                                                                (keyword path: ~2ms)
        │
        ▼
┌─────────────────────┐
│  Results (articles   │
│  OR passages)        │
└─────────────────────┘
```

**Key principle:** Only call OpenAI when the query actually needs semantic understanding. Everything else stays in PostgreSQL at single-digit millisecond latency.

---

## Part 1: Query Intent Classification

### How the App Decides What Type of Search to Run

No LLM call. Pure heuristics. Under 0.1ms.

```python
import re

def classify_query(query: str) -> str:
    q = query.strip()

    # 1. FILTER — structured operators (author:, tag:, site:, is:, before:, after:)
    if re.search(r'(is:|tag:|type:|author:|before:|after:|site:|from:)', q, re.IGNORECASE):
        return "filter"

    # 2. EXACT PHRASE — user wrapped in quotes
    if re.search(r'"[^"]+"', q):
        return "keyword"

    # 3. SHORT KEYWORD — 1-3 words, no question words
    words = q.split()
    question_words = {'how', 'what', 'why', 'which', 'when', 'where',
                      'who', 'explain', 'describe', 'find', 'show'}
    if len(words) <= 3 and not any(w.lower() in question_words for w in words):
        return "keyword"

    # 4. QUESTION — interrogative or ends with ?
    if q.endswith('?') or re.match(
        r'^(how|what|why|which|when|where|who|explain|describe|tell me|find me|show me)\b',
        q, re.IGNORECASE
    ):
        return "semantic"

    # 5. DEFAULT — run both, merge with RRF
    return "hybrid"
```

### What Each Classification Means

| Query Example | Classification | What Runs | Latency |
|---|---|---|---|
| `author:Paul Graham` | filter | SQL WHERE clause | ~2ms |
| `tag:AI is:unread` | filter | SQL WHERE clause | ~2ms |
| `site:newyorker.com` | filter | SQL ILIKE on original_url | ~2ms |
| `"attention economy"` | keyword | tsvector full-text search | ~2ms |
| `RLHF` | keyword | tsvector full-text search | ~2ms |
| `react hooks` | keyword | tsvector full-text search | ~2ms |
| `what have I read about habit formation?` | semantic | OpenAI embed + pgvector | ~400ms |
| `why do social media apps feel addictive` | semantic | OpenAI embed + pgvector | ~400ms |
| `articles about attention and dopamine` | hybrid | keyword + semantic + RRF | ~400ms |
| `effective altruism criticism` | hybrid | keyword + semantic + RRF | ~400ms |

**Why this works:** Most quick searches are 1-3 words ("react", "paul graham", "AI ethics"). Those go straight to keyword search at 2ms. Only natural language questions and longer conceptual queries need the embedding API call.

---

## Part 2: PostgreSQL Full-Text Search (New)

### What We Add

A generated `tsvector` column on `content_items` that indexes title, description, author, and tags. No new extension needed — this is built into PostgreSQL.

```sql
-- Migration: add full-text search column
ALTER TABLE content_items ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(author, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(array_to_string(tags, ' '), '')), 'B')
    ) STORED;

CREATE INDEX idx_content_items_fts ON content_items USING gin(search_vector);
```

**Weights:** Title and author matches rank higher (A) than description and tags (B). This means searching "Paul Graham" surfaces his articles above articles that merely mention him.

### Why Not BM25 (ParadeDB pg_search)?

ParadeDB's pg_search extension gives true BM25 scoring — better ranking than tsvector's `ts_rank_cd`. On 1M rows it's 20x faster.

**But:** It's an external extension. At sed.i's scale (hundreds to low-thousands of articles per user), tsvector is fast enough and ships with PostgreSQL. If ranking quality becomes an issue, pg_search is the upgrade path.

---

## Part 3: Hybrid Search with RRF

### Reciprocal Rank Fusion — How It Works

When a query is classified as "hybrid", we run **both** keyword and semantic search, then merge results using RRF:

```
RRF_score(article) = 1/(60 + keyword_rank) + 1/(60 + semantic_rank)
```

An article ranked #1 in keyword and #3 in semantic:
```
= 1/(60+1) + 1/(60+3) = 0.0164 + 0.0159 = 0.0323
```

An article ranked #1 in semantic only (not in keyword results):
```
= 0 + 1/(60+1) = 0.0164
```

The first article wins because it appeared in both lists. RRF naturally boosts articles that are relevant by multiple measures.

**Why not weighted scores (0.7 * BM25 + 0.3 * cosine)?** BM25 scores and cosine similarity are on completely different scales. Normalizing them is fragile and changes meaning across queries. RRF uses only rank positions — scale-invariant and robust.

### PostgreSQL Implementation

```sql
CREATE OR REPLACE FUNCTION hybrid_search(
    query_text text,
    query_embedding vector(1536),
    p_user_id uuid,
    match_count int DEFAULT 10,
    rrf_k int DEFAULT 60
)
RETURNS TABLE (
    id uuid,
    score float
)
LANGUAGE sql STABLE
AS $$
WITH keyword_results AS (
    SELECT
        ci.id,
        ROW_NUMBER() OVER (
            ORDER BY ts_rank_cd(ci.search_vector,
                websearch_to_tsquery('english', query_text)) DESC
        ) AS rank_ix
    FROM content_items ci
    WHERE ci.search_vector @@ websearch_to_tsquery('english', query_text)
        AND ci.user_id = p_user_id
        AND ci.deleted_at IS NULL
    ORDER BY rank_ix
    LIMIT match_count * 3
),
semantic_results AS (
    SELECT
        ci.id,
        ROW_NUMBER() OVER (
            ORDER BY ci.embedding <=> query_embedding
        ) AS rank_ix
    FROM content_items ci
    WHERE ci.user_id = p_user_id
        AND ci.deleted_at IS NULL
        AND ci.embedding IS NOT NULL
    ORDER BY rank_ix
    LIMIT match_count * 3
)
SELECT
    COALESCE(k.id, s.id) AS id,
    (
        COALESCE(1.0 / (rrf_k + k.rank_ix), 0.0) +
        COALESCE(1.0 / (rrf_k + s.rank_ix), 0.0)
    )::float AS score
FROM keyword_results k
FULL OUTER JOIN semantic_results s ON k.id = s.id
ORDER BY score DESC
LIMIT match_count;
$$;
```

**Tested performance:** ~8.5ms on 50K rows (Jonathan Katz benchmark, PostgreSQL core contributor). At sed.i's scale, expect <5ms for the SQL portion.

---

## Part 4: Filter Search (New)

### Structured Queries

When the classifier detects operators like `author:`, `tag:`, `site:`, the query is parsed into SQL filters:

```python
def parse_filter_query(query: str) -> dict:
    filters = {}
    patterns = {
        'author': r'author:(\S+|"[^"]+")',
        'tag': r'tag:(\S+|"[^"]+")',
        'site': r'site:(\S+)',
        'is': r'is:(read|unread|archived)',
        'before': r'before:(\d{4}-\d{2}-\d{2})',
        'after': r'after:(\d{4}-\d{2}-\d{2})',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            filters[key] = match.group(1).strip('"')
    return filters
```

Translates to:
```sql
WHERE author ILIKE '%Paul Graham%'
  AND 'AI' = ANY(tags)
  AND original_url ILIKE '%newyorker.com%'
  AND is_read = false
  AND created_at >= '2026-03-01'
```

**Latency:** ~2ms. No API calls.

### What the User Sees

The search bar accepts natural operators:
- `author:Graham` — articles by authors matching "Graham"
- `tag:AI` — articles tagged with AI
- `site:nytimes.com` — articles from NYT
- `is:unread` — unread articles
- `before:2026-01-01` — saved before a date
- Combinable: `author:Graham tag:startup is:unread`

These could also be surfaced as filter chips/buttons in the UI, translating clicks into the same structured query.

---

## Part 5: Chunked Passage Search (Phase 2)

### Why Chunks Matter

Current state: a 5,000-word article about "the history of computing, with a section on attention mechanisms" gets one embedding. Searching "attention mechanisms" returns it, but you don't know *where* in the article the match is — and the similarity score is diluted by the 4,500 words about computing history.

With chunks: the paragraph about attention mechanisms has its own embedding. It matches strongly and the app can show the user exactly that passage.

### Chunk Strategy for Articles

Based on Chroma Research benchmarks and the Obsidian hybrid search project:

- **Size:** 200-256 tokens (~1-2 paragraphs)
- **Overlap:** None (with `text-embedding-3-small`, overlap doesn't improve recall and wastes storage)
- **Splitting:** Prefer paragraph boundaries (`\n\n`), fall back to sentence boundaries
- **Heading context:** Store the nearest parent heading with each chunk for display

### Database Schema

```sql
CREATE TABLE content_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id uuid NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    chunk_index int NOT NULL,
    chunk_text text NOT NULL,
    heading_context text,            -- "## The Attention Economy"
    char_offset_start int NOT NULL,  -- position in original full_text
    char_offset_end int NOT NULL,
    token_count int,
    embedding vector(1536),
    content_hash char(16),           -- xxhash of chunk_text, skip re-embed if unchanged

    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', chunk_text)
    ) STORED
);

CREATE INDEX idx_chunks_content_item ON content_chunks(content_item_id);
CREATE INDEX idx_chunks_user ON content_chunks(user_id);
CREATE INDEX idx_chunks_fts ON content_chunks USING gin(search_vector);
CREATE INDEX idx_chunks_embedding ON content_chunks USING hnsw(embedding vector_cosine_ops);
```

### Search Query (Passage-Level)

```sql
SELECT
    cc.chunk_text,
    cc.heading_context,
    cc.char_offset_start,
    cc.char_offset_end,
    ci.id AS article_id,
    ci.title AS article_title,
    (1 - (cc.embedding <=> CAST(:query_embedding AS vector))) AS similarity
FROM content_chunks cc
JOIN content_items ci ON cc.content_item_id = ci.id
WHERE cc.user_id = :user_id
    AND ci.deleted_at IS NULL
    AND cc.embedding IS NOT NULL
ORDER BY cc.embedding <=> CAST(:query_embedding AS vector)
LIMIT :limit
```

### Frontend: What the User Sees

Search results change from article cards to **passage cards with article context:**

```
┌─────────────────────────────────────────────────────────────┐
│ The Attention Economy                                       │
│ paulgraham.com · 12 min · 93% match                         │
│                                                             │
│ § The Dopamine Loop                                         │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ "...the relationship between dopamine and variable      │ │
│ │ reward schedules explains why social media apps are     │ │
│ │ structurally addictive. The notification bell isn't     │ │
│ │ a feature — it's a slot machine lever..."               │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│                              [Open at this passage →]       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Deep Work (Cal Newport review)                              │
│ nytimes.com · 8 min · 87% match                             │
│                                                             │
│ § Why Focus Is Rare                                         │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ "Newport argues that the attention economy has made     │ │
│ │ sustained concentration a scarce and therefore          │ │
│ │ valuable skill..."                                      │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│                              [Open at this passage →]       │
└─────────────────────────────────────────────────────────────┘
```

Clicking "Open at this passage" navigates to `/content/{article_id}?highlight={char_offset_start}-{char_offset_end}` and the reader scrolls to and highlights that range — using the same highlighting mechanism that already exists for user highlights.

---

## Part 6: Embedding Cache (Quick Win)

### Problem

Every search that needs semantic matching calls OpenAI's embedding API (~300-500ms). The same query typed twice generates the same embedding twice.

### Solution

Cache in Redis (already in the stack):

```python
import hashlib, json

EMBEDDING_CACHE_TTL = 3600  # 1 hour

async def get_or_create_query_embedding(query: str, redis_client, openai_client) -> list[float]:
    cache_key = f"qemb:{hashlib.sha256(query.lower().strip().encode()).hexdigest()[:16]}"

    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=query,
        encoding_format="float",
    )
    embedding = response.data[0].embedding
    await redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))
    return embedding
```

**Impact:** Repeated searches drop from ~400ms to <1ms. First search is unchanged.

---

## Part 7: Content Hash for Smart Re-Embedding (From Notion)

### The Pattern

Notion uses xxHash to fingerprint each chunk. When an article is re-processed (metadata update, re-extraction), only chunks whose text actually changed get re-embedded.

```python
import xxhash

def chunk_hash(text: str) -> str:
    return xxhash.xxh64(text.encode()).hexdigest()[:16]

# During chunking:
for chunk in new_chunks:
    existing = db.query(ContentChunk).filter(
        content_item_id=article.id,
        chunk_index=chunk['index'],
        content_hash=chunk_hash(chunk['text'])
    ).first()
    if existing:
        continue  # skip, text unchanged
    # else: embed and upsert
```

**Impact:** Marking an article as read, changing tags, or updating metadata doesn't trigger expensive re-embedding. Notion reported 70% reduction in embedding pipeline volume.

---

## Part 8: In-App vs. Claude Delegation

### The Boundary

| Capability | In-App Search | Claude (MCP) |
|---|---|---|
| Find articles by topic/keyword | Yes | Overkill |
| Find articles by author/source/date | Yes | Overkill |
| Find the passage about X | Yes (with chunks) | Overkill |
| "What themes connect my saved articles about AI?" | No | Yes |
| "Summarize what I've read about climate this month" | No | Yes |
| "Compare the arguments in these 3 articles" | No | Yes |
| "What should I read next based on my interests?" | No | Yes |

**The line:** In-app search retrieves. Claude synthesizes, compares, and reasons.

The MCP tools (`search_content`, `find_similar`, `get_content_item`) are already the retrieval layer Claude uses. Improving in-app search quality automatically improves Claude's retrieval quality too — the same `hybrid_search` function serves both the SearchBar and the MCP `search_content` tool.

---

## Latency Summary

| Query Type | Current | After This Design |
|---|---|---|
| `author:Graham` | ~400ms (misrouted to semantic) | **~2ms** |
| `RLHF` | ~400ms (semantic, may miss) | **~2ms** (keyword, exact match) |
| `react hooks` | ~400ms | **~2ms** (keyword) |
| `"attention economy"` | ~400ms | **~2ms** (keyword, exact phrase) |
| `tag:AI is:unread` | Not possible | **~2ms** (filter) |
| `articles about habit formation` | ~400ms | **~400ms** (hybrid, but better results via RRF) |
| `why is social media addictive` | ~400ms | **~400ms** (semantic, same as current) |
| Repeated semantic query | ~400ms | **<1ms** (Redis cache hit) |

**Net effect:** Most quick, daily searches drop from ~400ms to ~2ms. Conceptual/natural language searches stay the same latency but return better results. Repeated searches become instant.

---

## Implementation Phases

### Phase 1: Hybrid Search Foundation (Small)

**Backend changes:**
1. Migration: add `search_vector` tsvector column + GIN index to `content_items`
2. New file: `app/core/search_router.py` — query classifier (the regex function above)
3. New file: `app/core/hybrid_search.py` — RRF fusion function
4. Update `app/api/search.py` `/semantic` endpoint to route through classifier
5. Update `app/mcp/tools/content.py` `search_content` to use hybrid search
6. Add Redis embedding cache

**Frontend changes:**
- None. SearchBar already calls the same endpoint. Results just get faster and more accurate.

**Estimated scope:** ~3 files new, ~2 files modified (backend only)

### Phase 2: Filter Search (Small)

**Backend changes:**
1. New endpoint: `GET /search/filter` or extend `/search/semantic` with query params
2. Filter parser in `search_router.py`

**Frontend changes:**
- Optional: filter chips in SearchBar UI
- Optional: show detected query type indicator ("searching by keyword..." / "searching by meaning...")

**Estimated scope:** ~1 file new, ~2 files modified

### Phase 3: Chunked Passage Search (Medium)

**Backend changes:**
1. Migration: `content_chunks` table + indexes
2. Update `app/tasks/embedding.py`: chunk articles at paragraph boundaries, embed each chunk, store with char offsets
3. New endpoint: `GET /search/passages` — chunk-level hybrid search
4. Content hash (xxHash) for smart re-embedding
5. Backfill task: chunk + embed all existing articles

**Frontend changes:**
1. New search result card component showing matched passage text + heading context
2. Article reader: accept `?highlight=start-end` query param, scroll to and highlight that range

**Estimated scope:** ~4 files new, ~3 files modified

---

## References

- [Jonathan Katz: Hybrid Search with PostgreSQL and pgvector](https://jkatz05.com/post/postgres/hybrid-search-postgres-pgvector/) — RRF implementation, 8.5ms benchmark on 50K rows
- [Supabase: Hybrid Search Documentation](https://supabase.com/docs/guides/ai/hybrid-search) — PostgreSQL-native hybrid search function
- [ParadeDB: Hybrid Search in PostgreSQL — The Missing Manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) — BM25 vs tsvector comparison
- [Notion: Two Years of Vector Search](https://www.notion.com/blog/two-years-of-vector-search-at-notion) — xxHash chunk caching, 70% pipeline reduction
- [Chroma Research: Evaluating Chunking](https://www.trychroma.com/research/evaluating-chunking) — 200 tokens / no overlap as optimal baseline
- [Blake Crosley: Hybrid Retriever for 16,894 Obsidian Files](https://blakecrosley.com/blog/hybrid-retriever-obsidian) — end-to-end hybrid at 23ms, heading-aware chunking
- [Qdrant: Hybrid Search with Query API](https://qdrant.tech/articles/hybrid-search/) — why linear score combination fails, use RRF
- [Tiger Data: pg_textsearch BM25](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres) — 1-2ms queries on 100K rows
- [nixiesearch: Embedding API Latency Benchmarks](https://nixiesearch.substack.com/p/benchmarking-api-latency-of-embedding) — OpenAI p50 300-500ms
- [Cormack, Clarke & Büttcher (2009): Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — original RRF paper, k=60
