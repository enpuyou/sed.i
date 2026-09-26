"""add_research_memory

Revision ID: c1d2e3f4a5b6
Revises: f5fa4cf4c67f
Create Date: 2026-07-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "f5fa4cf4c67f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sub_question", sa.Text(), nullable=False),
        sa.Column("topic_embedding", Vector(1536), nullable=True),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("topic_summary", sa.Text(), nullable=True),
        sa.Column("gap_description", sa.Text(), nullable=True),
        sa.Column(
            "source_item_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_memory_user_id_created_at",
        "research_memory",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_research_memory_run_id",
        "research_memory",
        ["run_id"],
    )
    # IVFFlat index for cosine similarity search — requires pgvector extension.
    # lists=100 is appropriate for < 1M rows.
    op.execute(
        """
        CREATE INDEX ix_research_memory_embedding
        ON research_memory
        USING ivfflat (topic_embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_research_memory_embedding", table_name="research_memory")
    op.drop_index("ix_research_memory_run_id", table_name="research_memory")
    op.drop_index("ix_research_memory_user_id_created_at", table_name="research_memory")
    op.drop_table("research_memory")
