"""add user memory tables

Revision ID: c3d4e5f6a1b2
Revises: f76159fa41d4
Create Date: 2026-07-09 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a1b2"
down_revision: Union[str, Sequence[str], None] = "f76159fa41d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE reading_velocity_enum AS ENUM ('fast', 'deep', 'browsing');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """
    )

    op.create_table(
        "user_memory_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column(
            "content_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_memory_events_user_id",
        "user_memory_events",
        ["user_id"],
    )
    op.create_unique_constraint(
        "uq_memory_event_day",
        "user_memory_events",
        ["user_id", "event_type", "content_item_id"],
    )

    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_focus", sa.Text, nullable=True),
        sa.Column(
            "reading_velocity",
            postgresql.ENUM(
                "fast",
                "deep",
                "browsing",
                name="reading_velocity_enum",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("preferred_depth_words", sa.Integer, nullable=True),
        sa.Column("writing_style_notes", sa.Text, nullable=True),
        sa.Column("active_knowledge_gaps", postgresql.JSONB, nullable=True),
        sa.Column("past_synthesis_topics", postgresql.JSONB, nullable=True),
        sa.Column("last_consolidated", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
    op.drop_table("user_memory_events")
    op.execute("DROP TYPE IF EXISTS reading_velocity_enum")
