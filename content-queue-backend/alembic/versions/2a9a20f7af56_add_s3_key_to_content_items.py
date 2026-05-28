"""add_s3_key_to_content_items

Revision ID: 2a9a20f7af56
Revises: 013_merge_heads
Create Date: 2026-05-22 10:46:02.467723

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2a9a20f7af56"
down_revision: Union[str, Sequence[str], None] = "013_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("content_items", sa.Column("s3_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_items", "s3_key")
