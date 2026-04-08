"""add_search_vector_to_content_items

Adds a tsvector column for full-text search with weighted fields:
  - title, author   → weight 'A' (higher priority)
  - description, tags → weight 'B'

Uses a trigger to keep the column up-to-date because PostgreSQL's
GENERATED ALWAYS AS columns require IMMUTABLE expressions, but
to_tsvector() is only STABLE. A trigger is the standard production
approach for this use case.

Revision ID: a1b2c3d4e5f6
Revises: 0618711d3113, 00b660c6eedb
Create Date: 2026-04-04 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = ("0618711d3113", "00b660c6eedb")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tsvector column + trigger + GIN index for full-text search."""
    # 1. Add the column (nullable, populated by trigger)
    op.execute(
        "ALTER TABLE content_items ADD COLUMN IF NOT EXISTS search_vector tsvector;"
    )

    # 2. Create trigger function
    # Index both 'english' (stems regular words) and 'simple' (preserves acronyms
    # like LLM, RAG, API as-is). Without 'simple', "LLMs" is not stemmed to "llm"
    # by the English dictionary, so searching "llm" would miss it.
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

    # 3. Attach trigger to INSERT and UPDATE
    op.execute(
        """
        DROP TRIGGER IF EXISTS tsvector_update ON content_items;
        CREATE TRIGGER tsvector_update
        BEFORE INSERT OR UPDATE OF title, author, description, tags
        ON content_items
        FOR EACH ROW EXECUTE FUNCTION content_items_search_vector_update();
    """
    )

    # 4. Backfill existing rows with updated dual-dictionary vector
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
            setweight(to_tsvector('simple',  COALESCE(array_to_string(tags, ' '), '')), 'B');
    """
    )

    # 5. GIN index for fast full-text queries
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_content_items_search_vector
        ON content_items USING gin(search_vector);
    """
    )


def downgrade() -> None:
    """Remove trigger, function, index, and column."""
    op.execute("DROP TRIGGER IF EXISTS tsvector_update ON content_items;")
    op.execute("DROP FUNCTION IF EXISTS content_items_search_vector_update();")
    op.execute("DROP INDEX IF EXISTS idx_content_items_search_vector;")
    op.execute("ALTER TABLE content_items DROP COLUMN IF EXISTS search_vector;")
