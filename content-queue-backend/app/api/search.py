"""
Search endpoints.

/search/semantic  — Hybrid search (keyword + filter + semantic + RRF). Delegates
                    to app/core/hybrid_search.py. Returns SimilarContentResponse list.
/search/{id}/similar         — pgvector cosine similarity for a ContentItem.
/search/connections/{hid}    — Highlight-level semantic connections.
/search/connections/article/ — All cross-article connections for a ContentItem's highlights.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from uuid import UUID
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.schemas.content import ContentItemResponse
from pydantic import BaseModel

router = APIRouter(prefix="/search", tags=["search"])


class SimilarContentResponse(BaseModel):
    """Response for similar content"""

    item: ContentItemResponse
    similarity_score: float
    match_type: str = "semantic"  # "filter" | "keyword" | "semantic" | "hybrid"
    semantic_score: float | None = (
        None  # cosine similarity, only set for semantic results
    )


class HighlightConnection(BaseModel):
    """A highlight that connects to another highlight"""

    id: str
    text: str
    color: str
    similarity_score: float


class HighlightConnectionResponse(BaseModel):
    """Response with highlight and its connections"""

    item: HighlightConnection
    from_article_id: str
    from_article_title: str


class ArticleConnection(BaseModel):
    """A connection between articles"""

    article_id: str
    article_title: str
    highlight_pairs: list[dict]  # {user_highlight, connected_highlight, similarity}
    total_similarity: float


# IMPORTANT: Specific literal paths MUST come before generic path parameters
# Otherwise /{item_id}/similar will match /semantic, /connections/..., etc.


@router.get("/semantic", response_model=list[SimilarContentResponse])
def semantic_search(
    query: str = Query(..., min_length=3),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    mode: str = Query("auto", pattern="^(auto|full)$"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Search content using hybrid routing: keyword, filter, semantic, or RRF fusion.

    The query is classified automatically:
    - Short keywords → full-text tsvector search (fast, no API call)
    - Known author/tag/domain → SQL filter (fast, no API call)
    - Natural language questions → semantic embedding search
    - Longer conceptual phrases → keyword + semantic fused with RRF
    """
    from app.core.hybrid_search import hybrid_search, get_user_search_context
    from app.models.content import ContentItem

    user_authors, user_tags = get_user_search_context(current_user, db)
    results = hybrid_search(
        query=query,
        user=current_user,
        db=db,
        limit=limit,
        offset=offset,
        mode=mode,
        user_authors=user_authors,
        user_tags=user_tags,
    )

    # Bulk-load all result ContentItems in one query to avoid N+1.
    result_ids = [r["id"] for r in results]
    items_by_id = {
        str(item.id): item
        for item in db.query(ContentItem).filter(ContentItem.id.in_(result_ids)).all()
    }

    # Map hybrid_search results to the existing SimilarContentResponse schema.
    # Each result has flat item fields + 'score'; the response model expects
    # {item: ContentItemResponse, similarity_score: float}.
    search_results = []
    for r in results:
        item = items_by_id.get(r["id"])
        if not item:
            continue
        item_dict = {
            "id": item.id,
            "user_id": item.user_id,
            "original_url": item.original_url,
            "title": item.title,
            "author": item.author,
            "description": item.description,
            "thumbnail_url": item.thumbnail_url,
            "content_type": item.content_type,
            "summary": item.summary,
            "tags": item.tags,
            "full_text": item.full_text,
            "word_count": item.word_count,
            "reading_time_minutes": item.reading_time_minutes,
            "read_position": item.read_position,
            "is_read": item.is_read,
            "is_archived": item.is_archived,
            "is_public": item.is_public,
            "processing_status": item.processing_status,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        search_results.append(
            {
                "item": item_dict,
                "similarity_score": float(r.get("score", 0.0)),
                "match_type": r.get("match_type", "semantic"),
                "semantic_score": (
                    float(r["semantic_score"])
                    if r.get("semantic_score") is not None
                    else None
                ),
            }
        )

    return search_results


@router.get(
    "/connections/{highlight_id}", response_model=list[HighlightConnectionResponse]
)
def find_highlight_connections(
    highlight_id: str,
    threshold: float = Query(settings.SIMILARITY_THRESHOLD_CONNECTIONS, ge=0, le=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Find highlights similar to the given highlight across all user's articles.

    - Searches based on highlight embeddings
    - Returns similar highlights from other articles
    - Excludes highlights from the same article
    """
    # Get the source highlight
    source_highlight = (
        db.query(Highlight)
        .filter(
            Highlight.id == highlight_id,
            Highlight.user_id == current_user.id,
        )
        .first()
    )

    if not source_highlight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Highlight not found"
        )

    if source_highlight.embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Highlight has no embedding yet. Please wait for processing.",
        )

    # Convert embedding to string format for PostgreSQL
    embedding_str = "[" + ",".join(map(str, source_highlight.embedding)) + "]"

    # Find similar highlights using pgvector
    connection_query = text(
        """
        SELECT
            h.id,
            h.text,
            h.color,
            h.content_item_id,
            ci.title as article_title,
            (1 - (h.embedding <=> CAST(:source_embedding AS vector))) as similarity
        FROM highlights h
        JOIN content_items ci ON h.content_item_id = ci.id
        WHERE h.user_id = :user_id
            AND h.content_item_id != :source_article_id
            AND h.embedding IS NOT NULL
            AND ci.deleted_at IS NULL
            AND LENGTH(h.text) >= 20
            AND (1 - (h.embedding <=> CAST(:source_embedding AS vector))) >= :threshold
        ORDER BY h.embedding <=> CAST(:source_embedding AS vector)
        LIMIT :limit
    """
    )

    results = db.execute(
        connection_query,
        {
            "source_embedding": embedding_str,
            "user_id": current_user.id,
            "source_article_id": source_highlight.content_item_id,
            "threshold": threshold,
            "limit": limit,
        },
    ).fetchall()

    # Format response
    connections = []
    for row in results:
        connections.append(
            {
                "item": {
                    "id": str(row.id),
                    "text": row.text,
                    "color": row.color,
                    "similarity_score": float(row.similarity),
                },
                "from_article_id": str(row.content_item_id),
                "from_article_title": row.article_title,
            }
        )

    return connections


@router.get("/connections/article/{content_id}", response_model=list[ArticleConnection])
def find_article_connections(
    content_id: str,
    threshold: float = Query(settings.SIMILARITY_THRESHOLD_CONNECTIONS, ge=0, le=1),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Find all cross-article connections for highlights in the given article.

    - Groups connections by connected article
    - Shows which highlights connect to other articles
    - Returns sorted by total similarity score
    """
    # Verify the article belongs to the user
    article = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == content_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    # Get all highlights for this article with embeddings
    highlights = (
        db.query(Highlight)
        .filter(
            Highlight.content_item_id == content_id,
            Highlight.user_id == current_user.id,
            Highlight.embedding.isnot(None),
        )
        .all()
    )

    if not highlights:
        return []

    # For each highlight, find connections
    article_connections = {}

    for highlight in highlights:
        embedding_str = "[" + ",".join(map(str, highlight.embedding)) + "]"

        connection_query = text(
            """
            SELECT
                h.id,
                h.text,
                h.color,
                h.content_item_id,
                ci.title as article_title,
                (1 - (h.embedding <=> CAST(:source_embedding AS vector)) / 2) as similarity
            FROM highlights h
            JOIN content_items ci ON h.content_item_id = ci.id
            WHERE h.user_id = :user_id
                AND h.content_item_id != :source_article_id
                AND h.embedding IS NOT NULL
                AND ci.deleted_at IS NULL
                AND LENGTH(h.text) >= 20
                AND (1 - (h.embedding <=> CAST(:source_embedding AS vector)) / 2) >= :threshold
            ORDER BY h.embedding <=> CAST(:source_embedding AS vector)
            LIMIT 5
        """
        )

        results = db.execute(
            connection_query,
            {
                "source_embedding": embedding_str,
                "user_id": current_user.id,
                "source_article_id": content_id,
                "threshold": threshold,
            },
        ).fetchall()

        for row in results:
            article_id = str(row.content_item_id)
            if article_id not in article_connections:
                article_connections[article_id] = {
                    "article_id": article_id,
                    "article_title": row.article_title,
                    "highlight_pairs": [],
                    "total_similarity": 0.0,
                }

            article_connections[article_id]["highlight_pairs"].append(
                {
                    "user_highlight_id": str(highlight.id),
                    "user_highlight_text": highlight.text,
                    "connected_highlight_id": str(row.id),
                    "connected_highlight_text": row.text,
                    "similarity": float(row.similarity),
                }
            )
            article_connections[article_id]["total_similarity"] += float(row.similarity)

    # Sort by total similarity and return
    return sorted(
        article_connections.values(),
        key=lambda x: x["total_similarity"],
        reverse=True,
    )


@router.get("/{item_id}/similar", response_model=list[SimilarContentResponse])
def find_similar_content(
    item_id: UUID,
    threshold: float = Query(settings.SIMILARITY_THRESHOLD_CONNECTIONS, ge=0, le=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Find content items similar to the given item.

    - Uses cosine similarity on embeddings
    - Returns most similar items first
    - Only searches user's own content
    """
    # Get the source item
    source_item = (
        db.query(ContentItem)
        .filter(
            ContentItem.id == item_id,
            ContentItem.user_id == current_user.id,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )

    if not source_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found"
        )

    if source_item.embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source item has no embedding yet. Please wait for processing to complete.",
        )

    # Convert embedding to string format for PostgreSQL
    embedding_str = "[" + ",".join(map(str, source_item.embedding)) + "]"

    # Find similar items using pgvector cosine similarity
    # cosine_distance returns 0 for identical, 2 for opposite
    # We convert to similarity score: 1 - (distance / 2)
    similar_query = text(
        """
        SELECT
            id,
            user_id,
            original_url,
            title,
            description,
            thumbnail_url,
            content_type,
            summary,
            tags,
            full_text,
            word_count,
            reading_time_minutes,
            read_position,
            is_read,
            is_archived,
            is_public,
            processing_status,
            created_at,
            updated_at,
            (1 - (embedding <=> CAST(:source_embedding AS vector))) as similarity
        FROM content_items
        WHERE user_id = :user_id
            AND id != :source_id
            AND deleted_at IS NULL
            AND embedding IS NOT NULL
            AND (1 - (embedding <=> CAST(:source_embedding AS vector))) >= :threshold
        ORDER BY embedding <=> CAST(:source_embedding AS vector)
        LIMIT :limit
    """
    )

    results = db.execute(
        similar_query,
        {
            "source_embedding": embedding_str,
            "user_id": current_user.id,
            "source_id": item_id,
            "threshold": threshold,
            "limit": limit,
        },
    ).fetchall()

    # Format response
    similar_items = []
    for row in results:
        item_dict = {
            "id": row.id,
            "user_id": row.user_id,
            "original_url": row.original_url,
            "title": row.title,
            "description": row.description,
            "thumbnail_url": row.thumbnail_url,
            "content_type": row.content_type,
            "summary": row.summary,
            "tags": row.tags,
            "full_text": row.full_text,
            "word_count": row.word_count,
            "reading_time_minutes": row.reading_time_minutes,
            "read_position": row.read_position,
            "is_read": row.is_read,
            "is_archived": row.is_archived,
            "is_public": row.is_public,
            "processing_status": row.processing_status,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

        similar_items.append(
            {"item": item_dict, "similarity_score": float(row.similarity)}
        )

    return similar_items
