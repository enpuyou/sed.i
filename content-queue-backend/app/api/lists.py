from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from uuid import UUID
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.list import List, content_list_membership
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.schemas.list import (
    ListCreate,
    ListUpdate,
    ListResponse,
    ListWithContentCount,
    AddContentToList,
    RemoveContentFromList,
)
from app.schemas.content import ContentItemResponse
from app.schemas.highlight import HighlightResponse

router = APIRouter(prefix="/lists", tags=["lists"])


@router.post("", response_model=ListResponse, status_code=status.HTTP_201_CREATED)
def create_list(
    list_data: ListCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Create a new list.
    """
    new_list = List(
        name=list_data.name,
        description=list_data.description,
        owner_id=current_user.id,
        is_shared=list_data.is_shared,
    )
    db.add(new_list)
    db.commit()
    db.refresh(new_list)

    return new_list


@router.get("", response_model=list[ListWithContentCount])
def get_user_lists(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """
    Get all lists for current user.
    Includes count of items in each list.
    """
    # Query lists with content count, excluding deleted items
    lists_with_count = (
        db.query(
            List,
            func.count(ContentItem.id).label("content_count"),
        )
        .outerjoin(
            content_list_membership, List.id == content_list_membership.c.list_id
        )
        .outerjoin(
            ContentItem,
            (ContentItem.id == content_list_membership.c.content_item_id)
            & (ContentItem.deleted_at.is_(None)),
        )
        .filter(List.owner_id == current_user.id)
        .group_by(List.id)
        .all()
    )

    # Format response
    result = []
    for list_obj, count in lists_with_count:
        list_dict = {
            "id": list_obj.id,
            "name": list_obj.name,
            "description": list_obj.description,
            "owner_id": list_obj.owner_id,
            "is_shared": list_obj.is_shared,
            "created_at": list_obj.created_at,
            "updated_at": list_obj.updated_at,
            "content_count": count,
        }
        result.append(list_dict)

    return result


@router.get("/{list_id}", response_model=ListResponse)
def get_list(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get a specific list.
    """
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    return list_obj


@router.get("/{list_id}/content", response_model=list[ContentItemResponse])
def get_list_content(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get all content items in a list.
    """
    # Verify list exists and belongs to user
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Get content items in this list (not deleted)
    content_items = (
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
        .all()
    )

    return content_items


@router.post("/{list_id}/content", status_code=status.HTTP_200_OK)
def add_content_to_list(
    list_id: UUID,
    data: AddContentToList,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Add content items to a list.
    """
    # Verify list exists and belongs to user
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Verify all content items exist and belong to user
    for content_id in data.content_item_ids:
        content_item = (
            db.query(ContentItem)
            .filter(
                ContentItem.id == content_id,
                ContentItem.user_id == current_user.id,
                ContentItem.deleted_at.is_(None),
            )
            .first()
        )

        if not content_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Content item {content_id} not found",
            )

        # Check if already in list
        existing = (
            db.query(content_list_membership)
            .filter(
                content_list_membership.c.content_item_id == content_id,
                content_list_membership.c.list_id == list_id,
            )
            .first()
        )

        # Add to list if not already there
        if not existing:
            stmt = content_list_membership.insert().values(
                content_item_id=content_id, list_id=list_id, added_by=current_user.id
            )
            db.execute(stmt)

    db.commit()

    return {"message": f"Added {len(data.content_item_ids)} items to list"}


@router.delete("/{list_id}/content", status_code=status.HTTP_200_OK)
def remove_content_from_list(
    list_id: UUID,
    data: RemoveContentFromList,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Remove content items from a list.
    """
    # Verify list exists and belongs to user
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Remove items from list
    for content_id in data.content_item_ids:
        stmt = content_list_membership.delete().where(
            content_list_membership.c.content_item_id == content_id,
            content_list_membership.c.list_id == list_id,
        )
        db.execute(stmt)

    db.commit()

    return {"message": f"Removed {len(data.content_item_ids)} items from list"}


@router.patch("/{list_id}", response_model=ListResponse)
def update_list(
    list_id: UUID,
    update_data: ListUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Update a list's name, description, or sharing status.
    """
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Update fields
    if update_data.name is not None:
        list_obj.name = update_data.name
    if update_data.description is not None:
        list_obj.description = update_data.description
    if update_data.is_shared is not None:
        list_obj.is_shared = update_data.is_shared

    db.commit()
    db.refresh(list_obj)

    return list_obj


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Delete a list.
    Note: This doesn't delete the content items, just the list and memberships.
    """
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )

    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Delete the list (cascade will delete memberships)
    db.delete(list_obj)
    db.commit()

    return None


@router.get("/{list_id}/highlights", response_model=list[HighlightResponse])
def get_list_highlights(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get all highlights across all content items in a list.
    Used by the writing workspace source pane.
    """
    # Verify list exists and belongs to user
    list_obj = (
        db.query(List)
        .filter(List.id == list_id, List.owner_id == current_user.id)
        .first()
    )
    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )

    # Get all highlights for content items in this list
    highlights = (
        db.query(Highlight)
        .join(ContentItem, Highlight.content_item_id == ContentItem.id)
        .join(
            content_list_membership,
            ContentItem.id == content_list_membership.c.content_item_id,
        )
        .filter(
            content_list_membership.c.list_id == list_id,
            Highlight.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .order_by(Highlight.created_at.desc())
        .all()
    )

    return highlights
