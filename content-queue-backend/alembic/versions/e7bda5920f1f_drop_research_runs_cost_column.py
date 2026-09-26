"""drop_research_runs_cost_column

Revision ID: e7bda5920f1f
Revises: d3e4f5a6b7c8
Create Date: 2026-07-21 00:00:00.000000

Removes ResearchRun.cost — dead schema that nothing ever wrote to (always
returned null via GET /research/{run_id}). Per docs/plans/llm-gateway-hardening.md
Phase 2: cost data lives in Braintrust (via braintrust_span metadata), not
duplicated in Postgres.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e7bda5920f1f"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("research_runs", "cost")


def downgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("cost", JSONB, nullable=True),
    )
