"""
Draft CRUD endpoints.

Drafts are long-form writing pieces associated with a List (one per List).
Created automatically when a List is created; updated via PATCH.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from uuid import UUID
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.list import List
from app.models.draft import Draft
from app.models.content import ContentItem
from app.schemas.draft import DraftCreate, DraftUpdate, DraftResponse

logger = logging.getLogger(__name__)

_MIN_DRAFT_WORDS = 50
_MAX_QUERY_CHARS = 200
_MAX_RELEVANT_RESULTS = 5

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


class RelevantReadItem(BaseModel):
    id: str
    title: str | None
    tags: list[str]
    thumbnail_url: str | None = None


class RelevantReadsResponse(BaseModel):
    items: list[RelevantReadItem]


@router.get(
    "/lists/{list_id}/draft/relevant-reads", response_model=RelevantReadsResponse
)
def get_relevant_reads(
    list_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> RelevantReadsResponse:
    """
    Return up to 5 library articles relevant to the current draft content.

    Uses the draft title + first 200 chars of content as the search query.
    Returns {items: []} for drafts with fewer than 50 words.
    """
    _verify_list_ownership(list_id, current_user, db)

    draft = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == current_user.id)
        .first()
    )

    content = (draft.content or "") if draft else ""
    word_count = len(content.split()) if content.strip() else 0

    if word_count < _MIN_DRAFT_WORDS:
        return RelevantReadsResponse(items=[])

    # Build search query from title + start of content
    title_part = (draft.title or "").strip()
    content_part = content[:_MAX_QUERY_CHARS].strip()
    query = f"{title_part} {content_part}".strip()

    if not query:
        return RelevantReadsResponse(items=[])

    try:
        from app.core.hybrid_search import hybrid_search, get_user_search_context

        user_authors, user_tags = get_user_search_context(current_user, db)
        results = hybrid_search(
            query=query,
            user=current_user,
            db=db,
            limit=_MAX_RELEVANT_RESULTS,
            mode="full",
            user_authors=user_authors,
            user_tags=user_tags,
        )
    except Exception as e:
        logger.error(f"relevant-reads search failed for list {list_id}: {e}")
        return RelevantReadsResponse(items=[])

    items = []
    for r in results[:_MAX_RELEVANT_RESULTS]:
        item_id = r.get("id") or r.get("content_item_id")
        if not item_id:
            continue
        article = db.query(ContentItem).filter(ContentItem.id == item_id).first()
        if not article or article.user_id != current_user.id:
            continue
        items.append(
            RelevantReadItem(
                id=str(article.id),
                title=article.title,
                tags=list(article.tags or []),
                thumbnail_url=article.thumbnail_url,
            )
        )

    return RelevantReadsResponse(items=items)
