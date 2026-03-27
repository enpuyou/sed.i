"""
MCP tool: get_reading_stats.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.content import ContentItem


def get_reading_stats(*, user: User, db: Session) -> dict:
    """
    Return reading statistics for the user.

    Deleted items are excluded from all counts.
    """
    base = db.query(ContentItem).filter(
        ContentItem.user_id == user.id,
        ContentItem.deleted_at.is_(None),
    )

    total = base.count()
    read_count = base.filter(ContentItem.is_read == True).count()  # noqa: E712
    archived_count = base.filter(ContentItem.is_archived == True).count()  # noqa: E712

    return {
        "total_items": total,
        "read_count": read_count,
        "unread_count": total - read_count,
        "archived_count": archived_count,
    }
