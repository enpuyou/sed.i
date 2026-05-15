"""add tag_embeddings table

Revision ID: 010_add_tag_embeddings
Revises: f76159fa41d4
Create Date: 2026-05-13
"""

from typing import Sequence, Union
from alembic import op

revision: str = "010_add_tag_embeddings"
down_revision: Union[str, Sequence[str], None] = "f76159fa41d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE tag_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            label TEXT NOT NULL UNIQUE,
            embedding VECTOR(1536) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """
    )
    op.execute(
        "CREATE INDEX ON tag_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tag_embeddings")
