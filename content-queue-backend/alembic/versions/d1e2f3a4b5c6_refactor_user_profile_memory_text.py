"""refactor user_profiles: add memory_text, drop obsolete columns

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a1b2
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("memory_text", sa.Text, nullable=True))
    op.drop_column("user_profiles", "preferred_depth_words")
    op.drop_column("user_profiles", "writing_style_notes")
    op.drop_column("user_profiles", "active_knowledge_gaps")
    op.drop_column("user_profiles", "past_synthesis_topics")


def downgrade() -> None:
    from sqlalchemy.dialects import postgresql

    op.drop_column("user_profiles", "memory_text")
    op.add_column(
        "user_profiles", sa.Column("preferred_depth_words", sa.Integer, nullable=True)
    )
    op.add_column(
        "user_profiles", sa.Column("writing_style_notes", sa.Text, nullable=True)
    )
    op.add_column(
        "user_profiles",
        sa.Column("active_knowledge_gaps", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("past_synthesis_topics", postgresql.JSONB, nullable=True),
    )
