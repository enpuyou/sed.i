"""
MCP tool: get_highlights.

Three modes depending on which arguments are provided:
  - item_id set  → highlights from one article
  - list_id set  → highlights from all articles in a list
  - neither      → all user highlights (capped at 100)
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.list import List, content_list_membership


def get_highlights(
    *,
    user: User,
    db: Session,
    item_id: str | None = None,
    list_id: str | None = None,
) -> list[dict]:
    """
    Return highlights. Modes:
      - item_id provided → highlights from that article only
      - list_id provided → highlights from all articles in the list
      - neither          → all user highlights (max 100)

    Raises:
        ValueError: If the specified item or list doesn't exist or belongs to
                    another user.
    """
    if item_id is not None:
        return _highlights_for_item(item_id=item_id, user=user, db=db)
    if list_id is not None:
        return _highlights_for_list(list_id=list_id, user=user, db=db)
    return _all_highlights(user=user, db=db)


def _highlights_for_item(*, item_id: str, user: User, db: Session) -> list[dict]:
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

    highlights = (
        db.query(Highlight)
        .filter(
            Highlight.content_item_id == item_id,
            Highlight.user_id == user.id,
        )
        .order_by(Highlight.created_at.desc())
        .all()
    )

    return [
        _format(h, article_title=item.title, article_id=str(item.id))
        for h in highlights
    ]


def _highlights_for_list(*, list_id: str, user: User, db: Session) -> list[dict]:
    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    # Join highlights → content_items → list membership
    rows = (
        db.query(Highlight, ContentItem)
        .join(ContentItem, Highlight.content_item_id == ContentItem.id)
        .join(
            content_list_membership,
            ContentItem.id == content_list_membership.c.content_item_id,
        )
        .filter(
            content_list_membership.c.list_id == list_id,
            Highlight.user_id == user.id,
            ContentItem.deleted_at.is_(None),
        )
        .order_by(Highlight.created_at.desc())
        .all()
    )

    return [
        _format(h, article_title=item.title, article_id=str(item.id))
        for h, item in rows
    ]


def _all_highlights(*, user: User, db: Session) -> list[dict]:
    rows = (
        db.query(Highlight, ContentItem)
        .join(ContentItem, Highlight.content_item_id == ContentItem.id)
        .filter(
            Highlight.user_id == user.id,
            ContentItem.deleted_at.is_(None),
        )
        .order_by(Highlight.created_at.desc())
        .limit(100)
        .all()
    )

    return [
        _format(h, article_title=item.title, article_id=str(item.id))
        for h, item in rows
    ]


def _format(h: Highlight, *, article_title: str, article_id: str) -> dict:
    return {
        "id": str(h.id),
        "text": h.text,
        "note": h.note,
        "color": h.color,
        "article_id": article_id,
        "article_title": article_title,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
