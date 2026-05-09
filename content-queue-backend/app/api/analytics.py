"""
Analytics endpoints.

Returns aggregate reading stats for the current user: total saved, read,
archived, in-progress counts, reading streak, and tag breakdown.
Route: GET /analytics/stats.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.content import ContentItem

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/stats")
async def get_user_stats(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """Get basic statistics about user's content"""

    # Base query - exclude soft-deleted items
    base_query = db.query(ContentItem).filter(
        ContentItem.user_id == current_user.id,
        ContentItem.deleted_at.is_(None),  # Exclude deleted items
    )

    # Total items saved
    total_items = base_query.count()

    # Items read
    items_read = base_query.filter(ContentItem.is_read).count()

    # Items unread
    items_unread = base_query.filter(
        ContentItem.is_read.is_(False), ContentItem.is_archived.is_(False)
    ).count()

    # Items archived
    items_archived = base_query.filter(ContentItem.is_archived).count()

    # Total reading time (sum of reading_time_minutes for all items)
    total_reading_time = (
        db.query(func.sum(ContentItem.reading_time_minutes))
        .filter(ContentItem.user_id == current_user.id)
        .filter(ContentItem.deleted_at.is_(None))
        .filter(ContentItem.reading_time_minutes.isnot(None))
        .scalar()
        or 0
    )

    # Reading time for read items
    read_reading_time = (
        db.query(func.sum(ContentItem.reading_time_minutes))
        .filter(ContentItem.user_id == current_user.id)
        .filter(ContentItem.deleted_at.is_(None))
        .filter(ContentItem.is_read)
        .filter(ContentItem.reading_time_minutes.isnot(None))
        .scalar()
        or 0
    )

    return {
        "total_items": total_items or 0,
        "items_read": items_read or 0,
        "items_unread": items_unread or 0,
        "items_archived": items_archived or 0,
        "total_reading_time_minutes": int(total_reading_time),
        "read_reading_time_minutes": int(read_reading_time),
    }
