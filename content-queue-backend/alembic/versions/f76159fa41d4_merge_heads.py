"""merge_heads

Revision ID: f76159fa41d4
Revises: 009_content_chunks, 5534fbac2811
Create Date: 2026-05-12 20:44:34.273458

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "f76159fa41d4"
down_revision: Union[str, Sequence[str], None] = ("009_content_chunks", "5534fbac2811")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
