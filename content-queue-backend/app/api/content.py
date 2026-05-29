"""
Content item CRUD and ingestion endpoints.

Owns the URL-to-library seam: normalization, duplicate detection, item creation,
list attachment, and Celery task dispatch. Does NOT do extraction or embedding —
those are Celery tasks.
"""

import re
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.list import content_list_membership
from app.models.user import User
from app.models.content import ContentItem
from app.schemas.content import (
    ContentItemCreate,
    ContentItemResponse,
    ContentItemUpdate,
    ContentItemList,
    ContentItemDetail,
)
from app.services.content import ingest_url, DuplicateContentError
from app.tasks.summarization import generate_summary


def _clean_extension_html(
    html: str,
    title: str | None,
    description: str | None,
    thumbnail: str | None,
) -> str:
    """
    Strip metadata elements from Readability-extracted HTML that are displayed
    separately in the reader (title header, description block, thumbnail image).
    Mirrors the dedup logic in extraction.py's xml_to_html().
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove <h1> matching the title — reader shows title in its own header
    if title:
        title_lower = title.strip().lower()
        for h1 in soup.find_all("h1"):
            if h1.get_text(strip=True).lower() == title_lower:
                h1.decompose()

    # Remove <p> matching the description — reader shows description block separately
    if description:
        desc_lower = description.strip().lower()
        for p in soup.find_all("p"):
            if p.get_text(strip=True).lower() == desc_lower:
                p.decompose()

    # Remove <img> matching the thumbnail filename — reader shows thumbnail above content.
    # Compare by filename so CDN path/query-string differences don't matter.
    if thumbnail:
        og_file = thumbnail.split("?")[0].split("/")[-1]
        if og_file:
            for img in soup.find_all("img"):
                src = (img.get("src") or img.get("data-src") or "").split("?")[0]
                if src.split("/")[-1] == og_file:
                    (img.find_parent("figure") or img).decompose()

    return str(soup)


def compute_reading_status(
    is_read: bool, read_position: float | None, is_archived: bool
) -> str:
    """
    Compute reading status based on read flags and position.

    - 'archived': if item is archived
    - 'read': if is_read flag is True OR read_position >= 0.9
    - 'in_progress': if read_position > 0 and < 0.9
    - 'unread': if read_position is 0 or None
    """
    if is_archived:
        return "archived"
    if is_read or (read_position and read_position >= 0.9):
        return "read"
    if read_position and read_position > 0:
        return "in_progress"
    return "unread"


def update_reading_patterns(user: User, item: ContentItem) -> None:
    """
    Update user's reading patterns based on completed article.
    Tracks: average reading time, preferred tags.
    """
    if not user.reading_patterns:
        user.reading_patterns = {}

    # Track reading time
    if item.reading_time_minutes:
        if "readings" not in user.reading_patterns:
            user.reading_patterns["readings"] = []
        user.reading_patterns["readings"].append(item.reading_time_minutes)

        # Keep only last 20 readings for rolling average
        if len(user.reading_patterns["readings"]) > 20:
            user.reading_patterns["readings"] = user.reading_patterns["readings"][-20:]

        # Calculate average reading time
        avg = sum(user.reading_patterns["readings"]) / len(
            user.reading_patterns["readings"]
        )
        user.reading_patterns["avg_reading_time"] = round(avg, 1)

    # Track preferred tags
    if item.tags:
        if "preferred_tags" not in user.reading_patterns:
            user.reading_patterns["preferred_tags"] = []
        for tag in item.tags:
            if tag not in user.reading_patterns["preferred_tags"]:
                user.reading_patterns["preferred_tags"].append(tag)


router = APIRouter(prefix="/content", tags=["content"])


@router.post(
    "", response_model=ContentItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_content_item(
    request: Request,
    item_data: ContentItemCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Save a new link/article.

    - Creates content item with status 'pending'
    - Trigger background job to extract metadata/full text
    - Optionally adds to specified lists
    """
    import json

    is_extension = bool(item_data.pre_extracted_html)

    try:
        new_item = ingest_url(
            url=item_data.url,
            user=current_user,
            db=db,
            submitted_via="extension" if is_extension else "web",
            dispatch_extraction=not is_extension,
        )
    except DuplicateContentError as exc:
        raise HTTPException(
            status_code=409,
            detail=json.dumps(
                {
                    "message": "Already in your library",
                    "existing_id": exc.existing_id,
                    "is_archived": exc.is_archived,
                }
            ),
        )

    # Add to lists if specified
    if item_data.list_ids:
        for list_id in item_data.list_ids:
            # Verify list exists and belongs to user
            from app.models.list import List

            list_obj = (
                db.query(List)
                .filter(List.id == list_id, List.owner_id == current_user.id)
                .first()
            )

            if list_obj:
                stmt = content_list_membership.insert().values(
                    content_item_id=new_item.id,
                    list_id=list_id,
                    added_by=current_user.id,
                )
                db.execute(stmt)
        db.commit()

    # Extension path: pre-extracted HTML provided — skip fetch/trafilatura pipeline
    if item_data.pre_extracted_html:
        html = _clean_extension_html(
            item_data.pre_extracted_html,
            title=item_data.pre_extracted_title,
            description=item_data.pre_extracted_description,
            thumbnail=item_data.pre_extracted_thumbnail,
        )
        new_item.full_text = html
        if item_data.pre_extracted_title:
            new_item.title = item_data.pre_extracted_title
        if item_data.pre_extracted_author:
            new_item.author = item_data.pre_extracted_author
        if item_data.pre_extracted_description:
            new_item.description = item_data.pre_extracted_description
        if item_data.pre_extracted_thumbnail:
            new_item.thumbnail_url = item_data.pre_extracted_thumbnail
        if item_data.pre_extracted_published_date:
            try:
                from dateutil import parser as dateparser

                new_item.published_date = dateparser.parse(
                    item_data.pre_extracted_published_date
                )
            except Exception:
                pass
        new_item.processing_status = "completed"
        if item_data.pre_extracted_access_restricted:
            new_item.processing_error = (
                "Content appears restricted by a paywall or source access controls"
            )
        # Compute word count from stripped HTML
        word_count = len(re.sub(r"<[^>]+>", " ", html).split())
        new_item.word_count = word_count
        new_item.reading_time_minutes = max(1, round(word_count / 200))
        # Create highlights captured in ephemeral reader (atomic with the article)
        if item_data.initial_highlights:
            from app.models.highlight import Highlight

            for hl in item_data.initial_highlights:
                db.add(
                    Highlight(
                        content_item_id=new_item.id,
                        user_id=current_user.id,
                        text=hl.text,
                        note=hl.note,
                        start_offset=hl.start_offset,
                        end_offset=hl.end_offset,
                        color=hl.color,
                    )
                )

        db.commit()
        # Fill any metadata gaps (e.g. missing thumbnail) and generate embedding.
        from app.tasks.embedding import generate_embedding
        from app.tasks.extraction import extract_metadata

        extract_metadata.delay(str(new_item.id))
        generate_embedding.delay(str(new_item.id))

    return new_item


