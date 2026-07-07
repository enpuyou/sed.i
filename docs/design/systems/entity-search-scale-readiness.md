# Entity Search — Full-Stack Scale Readiness Audit
Date: 2026-07-06
Target: 100 users × 300–500 articles each = 30,000–50,000 articles total.
Per-user entity count: ~5 entities/article × 400 articles = ~2,000 entities per user.
Cross-user DB totals: ~200,000 entities, ~1M entity_mentions rows.

This is the target scale. "Fine for now" is not an answer here.

---

## Correction on point 1 — extraction prompt

The `extract_entities_task` in `entity_extraction.py` *does* list types with
names like PERSON/CONCEPT/ORGANIZATION — that's the older standalone task. But
the live ingestion pipeline now runs `analyze_article_task` from
`article_analysis.py`, which has a substantially different prompt. Key
differences in `_ANALYSIS_PROMPT`:

- Priority order explicitly puts CONCEPT entities first: "named ideas,
  frameworks, phenomena, or cognitive patterns that the article analyzes"
- Gives concrete good/bad examples: `"availability heuristic"`, `"context
  anxiety"`, `"reverse centaur"` are good. `"AI"`, `"technology"` are bad.
- Concept tags are also promoted to entity nodes (lines 274–293 in
  `article_analysis.py`) — every concept tag becomes a CONCEPT entity even if
  the extraction missed it.

**The extraction prompt is stronger than assumed.** The vocabulary-mismatch
failure in the hub-cap investigation is not a prompt quality problem — it's a
coverage problem for specific articles (e.g. `trustworthy_agents`, which is
about implementation, not the autonomy/oversight debate). The prompt wouldn't
extract `human-in-the-loop` from an article that doesn't discuss it in those
terms regardless of how good the prompt is.

The standalone `extract_entities_task` is now a dead code path — the pipeline
uses `analyze_article_task`. No prompt fix needed; the extraction prompt is
already concept-first.

---

## Scale analysis — 100 users × 400 articles each

### A. Entity candidate SQL (the LIMIT 8 query)

**Current**: sequential cosine scan over `entities` for one user, returning top
8. At 2,000 entities per user, this is ~2,000 vector comparisons per query.
With `text-embedding-3-small` 1536-dim vectors, that's ~12MB of data read per
query just for the embedding column. Sequential scan.

**At target scale (2,000 entities/user)**: ~5–15ms per query. Acceptable.

**With Phase 2 (remove LIMIT 8, threshold-only)**: Same 2,000 row scan, now
returning all rows above 0.40 sim instead of just 8. Typically 5–30 qualifying
rows. Slightly more data returned but the scan cost is unchanged — the scan
reads all 2,000 rows either way. No performance regression.

**The missing piece**: there is no index on `entities.embedding`. pgvector's
cosine scan is sequential by default. This is fine at 2,000 entities/user. It
becomes a problem when the total entities table grows to hundreds of thousands
of rows and Postgres must distinguish per-user rows via the `WHERE user_id = :uid`
filter — the planner may scan more than just the target user's rows depending on
index selectivity.

**Fix**: add a partial HNSW index scoped per user — or a composite HNSW index
on `(user_id, embedding)`. Standard pgvector syntax:

```sql
CREATE INDEX entities_embedding_hnsw ON entities
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

This reduces scan from O(N_total) to O(log N_user). Required before going
multi-user. One migration.

**Verdict: needs the HNSW migration before multi-user.**

---

### B. Entity dedup — the O(N²) self-join

This is the biggest structural problem at target scale.

Current SQL in `entity_dedup.py` (line 94–113):

```sql
SELECT a.id, a.name, b.id, b.name, 1 - (a.embedding <=> b.embedding) AS sim
FROM entities a
JOIN entities b ON a.id < b.id
WHERE a.user_id = :uid AND b.user_id = :uid
  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
  AND 1 - (a.embedding <=> b.embedding) >= :thresh
```

This is a **cross-join of a user's entities with themselves** — O(N²) pairs. At
2,000 entities per user: 2,000 × 1,999 / 2 = **~2M pair comparisons** per dedup
run. At 5,000 entities: ~12.5M pairs.

The docstring says: "Runtime: ~15s at 1,500 entities, ~30min at 120,000."
At 2,000 entities — right at the edge of 15s. The beat schedule runs this task
periodically. If a user's dedup run takes 20–30s and overlaps with other users,
the Celery worker queue backs up.

**This does not block search correctness** — dedup is a background maintenance
task, not in the search path. But at 100 users × 2,000 entities each, running
dedup for all users in a beat window would require 100 × 15s = **25 minutes of
compute** per cycle. With `--concurrency=2`, the cycle takes ~12 minutes. If the
beat fires every 30 minutes (typical), this is marginal.

**Fix options**:

1. Run dedup per-user with jitter/staggered scheduling — don't dedup all 100
   users in one beat tick.
2. Replace the cross-join with pgvector's approximate nearest-neighbor:
   query each entity's top-K neighbors by HNSW index instead of exhaustive pairs.
   At 2,000 entities with HNSW, finding all pairs above 0.82 sim costs
   O(N × log N) instead of O(N²). Requires the same HNSW index as §A.
3. Batch the cross-join: split users into groups of 10, process groups on
   separate beat ticks.

**Verdict: needs HNSW-based approach or staggered scheduling for dedup at
100-user scale. Blocking for correctness at ~5,000 entities/user.**

---

### C. Mention fetch query (in `_entity_search`)

```sql
SELECT em.content_item_id, em.entity_id,
       COUNT(*) OVER (PARTITION BY em.entity_id) AS entity_article_count
