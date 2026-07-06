"""add_entity_graph_tables

Revision ID: 0249ceae48ac
Revises: 2a9a20f7af56
Create Date: 2026-07-01 15:12:02.145273

"""

from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0249ceae48ac"
down_revision: Union[str, Sequence[str], None] = "2a9a20f7af56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "entities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column(
            "embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_entity_user_name"),
    )
    op.create_index(op.f("ix_entities_user_id"), "entities", ["user_id"], unique=False)
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("content_item_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"], ["content_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id", "content_item_id", name="uq_mention_entity_article"
        ),
    )
    op.create_index(
        op.f("ix_entity_mentions_content_item_id"),
        "entity_mentions",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_entity_id"),
        "entity_mentions",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_user_id"), "entity_mentions", ["user_id"], unique=False
    )
    op.create_table(
        "entity_relations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_entity_id", sa.UUID(), nullable=False),
        sa.Column("target_entity_id", sa.UUID(), nullable=False),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("content_item_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"], ["content_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_entity_id"], ["entities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id"], ["entities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_entity_relations_content_item_id"),
        "entity_relations",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_relations_source_entity_id"),
        "entity_relations",
        ["source_entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_relations_target_entity_id"),
        "entity_relations",
        ["target_entity_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_entity_relations_target_entity_id"), table_name="entity_relations"
    )
    op.drop_index(
        op.f("ix_entity_relations_source_entity_id"), table_name="entity_relations"
    )
    op.drop_index(
        op.f("ix_entity_relations_content_item_id"), table_name="entity_relations"
    )
    op.drop_table("entity_relations")
    op.drop_index(op.f("ix_entity_mentions_user_id"), table_name="entity_mentions")
    op.drop_index(op.f("ix_entity_mentions_entity_id"), table_name="entity_mentions")
    op.drop_index(
        op.f("ix_entity_mentions_content_item_id"), table_name="entity_mentions"
    )
    op.drop_table("entity_mentions")
    op.drop_index(op.f("ix_entities_user_id"), table_name="entities")
    op.drop_table("entities")
