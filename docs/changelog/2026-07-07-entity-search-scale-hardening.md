# 2026-07-07 — Entity search scale hardening + bug fixes

What shipped: Entity search lane redesigned for scale — IDF dampening replaces hub-cap binary gate, SQL threshold filter replaces hardcoded LIMIT 8, neighbor sims computed via real cosine queries, query embedding shared between search lanes to eliminate double embedding, dedup replaced O(N²) self-join with HNSW ANN (O(N×K×log N)). Dead code (`entity_extraction.py`, `extract_entities_task`) deleted.

Bug fixes (from PR review):

- `article_analysis.py`: concept-tag entity nodes now created even when `skip_tags=True` (backfill path was silently skipping concept-entity promotion)
- `hybrid_search.py`: neighbor sims now filtered to `> 0` before adding to `sim_map` (hub neighbors with near-zero query similarity no longer pollute article scores); removed dead gate condition that was unreachable due to SQL-level threshold filtering
- `entity_dedup.py`: winner `id_a` now added to `already_merged` alongside `id_b` to prevent cascade re-merging in dense graphs
- `entity_graph.py`: stale entity object explicitly expunged after SAVEPOINT rollback on `IntegrityError`
- `scripts/post_deploy.py`: connection cleanup wrapped in `try/finally`

API changes: none
Deploy order: backend first (requires HNSW migration `a3f1e8b2c7d9` to run via `alembic upgrade head`)
