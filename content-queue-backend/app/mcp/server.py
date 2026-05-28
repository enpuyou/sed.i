"""
sed.i MCP Server — Phase 1 (local stdio).

Launch via:
    poetry run python -m app.mcp.server

Claude Desktop config (~/.../Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "sedi": {
          "command": "poetry",
          "args": ["run", "python", "-m", "app.mcp.server"],
          "cwd": "/absolute/path/to/content-queue-backend",
          "env": { "SEDI_TOKEN": "<your-sedi-jwt>" }
        }
      }
    }

Get your JWT from browser devtools → Application → localStorage → "token".
"""

import sys
import logging

from mcp.server.fastmcp import FastMCP

from app.mcp.auth import get_user_from_env
from app.mcp.db import get_db
from app.mcp.tools.lists import (
    list_lists as _list_lists,
    get_list_content as _get_list_content,
)
from app.mcp.tools.content import (
    get_content_item as _get_content_item,
    search_content as _search_content,
    find_similar as _find_similar,
)
from app.mcp.tools.highlights import get_highlights as _get_highlights
from app.mcp.tools.drafts import get_draft as _get_draft
from app.mcp.tools.stats import get_reading_stats as _get_reading_stats
from app.mcp.tools.summarize import summarize_list as _summarize_list
from app.mcp.tools.write import (
    update_draft as _update_draft,
    add_content as _add_content,
    create_list as _create_list,
    add_to_list as _add_to_list,
)
from app.mcp.tools.query import query_library as _query_library

# MCP stdio uses stdout for JSON-RPC — all logging must go to stderr.
logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("sedi.mcp")

mcp = FastMCP(
    "sedi",
    instructions=(
        "sed.i is a personal reading and writing assistant. "
        "Use these tools to explore the user's library, reading lists, highlights, and drafts. "
        "Always operate on the authenticated user's data only."
    ),
)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@mcp.tool()
def list_lists() -> list[dict]:
    """
    List all reading lists owned by the user with article counts.

    Returns a list of objects with fields:
      id, name, description, item_count, created_at, updated_at
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _list_lists(user=user, db=db)


@mcp.tool()
def get_list_content(
    list_id: str,
    include_full_text: bool = False,
    limit: int = 50,
) -> list[dict]:
    """
    Get articles in a reading list.

    Args:
        list_id: UUID of the list (get it from list_lists).
        include_full_text: Include the full HTML body of each article.
                           Defaults to False. Truncated at ~8k tokens.
        limit: Maximum number of articles to return (default 50, max 200).

    Returns a list of article objects with title, url, summary, tags, etc.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _get_list_content(
            list_id=list_id,
            user=user,
            db=db,
            include_full_text=include_full_text,
            limit=limit,
        )


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


@mcp.tool()
def get_content_item(item_id: str, include_full_text: bool = False) -> dict:
    """
    Get a single article by ID.

    Args:
        item_id: UUID of the article.
        include_full_text: Include the full HTML body. Defaults to False.

    Returns article metadata (title, url, summary, author, tags, read status, etc.).
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _get_content_item(
            item_id=item_id, user=user, db=db, include_full_text=include_full_text
        )


@mcp.tool()
def search_content(query: str, limit: int = 10) -> list[dict]:
    """
    Semantic search across the user's entire library.

    Uses OpenAI embeddings + vector similarity to find the most relevant articles.

    Args:
        query: Natural-language search query (e.g. "articles about AI agents").
        limit: Max results to return (default 10, max 50).

    Returns a list of {item, similarity_score} objects ordered by relevance.
    Returns an empty list if no articles have embeddings yet.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _search_content(query=query, user=user, db=db, limit=limit)


