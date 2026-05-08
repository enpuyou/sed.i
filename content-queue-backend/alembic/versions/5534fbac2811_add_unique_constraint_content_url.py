"""add_unique_constraint_content_url

Revision ID: 5534fbac2811
Revises: a1b2c3d4e5f6
Create Date: 2026-05-07 15:33:40.781651

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5534fbac2811"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicate active rows, keeping the most recently created one per
    # (user_id, original_url) pair before adding the unique index.
    op.execute(
        """
        DELETE FROM content_items
        WHERE deleted_at IS NULL
          AND id NOT IN (
            SELECT DISTINCT ON (user_id, original_url) id
            FROM content_items
            WHERE deleted_at IS NULL
            ORDER BY user_id, original_url, created_at DESC
          )
        """
    )

    # Partial unique index: enforces one active URL per user.
    # deleted_at IS NULL means soft-deleted items are excluded, so the same
    # URL can be re-added after deletion.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_content_items_user_url_active
        ON content_items (user_id, original_url)
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_content_items_user_url_active")
