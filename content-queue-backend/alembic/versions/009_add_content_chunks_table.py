"""add_content_chunks_table

Stores per-article embedding chunks for multi-vector semantic search.
Each article is split into structure-aware chunks (~256-400 tokens each)
and embedded individually. At query time, an article's score is the MAX
cosine similarity across all its chunks.

Revision ID: 009_add_content_chunks_table
Revises: 008_add_search_vector_to_highlights
Create Date: 2026-05-08 00:00:00.000000
"""

from typing import Sequence, Union
from alembic import op

revision: str = "009_content_chunks"
down_revision: Union[str, Sequence[str], None] = "008_highlights_search_vec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_chunks (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content_item_id UUID NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chunk_index     INTEGER NOT NULL,
            text            TEXT NOT NULL,
            embedding       vector(1536),
            created_at      TIMESTAMPTZ DEFAULT now()
        );
    """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_chunks_content_item ON content_chunks(content_item_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_chunks_user ON content_chunks(user_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content_chunks;")
