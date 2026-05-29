---
type: decision
status: active
last_updated: 2026-05-22
consumer: both
---

# ADR-0001: Vector Storage — pgvector vs. Managed Vector DB

**Status:** Accepted
**Date:** 2026-05-22

---

## Context

sed.i embeds every article and highlight with OpenAI `text-embedding-3-small` (1536 dimensions) and performs semantic similarity search at query time. The system also does hybrid retrieval (vector + keyword) and tag-level similarity queries. The question is where to store and index these vectors.

Options evaluated:
- **pgvector** (extension on existing Postgres) — current choice
- **Qdrant** (self-hosted or Qdrant Cloud)
- **Pinecone** (managed, serverless)
- **Weaviate** (managed, open-source core)
- **Chroma** (local, Python-native)

---

## Decision

**Stay on pgvector with HNSW indexing.**

---

## Rationale

**Scale is not a problem yet.** At single-user to small-team scale, the vector corpus is well under 1M vectors. pgvector with HNSW achieves sub-50ms p95 at this scale with a proper index. A managed vector DB solves a scaling problem we don't have.

**Operational simplicity.** Vectors live in the same Postgres instance as content items, highlights, and chunks. No dual-write logic, no eventual consistency window between the relational store and the vector store, no extra service to monitor or pay for.

**Hybrid search.** sed.i uses RRF fusion of vector similarity and Postgres `tsvector` full-text search. This fusion is trivial in a single SQL query. With a separate vector DB it requires a parallel query, result merge in application code, and careful handling of IDs across two stores — significantly more complexity for no quality gain at this scale.

**Cost.** pgvector is $0 incremental on the existing Postgres instance. Managed vector DBs start at $70+/month for always-on clusters (Pinecone, Weaviate Serverless).

**Qdrant self-hosted** was a close second — it has better HNSW performance at very large scale and good filtering. Rejected because it adds an extra service to the docker-compose stack and Railway deployment without a measurable quality or latency benefit at current scale.

---

## Tradeoffs accepted

- **HNSW build time** is O(n log n) and adds ~1s per 10K vectors on re-index. Acceptable — we rebuild indexes offline, not on the query path.
- **No native multi-tenancy filtering** — pgvector requires `WHERE user_id = ?` on every query, which works fine but is less elegant than Qdrant's native payload filtering.
- **Memory pressure** — HNSW indexes load into shared_buffers. At 1M+ vectors with m=16 this becomes significant. Currently well below that threshold.

---

## Migration trigger

Migrate to a dedicated vector store (likely Qdrant self-hosted) when **any one** of these conditions is true:

1. Vector corpus exceeds **5M vectors** (pgvector HNSW recall degrades above this without careful parameter tuning)
2. p95 semantic search latency exceeds **200ms** under normal load
3. A feature requires **multi-vector-per-document** indexing at scale (e.g., per-chunk search across 100K+ articles per user)
4. We need **real-time vector updates** at high write throughput (>100 writes/sec)

---

## References

- [pgvector HNSW benchmarks](https://github.com/pgvector/pgvector#hnsw)
- sed.i hybrid search implementation: `app/core/hybrid_search.py`
