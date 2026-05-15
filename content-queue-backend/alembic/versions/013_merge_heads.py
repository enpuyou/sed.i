"""merge heads: 011_add_reading_clusters + 012_add_refresh_tokens

Revision ID: 013_merge_heads
Revises: 011_add_reading_clusters, 012_add_refresh_tokens
Create Date: 2026-05-15

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "013_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "011_add_reading_clusters",
    "012_add_refresh_tokens",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
