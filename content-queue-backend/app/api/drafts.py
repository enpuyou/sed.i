from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.list import List
from app.models.draft import Draft
from app.schemas.draft import DraftCreate, DraftUpdate, DraftResponse

router = APIRouter(tags=["drafts"])


def _verify_list_ownership(list_id: UUID, user: User, db: Session) -> List:
    """Verify that the list exists and belongs to the user."""
    list_obj = (
        db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    )
    if not list_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="List not found"
        )
    return list_obj


@router.get("/lists/{list_id}/draft", response_model=DraftResponse)
def get_draft(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get the draft for a list. Returns 404 if no draft exists yet."""
    _verify_list_ownership(list_id, current_user, db)

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == current_user.id)
        .first()
    )
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No draft found for this list"
        )
    return draft


@router.post(
    "/lists/{list_id}/draft",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_draft(
    list_id: UUID,
    data: DraftCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Create a new draft for a list. Only one draft per list per user."""
    _verify_list_ownership(list_id, current_user, db)

    # Check for existing draft
    existing = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == current_user.id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A draft already exists for this list. Use PATCH to update it.",
        )

    draft = Draft(
        list_id=list_id,
        user_id=current_user.id,
        content=data.content,
        title=data.title,
        word_count=data.word_count,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.patch("/lists/{list_id}/draft", response_model=DraftResponse)
def update_draft(
    list_id: UUID,
    data: DraftUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Update the draft content (autosave target). Creates draft if it doesn't exist."""
    _verify_list_ownership(list_id, current_user, db)

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == current_user.id)
        .first()
    )

    if not draft:
        # Auto-create on first patch (simplifies frontend: just always PATCH)
        draft = Draft(
            list_id=list_id,
            user_id=current_user.id,
            content=data.content or "",
            title=data.title,
            word_count=data.word_count or 0,
        )
        db.add(draft)
    else:
        if data.content is not None:
            draft.content = data.content
        if data.title is not None:
            draft.title = data.title
        if data.word_count is not None:
            draft.word_count = data.word_count

    db.commit()
    db.refresh(draft)
    return draft


@router.delete("/lists/{list_id}/draft", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Delete the draft for a list."""
    _verify_list_ownership(list_id, current_user, db)

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == current_user.id)
        .first()
    )
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No draft found for this list"
        )

    db.delete(draft)
    db.commit()
    return None
