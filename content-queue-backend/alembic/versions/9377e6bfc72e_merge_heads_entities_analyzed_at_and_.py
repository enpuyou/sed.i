"""merge_heads_entities_analyzed_at_and_search_vector

Revision ID: 9377e6bfc72e
Revises: b5c2f1d8e4a6, e3f7a2c9b1d4
Create Date: 2026-07-07 23:23:13.880055

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "9377e6bfc72e"
down_revision: Union[str, Sequence[str], None] = ("b5c2f1d8e4a6", "e3f7a2c9b1d4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
