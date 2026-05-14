"""add reading_clusters table

Revision ID: 011_add_reading_clusters
Revises: 010_add_tag_embeddings
Create Date: 2026-05-13
"""

from typing import Sequence, Union
from alembic import op


revision: str = "011_add_reading_clusters"
down_revision: Union[str, None] = "010_add_tag_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reading_clusters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            article_ids UUID[] NOT NULL DEFAULT '{}',
            tag_labels TEXT[] NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """
    )
    op.execute("CREATE INDEX ON reading_clusters(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reading_clusters")
