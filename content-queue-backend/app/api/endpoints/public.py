from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from uuid import UUID

from app.core.database import get_db
from app.models.user import User
from app.models.content import ContentItem
from app.models.vinyl import VinylRecord
from app.schemas.user import UserResponse
from app.schemas.content import ContentItemList, ContentItemResponse
from app.schemas.vinyl import VinylRecordResponse

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/u/{username}", response_model=UserResponse)
def get_public_profile(username: str, db: Session = Depends(get_db)):
    """Fetch user profile details. Only succeeds if user.is_public == True."""
    user = db.query(User).filter(User.username == username).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    if not user.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This profile is private"
        )

    return user


@router.get("/u/{username}/content", response_model=ContentItemList)
def get_public_content(
    username: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Fetch the user's public queue items."""
    user = db.query(User).filter(User.username == username).first()

    if not user or not user.is_queue_public:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue is private or profile not found",
        )

    # Base query: belongs to user, and item itself must be public, and not deleted/archived
    query = db.query(ContentItem).filter(
        ContentItem.user_id == user.id,
        ContentItem.is_public == True,  # noqa: E712
        ContentItem.is_archived == False,  # noqa: E712
        ContentItem.deleted_at.is_(None),
    )

    total = query.count()
    items = query.order_by(desc(ContentItem.created_at)).offset(skip).limit(limit).all()

    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/u/{username}/content/{item_id}", response_model=ContentItemResponse)
def get_public_content_item(
    username: str, item_id: UUID, db: Session = Depends(get_db)
):
    """Fetch a single public content item by ID."""
    user = db.query(User).filter(User.username == username).first()

    if not user or not user.is_queue_public:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == user.id,
            ContentItem.is_public,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return item


@router.get("/u/{username}/vinyl", response_model=list[VinylRecordResponse])
def get_public_vinyl(
    username: str, skip: int = 0, limit: int = 50, db: Session = Depends(get_db)
):
    """Fetch the user's public vinyl records."""
    user = db.query(User).filter(User.username == username).first()

    if not user or not user.is_crates_public:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Crates are private or profile not found",
        )

    records = (
        db.query(VinylRecord)
        .filter(
            VinylRecord.user_id == user.id,
            VinylRecord.deleted_at.is_(None),
        )
        .order_by(desc(VinylRecord.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return records
