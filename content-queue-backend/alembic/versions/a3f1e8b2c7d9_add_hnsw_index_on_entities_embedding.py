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

Parameters: m=16, ef_construction=64 (pgvector defaults for 1536-dim vectors).

The index is built by scripts/post_deploy.py using CREATE INDEX CONCURRENTLY,
which runs after alembic upgrade and outside a transaction. This avoids the
table lock that a transactional CREATE INDEX would impose. The migration only
records the downgrade path so alembic can drop the index if rolled back.
"""

from alembic import op

revision = "a3f1e8b2c7d9"
down_revision = "014958e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index is built by scripts/post_deploy.py (CONCURRENTLY, outside transaction).
    pass


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS entities_embedding_hnsw")
