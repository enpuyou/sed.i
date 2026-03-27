"""
MCP tools: get_content_item, search_content, find_similar.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.user import User
from app.models.content import ContentItem

_FULL_TEXT_CHAR_LIMIT = 32_000


def get_content_item(
    *,
    item_id: str,
    user: User,
    db: Session,
    include_full_text: bool = False,
) -> dict:
    """
    Return a single article's metadata.

    Args:
        item_id: UUID of the content item.
        include_full_text: Include HTML body (default False). Truncated at ~8k tokens.

    Raises:
        ValueError: If the item doesn't exist, is deleted, or belongs to another user.
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )
    if not item:
        raise ValueError(f"Content item '{item_id}' not found")

    return _format_item(item, include_full_text=include_full_text)


def search_content(
    *,
    query: str,
    user: User,
    db: Session,
    limit: int = 10,
) -> list[dict]:
    """
    Semantic search across the user's entire library using OpenAI embeddings + pgvector.

    Args:
        query: Natural-language search query.
        limit: Max results (default 10, capped at 50).

    Returns:
        List of {item, similarity_score} dicts, ordered by relevance.
        Returns [] if no embeddings exist or OpenAI is not configured.
    """
    limit = min(limit, 50)

    # Check whether any embeddings exist before calling OpenAI
    has_any = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == user.id,
            ContentItem.embedding.isnot(None),
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )
    if not has_any:
        return []

    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=query,
        encoding_format="float",
    )
    query_embedding = response.data[0].embedding
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

    rows = db.execute(
        text(
            """
            SELECT id, (1 - (embedding <=> CAST(:q AS vector))) AS similarity
            FROM content_items
            WHERE user_id = :uid
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :lim
        """
        ),
        {"q": embedding_str, "uid": user.id, "lim": limit},
    ).fetchall()

    results = []
    for row in rows:
        item = db.query(ContentItem).filter(ContentItem.id == row.id).first()
        if item:
            results.append(
                {
                    "item": _format_item(item, include_full_text=False),
                    "similarity_score": float(row.similarity),
                }
            )
    return results


def find_similar(
    *,
    item_id: str,
    user: User,
    db: Session,
    limit: int = 5,
    threshold: float = 0.5,
) -> list[dict]:
    """
    Find articles similar to a given article using pgvector cosine similarity.

    Args:
        item_id: UUID of the source article.
        limit: Max results (default 5).
        threshold: Minimum similarity score (default 0.5).

    Returns:
        List of {item, similarity_score} dicts.
        Returns [] if the source article has no embedding.

    Raises:
        ValueError: If the article doesn't exist or belongs to another user.
    """
    limit = min(limit, 50)

    source = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )
    if not source:
        raise ValueError(f"Content item '{item_id}' not found")

    if source.embedding is None:
        return []

    embedding_str = "[" + ",".join(map(str, source.embedding)) + "]"

    rows = db.execute(
        text(
            """
            SELECT id, (1 - (embedding <=> CAST(:src AS vector))) AS similarity
            FROM content_items
            WHERE user_id = :uid
              AND id != :src_id
              AND deleted_at IS NULL
              AND embedding IS NOT NULL
              AND (1 - (embedding <=> CAST(:src AS vector))) >= :threshold
            ORDER BY embedding <=> CAST(:src AS vector)
            LIMIT :lim
        """
        ),
        {
            "src": embedding_str,
            "uid": user.id,
            "src_id": source.id,
            "threshold": threshold,
            "lim": limit,
        },
    ).fetchall()

    results = []
    for row in rows:
        item = db.query(ContentItem).filter(ContentItem.id == row.id).first()
        if item:
            results.append(
                {
                    "item": _format_item(item, include_full_text=False),
                    "similarity_score": float(row.similarity),
                }
            )
    return results


def _format_item(item: ContentItem, *, include_full_text: bool) -> dict:
    result = {
        "id": str(item.id),
        "title": item.title,
        "url": item.original_url,
        "description": item.description,
        "summary": item.summary,
        "author": item.author,
        "tags": item.tags or [],
        "is_read": item.is_read,
        "is_archived": item.is_archived,
        "word_count": item.word_count,
        "reading_time_minutes": item.reading_time_minutes,
        "content_type": item.content_type,
        "read_position": item.read_position,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }

    if include_full_text:
        text_val = item.full_text or ""
        if len(text_val) > _FULL_TEXT_CHAR_LIMIT:
            text_val = text_val[:_FULL_TEXT_CHAR_LIMIT] + " [truncated]"
        result["full_text"] = text_val

    return result