@mcp.tool()
def find_similar(item_id: str, limit: int = 5) -> list[dict]:
    """
    Find articles similar to a given article.

    Args:
        item_id: UUID of the source article.
        limit: Max similar articles to return (default 5).

    Returns a list of {item, similarity_score} objects.
    Returns an empty list if the source article has no embedding yet.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _find_similar(item_id=item_id, user=user, db=db, limit=limit)


# ---------------------------------------------------------------------------
# Highlights
# ---------------------------------------------------------------------------


@mcp.tool()
def get_highlights(
    item_id: str | None = None,
    list_id: str | None = None,
) -> list[dict]:
    """
    Get highlights. Three modes:

    - item_id provided: highlights from one article only.
    - list_id provided: highlights from all articles in a list.
    - neither provided: all user highlights (max 100, most recent first).

    Returns a list of {id, text, note, color, article_id, article_title} objects.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _get_highlights(item_id=item_id, list_id=list_id, user=user, db=db)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


@mcp.tool()
def get_draft(list_id: str) -> dict | None:
    """
    Get the writing draft for a reading list.

    Args:
        list_id: UUID of the list.

    Returns {title, content (markdown), word_count, updated_at},
    or null if no draft has been started for this list yet.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _get_draft(list_id=list_id, user=user, db=db)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@mcp.tool()
def get_reading_stats() -> dict:
    """
    Get reading statistics for the user.

    Returns {total_items, read_count, unread_count, archived_count}.
    Deleted articles are excluded from all counts.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _get_reading_stats(user=user, db=db)


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------


@mcp.tool()
def summarize_list(
    list_id: str,
    style: str = "overview",
    max_items: int = 20,
) -> dict:
    """
    Summarize a reading list using AI.

    Args:
        list_id: UUID of the list (get it from list_lists).
        style: Summary style — one of:
               'overview'  General summary of topics covered.
               'themes'    Key recurring themes across articles.
               'gaps'      What's missing or underrepresented (considers your draft).
               'timeline'  How topics have evolved over time.
        max_items: Max articles to include in the prompt (default 20).

    Returns {summary, style, item_count, cached}.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _summarize_list(
            list_id=list_id, user=user, db=db, style=style, max_items=max_items
        )


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool()
def update_draft(
    list_id: str,
    content: str,
    title: str | None = None,
) -> dict:
    """
    Create or update the writing draft for a reading list.

    Args:
        list_id: UUID of the list.
        content: Markdown content for the draft.
        title: Optional title override.

    Returns {title, content, word_count, updated_at}.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _update_draft(
            list_id=list_id, content=content, title=title, user=user, db=db
        )


@mcp.tool()
def add_content(url: str) -> dict:
    """
    Save a URL to the user's sed.i library and queue extraction.

    If the URL already exists, returns the existing item.

    Args:
        url: Full URL of the article to save (e.g. 'https://example.com/post').

    Returns {item_id, status} where status is 'queued' or 'exists'.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _add_content(url=url, user=user, db=db)


@mcp.tool()
def create_list(name: str, description: str | None = None) -> dict:
    """
    Create a new reading list.

    Args:
        name: Name of the list.
        description: Optional description.

    Returns {id, name, description, created_at}.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _create_list(name=name, description=description, user=user, db=db)


@mcp.tool()
def add_to_list(list_id: str, item_id: str) -> dict:
    """
    Add an existing content item to a reading list.

    Args:
        list_id: UUID of the list (from list_lists or create_list).
        item_id: UUID of the content item (from search_content or add_content).

    Returns {status, item_id, list_id} where status is 'added' or 'already_in_list'.
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _add_to_list(list_id=list_id, item_id=item_id, user=user, db=db)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@mcp.tool()
def query_library(question: str) -> dict:
    """
    Answer a natural-language question about your reading library using SQL.

    Translates your question into a read-only SQL query, runs it against your
    library database, and returns a plain-English answer along with the SQL
    that was generated (for transparency).

    Args:
        question: A plain-English question about your library.
                  Examples:
                    "What have I read this week?"
                    "Which articles are tagged 'machine learning'?"
                    "How many articles do I have about climate change?"
                    "What are my longest unread articles?"
                    "Show me everything I saved but never read."

    Returns:
        {
            "answer": str,     # Natural-language summary of results
            "sql": str,        # SQL that was generated (for transparency)
            "row_count": int,  # Number of rows returned
        }
    """
    with get_db() as db:
        user = get_user_from_env(db)
        return _query_library(question=question, user=user, db=db)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting sed.i MCP server (stdio transport)")
    mcp.run()
