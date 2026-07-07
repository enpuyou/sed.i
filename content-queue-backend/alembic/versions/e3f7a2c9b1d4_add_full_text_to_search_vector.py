"""add_full_text_to_search_vector

Extends the search_vector trigger on content_items to include the full_text
column at weight 'C' (lower priority than title/author 'A' and description/tags 'B').

This enables keyword search to match terms that appear only in the article body,
not just in metadata fields.

Revision ID: e3f7a2c9b1d4
Revises: b683b1898f35, 011_add_reading_clusters, 012_add_refresh_tokens
Create Date: 2026-07-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e3f7a2c9b1d4"
down_revision: Union[str, Sequence[str], None] = (
    "b683b1898f35",
    "011_add_reading_clusters",
    "012_add_refresh_tokens",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Replace the trigger function to include full_text at weight C.
    # The trigger is already attached (BEFORE INSERT OR UPDATE); we just need
    # to update the function body and expand the column list in the trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION content_items_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.author, '')), 'A') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.author, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.description, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(array_to_string(NEW.tags, ' '), '')), 'B') ||
                setweight(to_tsvector('simple',  COALESCE(array_to_string(NEW.tags, ' '), '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(NEW.full_text, '')), 'C') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.full_text, '')), 'C');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Recreate the trigger to fire on full_text changes too.
    op.execute("DROP TRIGGER IF EXISTS tsvector_update ON content_items;")
    op.execute(
        """
        CREATE TRIGGER tsvector_update
        BEFORE INSERT OR UPDATE OF title, author, description, tags, full_text
        ON content_items
        FOR EACH ROW EXECUTE FUNCTION content_items_search_vector_update();
        """
    )

    # Backfill all existing rows — this may be slow on large tables but runs once.
    op.execute(
        """
        UPDATE content_items SET search_vector =
            setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
            setweight(to_tsvector('simple',  COALESCE(title, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(author, '')), 'A') ||
            setweight(to_tsvector('simple',  COALESCE(author, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
            setweight(to_tsvector('simple',  COALESCE(description, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(array_to_string(tags, ' '), '')), 'B') ||
            setweight(to_tsvector('simple',  COALESCE(array_to_string(tags, ' '), '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(full_text, '')), 'C') ||
            setweight(to_tsvector('simple',  COALESCE(full_text, '')), 'C');
        """
    )


def downgrade() -> None:
    # Revert to metadata-only trigger (title, author, description, tags).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION content_items_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.author, '')), 'A') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.author, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
                setweight(to_tsvector('simple',  COALESCE(NEW.description, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(array_to_string(NEW.tags, ' '), '')), 'B') ||
                setweight(to_tsvector('simple',  COALESCE(array_to_string(NEW.tags, ' '), '')), 'B');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute("DROP TRIGGER IF EXISTS tsvector_update ON content_items;")
    op.execute(
        """
        CREATE TRIGGER tsvector_update
        BEFORE INSERT OR UPDATE OF title, author, description, tags
        ON content_items
        FOR EACH ROW EXECUTE FUNCTION content_items_search_vector_update();
        """
    )
