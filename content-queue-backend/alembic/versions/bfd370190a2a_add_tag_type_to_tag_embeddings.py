"""add_tag_type_to_tag_embeddings

Revision ID: bfd370190a2a
Revises: 0249ceae48ac
Create Date: 2026-07-01 15:54:25.976700

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "bfd370190a2a"
down_revision: Union[str, Sequence[str], None] = "0249ceae48ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tag_embeddings", sa.Column("tag_type", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tag_embeddings", "tag_type")
