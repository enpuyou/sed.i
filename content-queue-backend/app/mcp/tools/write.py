"""
MCP write tools: update_draft, add_content, create_list, add_to_list.
"""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.list import List, content_list_membership
from app.models.content import ContentItem
from app.models.draft import Draft
from app.tasks.extraction import extract_metadata as process_url_task


def update_draft(
    *,
    list_id: str,
    content: str,
    title: str | None = None,
    user: User,
    db: Session,
) -> dict:
    """
    Create or update the writing draft for a reading list.

    Args:
        list_id: UUID of the list.
        content: Markdown content for the draft.
        title: Optional title for the draft.

    Returns:
        {title, content, word_count, updated_at}

    Raises:
        ValueError: If list not found or belongs to another user.
    """
    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == user.id)
        .first()
    )

    word_count = len(content.split())

    if draft:
        draft.content = content
        if title is not None:
            draft.title = title
        draft.word_count = word_count
    else:
        draft = Draft(
            list_id=list_id,
            user_id=user.id,
            title=title or "",
            content=content,
            word_count=word_count,
        )
        db.add(draft)

    db.commit()
    db.refresh(draft)

    return {
        "title": draft.title,
        "content": draft.content,
        "word_count": draft.word_count,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def add_content(
    *,
    url: str,
    user: User,
    db: Session,
) -> dict:
    """
    Save a URL to the user's sed.i library and queue extraction.

    If the URL is already in the library, returns the existing item.

    Args:
        url: Full URL of the article to save.

    Returns:
        {item_id, status} where status is 'queued' or 'exists'.

    Raises:
        ValueError: If the URL is invalid.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid url: '{url}'")

    existing = (
        db.query(ContentItem)
        .filter(ContentItem.user_id == user.id, ContentItem.original_url == url)
        .first()
    )
    if existing:
        return {"item_id": str(existing.id), "status": "exists"}

    item = ContentItem(
        user_id=user.id,
        original_url=url,
        submitted_via="mcp",
        processing_status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    process_url_task.delay(str(item.id))

    return {"item_id": str(item.id), "status": "queued"}


def create_list(
    *,
    name: str,
    description: str | None = None,
    user: User,
    db: Session,
) -> dict:
    """
    Create a new reading list.

    Args:
        name: Name of the list (required).
        description: Optional description.

    Returns:
        {id, name, description, created_at}

    Raises:
        ValueError: If name is empty.
    """
    if not name or not name.strip():
        raise ValueError("List name cannot be empty")

    lst = List(
        name=name.strip(),
        description=description or None,
        owner_id=user.id,
    )
    db.add(lst)
    db.commit()
    db.refresh(lst)

    return {
        "id": str(lst.id),
        "name": lst.name,
        "description": lst.description,
        "created_at": lst.created_at.isoformat() if lst.created_at else None,
    }


def add_to_list(
    *,
    list_id: str,
    item_id: str,
    user: User,
    db: Session,
) -> dict:
    """
    Add an existing content item to a reading list.

    Args:
        list_id: UUID of the list.
        item_id: UUID of the content item.

    Returns:
        {status, item_id, list_id} where status is 'added' or 'already_in_list'.

    Raises:
        ValueError: If list or item not found, or list belongs to another user.
    """
    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    item = (
        db.query(ContentItem)
        .filter(ContentItem.id == item_id, ContentItem.user_id == user.id)
        .first()
    )
    if not item:
        raise ValueError(f"Content item '{item_id}' not found")

    # Check if already in list
    existing = db.execute(
        content_list_membership.select().where(
            content_list_membership.c.content_item_id == item.id,
            content_list_membership.c.list_id == lst.id,
        )
    ).fetchone()

    if existing:
        return {
            "status": "already_in_list",
            "item_id": str(item.id),
            "list_id": str(lst.id),
        }

    db.execute(
        content_list_membership.insert().values(
            content_item_id=item.id,
            list_id=lst.id,
            added_by=user.id,
        )
    )
    db.commit()

    return {"status": "added", "item_id": str(item.id), "list_id": str(lst.id)}
