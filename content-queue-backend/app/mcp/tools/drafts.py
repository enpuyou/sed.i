"""
MCP tool: get_draft.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.list import List
from app.models.draft import Draft


def get_draft(*, list_id: str, user: User, db: Session) -> dict | None:
    """
    Return the writing draft for a list, or None if no draft exists.

    Raises:
        ValueError: If the list doesn't exist or belongs to another user.
    """
    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == user.id)
        .first()
    )
    if not draft:
        return None

    return {
        "title": draft.title,
        "content": draft.content,
        "word_count": draft.word_count,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }
