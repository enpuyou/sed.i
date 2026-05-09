"""add_search_vector_to_highlights

Adds a tsvector column for full-text search over highlight text and notes.
Uses a trigger (same pattern as content_items) because to_tsvector() is
STABLE not IMMUTABLE, so GENERATED ALWAYS AS is not allowed.

Revision ID: 008_add_search_vector_to_highlights
Revises: a1b2c3d4e5f6
Create Date: 2026-05-08 00:00:00.000000
"""

from typing import Sequence, Union
from alembic import op

revision: str = "008_highlights_search_vec"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE highlights ADD COLUMN IF NOT EXISTS search_vector tsvector;"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION highlights_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('english', COALESCE(NEW.text, '')) ||
                to_tsvector('simple',  COALESCE(NEW.text, '')) ||
                to_tsvector('english', COALESCE(NEW.note, '')) ||
                to_tsvector('simple',  COALESCE(NEW.note, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS highlights_tsvector_update ON highlights;
        CREATE TRIGGER highlights_tsvector_update
        BEFORE INSERT OR UPDATE OF text, note
        ON highlights
        FOR EACH ROW EXECUTE FUNCTION highlights_search_vector_update();
        """
    )

    op.execute(
        """
        UPDATE highlights SET search_vector =
            to_tsvector('english', COALESCE(text, '')) ||
            to_tsvector('simple',  COALESCE(text, '')) ||
            to_tsvector('english', COALESCE(note, '')) ||
            to_tsvector('simple',  COALESCE(note, ''));
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_highlights_search_vector
        ON highlights USING gin(search_vector);
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS highlights_tsvector_update ON highlights;")
    op.execute("DROP FUNCTION IF EXISTS highlights_search_vector_update();")
    op.execute("DROP INDEX IF EXISTS idx_highlights_search_vector;")
    op.execute("ALTER TABLE highlights DROP COLUMN IF EXISTS search_vector;")