FROM entity_mentions em
WHERE em.entity_id = ANY(:eids) AND em.user_id = :uid
```

With anchors + neighbors: typically 8–20 entity IDs in `:eids`. Each entity
mentions ~5 articles on average. So 20 entities × 5 articles = ~100 mention rows
fetched. `entity_mentions.entity_id` is indexed. This query is fast regardless
of total table size — it uses the index to jump directly to the matching rows.

**At target scale (1M total mention rows)**: still fast because the index lookup
bounds the scan to exactly the rows for the given entity IDs. This is O(|eids| ×
avg_mentions_per_entity), not O(total_mentions).

**Verdict: no problem.**

---

### D. Hydration — the N+1 risk

`hydrate_items()` does a single `ContentItem.id.in_(row_ids)` query — bulk
load, not N+1. At target scale, `row_ids` is at most `limit` items (10–30).
Single query, fast.

**Verdict: no problem.**

---

### E. RRF fusion (in-memory)

The fusion loop unions all four lane results in Python dicts. At 100-user scale,
each lane returns at most `fetch_limit` rows (= `(offset + limit) * 3` = 30 at
page 1). Four lanes × 30 = 120 items in the union. O(120) dict operations.

**Verdict: no problem at any realistic scale.**

---

### F. Query embedding cache

Redis-cached per query text, TTL 1h. At 100 concurrent users all searching
simultaneously, Redis gets ~100 cache lookups/second. Redis handles 100K
ops/second. No issue.

The entity lane currently calls `call_embed()` directly (separate from the
shared `get_or_create_query_embedding()` call earlier in `hybrid_search`). This
means a search in `mode="full"` currently embeds the query **twice** — once in
the semantic lane and once in the entity lane. Each is Redis-cached after the
first call, but the first call on a new query is one extra OpenAI API call.

**Fix**: pass the already-computed embedding into `_entity_search` instead of
re-embedding. The semantic lane already calls `get_or_create_query_embedding()`,
so the vector is available in `hybrid_search`'s scope.

**Verdict: double-embedding is a minor cost inefficiency (~$0 at low volume,
~$2/day at 100 users × 100 searches/day). Fix is trivial — pass the embedding
as a parameter. Not blocking but worth fixing in Phase 2.**

---

### G. `article_count` staleness

The entity candidate SQL computes `COUNT(em.content_item_id)` live via a JOIN —
correct, not using the stale `entities.article_count` column. This adds a GROUP
BY to the query but is accurate at any scale.

**Verdict: no problem.**

---

### H. Celery concurrency model

`--concurrency=2 --pool=solo` means 2 tasks run at a time. At 100 users
simultaneously ingesting articles, the task queue will grow. Each article
analysis runs `analyze_article_task` (LLM call, ~3–5s) then
`embed_new_entities_task` (embed API call, ~1–2s). Two concurrent workers can
process ~20 articles/minute. At 100 users ingesting 5 articles/day each = 500
articles/day = ~35 articles/hour = 0.6/minute. Two workers are more than enough
for steady-state ingestion.

Burst (e.g., one user bulk-imports 300 articles): the queue grows to ~300 tasks.
At 20/minute, this takes 15 minutes to drain. Acceptable for a read-it-later app.

**Verdict: no problem for the described scale.**

---

## Summary — honest verdict

| Layer | Target scale verdict | Action required |
|-------|---------------------|-----------------|
| Entity candidate SQL (LIMIT 8 → threshold) | Fine at 2K entities/user | Phase 2 of redesign |
| **HNSW index on `entities.embedding`** | **Missing — needed before multi-user** | **1 migration** |
| **Entity dedup O(N²) cross-join** | **Marginal at 2K entities, breaks at 5K** | **HNSW-based or staggered scheduling** |
| Mention fetch query | Fine — index-bounded | Nothing |
| Hydration (hydrate_items) | Fine — bulk load | Nothing |
| RRF fusion | Fine — in-memory, O(120) | Nothing |
| **Double query embedding** | **Minor cost waste** | **Pass embedding into _entity_search** |
| Celery worker concurrency | Fine for steady-state | Nothing |
| Extraction prompt | Fine — concept-first already | Nothing (earlier analysis was wrong) |

## Blocking before multi-user ship

1. **HNSW index migration** — without it, the entity candidate scan degrades as
   total entities grows across all users. One migration, low risk.

2. **Dedup strategy** — O(N²) cross-join is the most structurally fragile piece.
   Needs either HNSW-based neighbor lookup (replaces the self-join) or per-user
   staggered scheduling to avoid worker saturation.

## Non-blocking but fix in the redesign

1. **Double embedding** — pass `query_embedding` from `hybrid_search` into
   `_entity_search` as a parameter. Eliminates the redundant embed call.
   Natural fit in Phase 2 since Phase 2 restructures the candidate SQL anyway.
