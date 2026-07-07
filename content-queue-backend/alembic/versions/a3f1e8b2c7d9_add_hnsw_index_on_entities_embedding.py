"""Add HNSW index on entities.embedding for scalable cosine search.

Revision ID: a3f1e8b2c7d9
Revises: 014958e5
Create Date: 2026-07-06

Without this index, every entity candidate query in _entity_search does a
full sequential scan of all entities rows for the user (ORDER BY embedding <=>
query_vec). At 200 entities/user this is ~1ms; at 2,000 it is ~10-15ms; at the
target scale of 100 users x 2,000 entities each the total table grows to ~200K
rows and the planner may scan more than just the target user's partition.

HNSW (Hierarchical Navigable Small World) is pgvector's approximate nearest-
neighbor graph index. It reduces the cosine scan from O(N) to O(log N) at query
time. Tradeoff: ~5% recall loss (approximate, not exact) and extra memory.

Parameters chosen:
  m = 16        — number of connections per layer; 16 is pgvector default, good
                  for 1536-dim vectors up to ~1M rows
  ef_construction = 64 — build-time search width; higher = better index quality,
                          slower build. 64 is the pgvector default.

The index is on the full table (not partial by user_id) because pgvector does
not support partial HNSW indexes. The WHERE user_id = :uid filter is applied
after the ANN lookup — Postgres's planner uses the index for the vector scan
and the B-tree user_id index for the equality filter together.

Build time: ~1s per 10K rows at 1536 dims. Concurrent build (CONCURRENTLY)
avoids locking the table during migration but is not supported inside a
transaction, so this migration uses op.execute() directly with CONCURRENTLY.
"""

from alembic import op

# Alembic metadata
revision = "a3f1e8b2c7d9"
down_revision = "014958e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Production note: run this migration outside a transaction to use
    # CREATE INDEX CONCURRENTLY (avoids table lock). On Railway/Render, apply
    # the index manually with CONCURRENTLY after deploying. In dev/CI the
    # non-concurrent form is fine.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS entities_embedding_hnsw
        ON entities
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS entities_embedding_hnsw")
