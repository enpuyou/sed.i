"""
MCP tools: list_lists, get_list_content.

These are pure functions (user, db) → dict/list — no HTTP, no FastAPI deps.
The MCP server calls them after resolving the user from the token.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User
from app.models.list import List, content_list_membership
from app.models.content import ContentItem

# At ~4 chars per token; 8000 tokens ≈ 32000 chars. Keep generous.
_FULL_TEXT_CHAR_LIMIT = 32_000


def list_lists(*, user: User, db: Session) -> list[dict]:
    """
    Return all reading lists owned by the user with article counts.

    Deleted articles are excluded from the count.
    """
    rows = (
        db.query(
            List,
            func.count(ContentItem.id).label("item_count"),
        )
        .outerjoin(
            content_list_membership,
            List.id == content_list_membership.c.list_id,
        )
        .outerjoin(
            ContentItem,
            (ContentItem.id == content_list_membership.c.content_item_id)
            & (ContentItem.deleted_at.is_(None)),
        )
        .filter(List.owner_id == user.id)
        .group_by(List.id)
        .all()
    )

    return [
        {
            "id": str(lst.id),
            "name": lst.name,
            "description": lst.description,
            "item_count": count,
            "created_at": lst.created_at.isoformat() if lst.created_at else None,
            "updated_at": lst.updated_at.isoformat() if lst.updated_at else None,
        }
        for lst, count in rows
    ]


def get_list_content(
    *,
    list_id: str,
    user: User,
    db: Session,
    include_full_text: bool = False,
    limit: int = 50,
) -> list[dict]:
    """
    Return articles in a list.

    Args:
        list_id: UUID of the list.
        include_full_text: Include HTML body (default False). Truncated at ~8k tokens.
        limit: Max articles (default 50, max 200).

    Raises:
        ValueError: If the list doesn't exist or doesn't belong to the user.
    """
    limit = min(limit, 200)

    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    items = (
        db.query(ContentItem)
        .join(
            content_list_membership,
            ContentItem.id == content_list_membership.c.content_item_id,
        )
        .filter(
            content_list_membership.c.list_id == list_id,
            ContentItem.deleted_at.is_(None),
        )
        .order_by(ContentItem.created_at.desc())
        .limit(limit)
        .all()
    )

    return [_format_item(item, include_full_text=include_full_text) for item in items]


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
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }

    if include_full_text:
        text = item.full_text or ""
        if len(text) > _FULL_TEXT_CHAR_LIMIT:
            text = text[:_FULL_TEXT_CHAR_LIMIT] + " [truncated]"
        result["full_text"] = text

    return result
