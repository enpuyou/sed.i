"""Add HNSW indexes on content_items/highlights/content_chunks embeddings.

Revision ID: f4c8b6e21a3d
Revises: e7bda5920f1f
Create Date: 2026-07-22

ADR-0001 accepted "pgvector with HNSW indexing" as the vector storage
strategy. In practice, only entities.embedding got the HNSW treatment
(migration a3f1e8b2c7d9) — the three columns actually hit by every
user-facing semantic search (_semantic_search in hybrid_search.py) had
no ANN index at all: plain sequential scan + exact `<=>` cosine distance.

This closes that gap for:
  - content_chunks.embedding — the primary semantic-search lane (chunk-level
    MAX similarity across an article's chunks)
  - content_items.embedding — the fallback lane for pre-chunking-era articles
  - highlights.embedding — highlight semantic search

Same pattern as a3f1e8b2c7d9: the index is built by scripts/post_deploy.py
using CREATE INDEX CONCURRENTLY (outside a transaction, avoids the table
lock a transactional CREATE INDEX would impose). This migration only
records the downgrade path.

Parameters: m=16, ef_construction=64 (pgvector defaults for 1536-dim
vectors, matching the entities index for consistency).
"""

from alembic import op

revision = "f4c8b6e21a3d"
down_revision = "e7bda5920f1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Indexes are built by scripts/post_deploy.py (CONCURRENTLY, outside transaction).
    pass


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS content_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS content_items_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS highlights_embedding_hnsw")