@router.get("", response_model=ContentItemList)
def list_content_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    is_read: bool | None = None,
    is_archived: bool | None = None,
    tag: str | None = Query(None),  # Filter by tag
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    List all content items for current user.

    - Supports pagination (skip/limit)
    - Filter by read/archived status
    - Filter by tag
    - Excludes soft-deleted items
    """
    # Base query: user's items that aren't deleted
    query = db.query(ContentItem).filter(
        ContentItem.user_id == current_user.id, ContentItem.deleted_at.is_(None)
    )

    # Apply filters
    if is_read is not None:
        query = query.filter(ContentItem.is_read == is_read)
    if is_archived is not None:
        query = query.filter(ContentItem.is_archived == is_archived)
    if tag is not None:
        query = query.filter(ContentItem.tags.contains([tag]))

    # Get total count
    total = query.count()

    # Get paginated items
    items = (
        query.order_by(ContentItem.created_at.desc()).offset(skip).limit(limit).all()
    )

    return {"items": items, "total": total, "skip": skip, "limit": limit}


# IMPORTANT: Specific literal paths MUST come before generic /{item_id} routes
# Otherwise FastAPI will try to parse "recommended", "tags" etc. as UUIDs


@router.get("/tags", response_model=list)
def get_user_tags(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get all unique tags for the current user with occurrence counts.
    Returns list of (tag, count) tuples, sorted by frequency.
    """
    from sqlalchemy import func

    tag_counts = (
        db.query(
            func.unnest(ContentItem.tags).label("tag"),
            func.count("*").label("count"),
        )
        .filter(
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .group_by("tag")
        .order_by(func.count("*").desc())
        .all()
    )

    return [{"tag": tag, "count": count} for tag, count in tag_counts]


@router.get("/recommended", response_model=ContentItemList)
def get_recommended_content(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    mood: str | None = Query(None),  # "quick_read", "deep_dive", "light"
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get recommended content items for the user.

    Scoring factors (no ML):
    - Embedding similarity to recently read articles
    - Reading time match (based on user's patterns)
    - Recency (newer articles scored higher with decay)
    - Tag overlap with user's preferred tags
    """
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)

    # Get recently read articles (last 7 days)
    seven_days_ago = now - timedelta(days=7)
    recent_reads = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == current_user.id,
            ContentItem.is_read,
            ContentItem.read_at >= seven_days_ago,
            ContentItem.embedding.isnot(None),
        )
        .all()
    )

    # Get unread content
    unread = (
        db.query(ContentItem)
        .filter(
            ContentItem.user_id == current_user.id,
            ContentItem.is_read.is_(False),
            ContentItem.deleted_at.is_(None),
        )
        .all()
    )

    if not unread:
        return {"items": [], "total": 0, "skip": skip, "limit": limit}

    # Score each unread item
    scored_items = []

    for item in unread:
        score = 0

        # Factor 1: Embedding similarity (if we have recent reads)
        if recent_reads and item.embedding is not None:
            similarities = []
            for recent in recent_reads:
                if recent.embedding is None:
                    continue
                # Cosine similarity using pure Python
                a = list(item.embedding)
                b = list(recent.embedding)
                dot = sum(x * y for x, y in zip(a, b))
                norm_a = sum(x * x for x in a) ** 0.5
                norm_b = sum(x * x for x in b) ** 0.5
                if norm_a > 0 and norm_b > 0:
                    similarity = dot / (norm_a * norm_b)
                    similarities.append(similarity)
            if similarities:
                score += max(similarities) * 30  # Max similarity score: 30 points

        # Factor 2: Recency (newer is better)
        days_old = (now - item.created_at).days
        recency_score = max(0, 20 - (days_old / 10))  # Decay over 200 days
        score += recency_score

        # Factor 3: Tag overlap with user's preferred tags
        if (
            current_user.reading_patterns
            and "preferred_tags" in current_user.reading_patterns
        ):
            preferred = set(current_user.reading_patterns["preferred_tags"])
            item_tags = set(item.tags or [])
            overlap = len(preferred & item_tags)
            score += overlap * 10

        # Factor 4: Reading time match (if user has patterns)
        if (
            current_user.reading_patterns
            and "avg_reading_time" in current_user.reading_patterns
            and item.reading_time_minutes
        ):
            user_avg = current_user.reading_patterns["avg_reading_time"]
            time_diff = abs(item.reading_time_minutes - user_avg)
            time_match = max(0, 15 - time_diff / 2)  # Penalty for big time differences
            score += time_match

        # Apply mood filter
        if mood:
            if (
                mood == "quick_read"
                and item.reading_time_minutes
                and item.reading_time_minutes > 10
            ):
                continue  # Skip long articles
            elif (
                mood == "deep_dive"
                and item.reading_time_minutes
                and item.reading_time_minutes < 10
            ):
                continue  # Skip short articles
            elif mood == "light" and item.word_count and item.word_count > 5000:
                continue  # Skip very long articles

        scored_items.append((item, score))

    # Sort by score (highest first)
    scored_items.sort(key=lambda x: x[1], reverse=True)

    # Paginate
    total = len(scored_items)
    items = [item for item, _ in scored_items[skip : skip + limit]]

    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{item_id}", response_model=ContentItemDetail)
def get_content_item(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get a specific content item.

    - Only returns if item belongs to current user
    - Returns 404 if not found or deleted
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    return item


@router.get("/{item_id}/full", response_model=ContentItemDetail)
def get_content_item_full(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Get a content item with full text.

    - Returns complete article content
    - Use this for reading view
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    return item


@router.get("/{item_id}/pdf-url")
def get_pdf_presigned_url(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Return a short-lived presigned S3 URL for downloading the original PDF.

    Returns 404 if the item doesn't exist or has no S3-stored PDF.
    Returns 503 if S3 is not configured.
    """
    from app.core.storage import presign_url

    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    if not item.s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No PDF stored for this item",
        )

    url = presign_url(item.s3_key)
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage not configured",
        )

    return {"url": url}


@router.patch("/{item_id}", response_model=ContentItemResponse)
async def update_content_item(
    item_id: UUID,
    update_data: ContentItemUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Update a content item.

    - Can mark as read/unread
    - Can archive/unarchive
    """
    # Find item
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    # Update fields
    if update_data.title is not None:
        item.title = update_data.title

    if update_data.description is not None:
        item.description = update_data.description

    if update_data.is_read is not None:
        item.is_read = update_data.is_read
        if update_data.is_read:
            item.read_at = datetime.utcnow()
            item.read_position = 0.0
            # Update user's reading patterns when manually marked as read
            update_reading_patterns(current_user, item)
        else:
            item.read_at = None
            item.read_position = 0.0

    if update_data.is_archived is not None:
        item.is_archived = update_data.is_archived

    if update_data.is_public is not None:
        item.is_public = update_data.is_public

    if update_data.read_position is not None:
        item.read_position = update_data.read_position
        # Auto-mark as read if scrolled to near the end
        if item.read_position >= 0.9 and not item.is_read:
            item.is_read = True
            item.read_at = datetime.utcnow()
            # Update reading patterns when auto-marked as read
            update_reading_patterns(current_user, item)

    if update_data.tags is not None:
        item.tags = update_data.tags

    if getattr(update_data, "auto_tags", None) is not None:
        item.auto_tags = update_data.auto_tags

    if update_data.full_text is not None:
        item.full_text = update_data.full_text

    if update_data.author is not None:
        item.author = update_data.author

    if update_data.published_date is not None:
        item.published_date = update_data.published_date

    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_content_item(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Soft delete a content item.

    - Sets deleted_at timestamp
    - Item won't appear in lists anymore
    - Can be restored later (if we build that feature)
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    # Soft delete
    item.deleted_at = datetime.utcnow()
    db.commit()

    return None


@router.post(
    "/{item_id}/summary", response_model=dict, status_code=status.HTTP_202_ACCEPTED
)
def generate_content_summary(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Trigger summary generation for a content item.
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    # Trigger task
    generate_summary.delay(str(item.id))

    return {"status": "processing"}


@router.post("/{item_id}/tags/accept", status_code=status.HTTP_200_OK)
def accept_auto_tags(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Accept auto-generated tags for an item.
    Copies auto_tags → tags.
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    if item.auto_tags:
        item.tags = item.auto_tags
        db.commit()

    return {"status": "accepted", "tags": item.tags}


@router.post("/{item_id}/tags/dismiss", status_code=status.HTTP_200_OK)
def dismiss_auto_tags(
    item_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Dismiss auto-generated tags for an item.
    Clears auto_tags, keeps user's tags unchanged.
    """
    item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    item.auto_tags = []
    db.commit()

    return {"status": "dismissed", "tags": item.tags}
