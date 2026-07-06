"""add unique constraint to entity_relations

Revision ID: b683b1898f35
Revises: bfd370190a2a
Create Date: 2026-07-02 13:46:27.525263

"""

from typing import Sequence, Union

from alembic import op

revision: str = "b683b1898f35"
down_revision: Union[str, Sequence[str], None] = "bfd370190a2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_entity_relation_source_target_type_article",
        "entity_relations",
        ["source_entity_id", "target_entity_id", "relation_type", "content_item_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_entity_relation_source_target_type_article",
        "entity_relations",
        type_="unique",
    )
