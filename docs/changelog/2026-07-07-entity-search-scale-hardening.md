# 2026-07-07 — Entity search scale hardening

What shipped: Entity search lane redesigned for scale — IDF dampening replaces hub-cap binary gate, SQL threshold filter replaces hardcoded LIMIT 8, neighbor sims computed via real cosine queries, query embedding shared between search lanes to eliminate double embedding, dedup replaced O(N²) self-join with HNSW ANN (O(N×K×log N)). Dead code (`entity_extraction.py`, `extract_entities_task`) deleted.
API changes: none
Deploy order: backend first (requires HNSW migration `a3f1e8b2c7d9` to run via `alembic upgrade head`)
