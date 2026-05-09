"""
Highlight CRUD endpoints.

Highlights are user text selections within a ContentItem. Creating a Highlight
also triggers async embedding generation (for Connection discovery later).
Nested under /content/{content_id}/highlights.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.highlight import Highlight
from app.models.content import ContentItem
from app.schemas.highlight import HighlightCreate, HighlightUpdate, HighlightResponse

router = APIRouter()


@router.post(
    "/content/{content_id}/highlights",
    response_model=HighlightResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_highlight(
    content_id: UUID,
    highlight_data: HighlightCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new highlight on a content item"""
    # Verify content exists and belongs to user
    content = (
        db.query(ContentItem)
        .filter(ContentItem.id == content_id, ContentItem.user_id == current_user.id)
        .first()
    )

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    # Create highlight
    highlight = Highlight(
        content_item_id=content_id,
        user_id=current_user.id,
        **highlight_data.model_dump(),
    )
    db.add(highlight)
    db.commit()
    db.refresh(highlight)

    return highlight


@router.get("/content/{content_id}/highlights", response_model=List[HighlightResponse])
async def get_highlights(
    content_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all highlights for a content item"""
    # Verify content belongs to user
    content = (
        db.query(ContentItem)
        .filter(ContentItem.id == content_id, ContentItem.user_id == current_user.id)
        .first()
    )

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    highlights = (
        db.query(Highlight)
        .filter(Highlight.content_item_id == content_id)
        .order_by(Highlight.start_offset)
        .all()
    )

    return highlights


@router.patch("/highlights/{highlight_id}", response_model=HighlightResponse)
async def update_highlight(
    highlight_id: UUID,
    update_data: HighlightUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a highlight's note or color"""
    highlight = (
        db.query(Highlight)
        .filter(Highlight.id == highlight_id, Highlight.user_id == current_user.id)
        .first()
    )

    if not highlight:
        raise HTTPException(status_code=404, detail="Highlight not found")

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(highlight, key, value)

    db.commit()
    db.refresh(highlight)

    return highlight


@router.delete("/highlights/{highlight_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_highlight(
    highlight_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a highlight"""
    highlight = (
        db.query(Highlight)
        .filter(Highlight.id == highlight_id, Highlight.user_id == current_user.id)
        .first()
    )

    if not highlight:
        raise HTTPException(status_code=404, detail="Highlight not found")

    db.delete(highlight)
    db.commit()

    return None
