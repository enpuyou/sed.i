"""add_research_runs

Revision ID: f5fa4cf4c67f
Revises: e4f5a6b7c8d9
Create Date: 2026-07-10 16:17:07.031879

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f5fa4cf4c67f"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column(
            "sub_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "subagent_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "item_ids_retrieved", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "searches_run", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iteration_count", sa.Integer(), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_runs_user_id"), "research_runs", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_research_runs_user_id"), table_name="research_runs")
    op.drop_table("research_runs")
