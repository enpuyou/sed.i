"""Replace case-sensitive entity unique constraint with functional lower(name) index.

Revision ID: 014958e5
Revises: b683b1898f35
Create Date: 2026-07-07

The original uq_entity_user_name constraint was (user_id, name) — case-sensitive.
upsert_entity() queries by lower(name), so concurrent inserts of "Backprop" and
"backprop" could both succeed, creating duplicate logical entities.

This migration drops the old constraint and replaces it with a unique functional
index on (user_id, lower(name)), matching the application lookup logic.

Additive-safe: all existing rows are already stored in their canonical casing
(first-seen wins). The new index will not conflict with any existing data.
"""

from alembic import op

revision = "014958e5"
down_revision = "b683b1898f35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_entity_user_name", "entities", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_entity_user_lower_name
        ON entities (user_id, lower(name))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_entity_user_lower_name")
    op.create_unique_constraint("uq_entity_user_name", "entities", ["user_id", "name"])
