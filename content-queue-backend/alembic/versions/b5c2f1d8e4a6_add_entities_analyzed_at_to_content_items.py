"""Add entities_analyzed_at to content_items for backfill tracking.

Revision ID: b5c2f1d8e4a6
Revises: a3f1e8b2c7d9
Create Date: 2026-07-07

Allows the backfill task to distinguish "analyzed, produced zero entities"
from "never analyzed". Without this column, both states look identical
(no entity_mentions rows) and the backfill would re-analyze articles that
legitimately have no extractable concepts, wasting LLM calls.

Set to NOW() for all existing rows on upgrade so the backfill only targets
articles ingested before the entity system existed — those will have NULL.
Articles processed by analyze_article going forward get the timestamp written
by the task itself.
"""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column(
            "entities_analyzed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("content_items", "entities_analyzed_at")
