# Hypothesis — HNSW Index on entities.embedding

**Date:** 2026-07-07

## Statement

```
Evaluating:    cosine KNN query on entities.embedding table
Variants:      A: sequential scan (no index, current state before migration)
               B: HNSW index (m=16, ef_construction=64) via migration a3f1e8b2c7d9

Hypothesis:    HNSW will deliver ≥2× median query speedup at N≥10K rows, with
               negligible benefit (<10%) below N=5K. At current prod scale
               (~300 entities / ~1 user), the index adds no meaningful speedup.
               The purpose of the migration is to make entity search scale to
               multi-user deployments (100+ users × 2K entities = 200K rows).

Falsified by:  HNSW p50 latency within 10% of sequential scan at N=50K
               (would mean pgvector planner is not using the index, or the
               table is too small for the index to activate)

Success cases: N=10K: HNSW p50 < 0.5× seq scan p50
               N=50K: HNSW p50 < 0.25× seq scan p50

Failure cases: N=1K: should NOT regress (seq scan is faster at small N due
               to index overhead — expected behavior, not a bug)
```

## Metrics

- **Primary:** median query latency (p50) in ms — wall-clock per KNN query, K=10
- **Secondary:** p95 latency — tail behavior matters for interactive search
- **Ratio:** seq_p50 / hnsw_p50 — speedup factor at each N
- **Crossover point:** smallest N where speedup ≥ 2× (the "index activation" point)

Guard rails:
- No regression in query correctness — results from HNSW should overlap with seq
  scan results ≥ 90% (approximate by design; pgvector default recall ≥ 95%)
- Index build must complete in < 60s at N=50K (production SLA)

## Why this matters

Entity search (`_entity_search` in `hybrid_search.py`) runs a cosine KNN against
`entities.embedding` on every `mode="full"` query. At 302 entities it is
negligible. At 200K rows (100 users × 2K entities each), sequential scan would
add ~200ms per query. The HNSW index prevents this from being a blocking issue
before it happens.

## Method

- Synthetic benchmark: numpy random unit vectors (seed=42), 1536-dim, inserted
  into a temp table (`hnsw_bench_tmp`), not the production `entities` table.
- Variants run within the same DB session; each N gets a fresh connection.
- Harness: `evals/hnsw-index/runner.py`
- Pilot: N ∈ {1K, 5K} — fast check, < 30 seconds
- Full:  N ∈ {1K, 5K, 10K, 50K} — complete profile
- 20 query repetitions per N (up from 10 in the draft script) for tighter p50
