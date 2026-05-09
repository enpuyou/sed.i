"""
Vinyl Record endpoints.

CRUD for the user's vinyl collection. Creating a Record triggers a Celery
task to fetch metadata from Discogs. The API is always active; the frontend
gates it with the SHOW_CRATES feature flag.
"""

from typing import List, Optional
from app.tasks.discogs import fetch_discogs_metadata
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.vinyl import VinylRecord
from app.models.user import User
from app.api.auth import get_current_active_user
from app.schemas.vinyl import (
    VinylRecordCreate,
    VinylRecordUpdate,
    VinylRecordResponse,
)
from uuid import UUID
from datetime import datetime


router = APIRouter(prefix="/vinyl", tags=["vinyl"])


@router.get("", response_model=List[VinylRecordResponse])
def get_vinyl_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    status: Optional[str] = Query(
        None, description="Filter by status (collection, wantlist, library)"
    ),
    sort_by: str = Query(
        "created_at", description="Sort field (created_at, year, artist)"
    ),
    sort_order: str = Query("desc", description="Sort order (asc, desc)"),
):
    """
    Get all vinyl records for the current user.
    """
    query = db.query(VinylRecord).filter(
        VinylRecord.user_id == current_user.id, VinylRecord.deleted_at.is_(None)
    )

    if status:
        query = query.filter(VinylRecord.status == status)

    # Sorting
    if sort_by == "year":
        sort_attr = VinylRecord.year
    elif sort_by == "artist":
        sort_attr = VinylRecord.artist
    else:
        sort_attr = VinylRecord.created_at

    if sort_order == "desc":
        query = query.order_by(sort_attr.desc())
    else:
        query = query.order_by(sort_attr.asc())

    return query.all()


@router.post(
    "", response_model=VinylRecordResponse, status_code=status.HTTP_201_CREATED
)
def create_vinyl_record(
    record_in: VinylRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new vinyl record from a Discogs URL.
    This session only creates the skeleton; the metadata is fetched in the background.
    """
    # Check if this record already exists for this user
    # Note: For simplicity, we create a new entry even if it exists for another user
    # but for the SAME user, we might want to prevent duplicates or just allow them.
    # Here we just create it.

    db_record = VinylRecord(
        user_id=current_user.id,
        discogs_url=record_in.discogs_url,
        processing_status="pending",
    )
    db.add(db_record)
    db.commit()
    db.refresh(db_record)

    # Trigger Celery task
    fetch_discogs_metadata.delay(str(db_record.id))

    return db_record


@router.get("/{id}", response_model=VinylRecordResponse)
def get_vinyl_record(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get a specific vinyl record by ID.
    """
    db_record = (
        db.query(VinylRecord)
        .filter(
            VinylRecord.id == id,
            VinylRecord.user_id == current_user.id,
            VinylRecord.deleted_at.is_(None),
        )
        .first()
    )

    if not db_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vinyl record not found"
        )
    return db_record


@router.patch("/{id}", response_model=VinylRecordResponse)
def update_vinyl_record(
    id: UUID,
    record_in: VinylRecordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Update a vinyl record's user-provided fields.
    """
    db_record = (
        db.query(VinylRecord)
        .filter(
            VinylRecord.id == id,
            VinylRecord.user_id == current_user.id,
            VinylRecord.deleted_at.is_(None),
        )
        .first()
    )

    if not db_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vinyl record not found"
        )

    update_data = record_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_record, field, value)

    db.commit()
    db.refresh(db_record)
    return db_record


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vinyl_record(
    id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Soft delete a vinyl record.
    """
    db_record = (
        db.query(VinylRecord)
        .filter(
            VinylRecord.id == id,
            VinylRecord.user_id == current_user.id,
            VinylRecord.deleted_at.is_(None),
        )
        .first()
    )

    if not db_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vinyl record not found"
        )

    db_record.deleted_at = datetime.utcnow()
    db.commit()
    return None
