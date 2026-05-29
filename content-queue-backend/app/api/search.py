"""
Search HTTP endpoints.

Routes to hybrid_search() for semantic/keyword/hybrid queries and filter
execution. Also hosts highlight connection, similar content, and search
telemetry endpoints. Does NOT implement search logic — delegates to
app/core/hybrid_search.py.
"""

import json
import logging
import re as _re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


class SimilarContentResponse(BaseModel):
    """Response for similar content"""

    item: ContentItemResponse
    similarity_score: float
    match_type: str = "semantic"  # "filter" | "keyword" | "semantic" | "hybrid"
    semantic_score: float | None = None
    shared_tags: list[str] = []


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
    shared_tags: list[str] = []


class HighlightArticleConnection(BaseModel):
    """One connected article with its metadata and matched passages."""

    article_id: str
    article_title: str
    article_author: str | None = None
    article_domain: str
    shared_tags: list[str]
    passages: list[str]
    passage_highlight_ids: list[str]
    connection_score: float


class ConnectionsForHighlightResponse(BaseModel):
    """Wrapper returned by GET /search/connections/{highlight_id}."""

    source_note: str | None = None
    connections: list[HighlightArticleConnection]


class HighlightWithConnections(BaseModel):
    """One source highlight with its article connections — used in Mode 2."""

    highlight_id: str
    highlight_text: str
    connections: list[HighlightArticleConnection]


class HighlightSearchResult(BaseModel):
    """A highlight or note that matched a search query."""

    highlight_id: str
    text: str
    note: str | None
    color: str
    content_item_id: str
    article_title: str
    score: float


class SearchResponse(BaseModel):
    """Unified search response with article and highlight results."""

    articles: list[SimilarContentResponse]
    highlights: list[HighlightSearchResult]


def _search_highlights(
    query: str, user_id, db: Session, limit: int = 10
) -> list[HighlightSearchResult]:
    """Keyword search over highlight text and notes using tsvector."""
    # to_tsquery raises a syntax error on special chars (e.g. "c++", "foo-bar").
    # Only use prefix-match form for single safe alphanumeric tokens; everything
    # else goes through websearch_to_tsquery which handles arbitrary input safely.
    _safe_token = _re.compile(r"^[A-Za-z0-9_]+$")
    words = query.strip().split()
    if len(words) == 1 and _safe_token.match(words[0]):
        tsq_expr = "to_tsquery('simple', :tsq_simple)"
        tsq_val = words[0] + ":*"
    else:
        tsq_expr = "websearch_to_tsquery('simple', :tsq_simple)"
        tsq_val = query

    sql = text(
        f"""
        SELECT h.id::text           AS highlight_id,
               h.text               AS text,
               h.note               AS note,
               h.color              AS color,
               h.content_item_id::text AS content_item_id,
               c.title              AS article_title,
               ts_rank_cd(h.search_vector, {tsq_expr}) AS score
        FROM highlights h
        JOIN content_items c ON c.id = h.content_item_id
        WHERE h.user_id = :user_id
          AND c.deleted_at IS NULL
          AND h.search_vector IS NOT NULL
          AND h.search_vector @@ {tsq_expr}
        ORDER BY score DESC
        LIMIT :limit
    """
    )

    rows = db.execute(
        sql, {"user_id": user_id, "tsq_simple": tsq_val, "limit": limit}
    ).fetchall()
    return [
        HighlightSearchResult(
            highlight_id=row.highlight_id,
            text=row.text,
            note=row.note,
            color=row.color,
            content_item_id=row.content_item_id,
            article_title=row.article_title or "",
            score=float(row.score),
        )
        for row in rows
    ]


class SearchTelemetryEvent(BaseModel):
    surface: str
    item_id: UUID
    shared_tag: str | None = None
    action: Literal["click", "dismiss"]


# IMPORTANT: Specific literal paths MUST come before generic path parameters
# Otherwise /{item_id}/similar will match /semantic, /connections/..., etc.


def _connections_for_highlight(
    highlight: "Highlight",
    source_tags: set[str],
    threshold: float,
    db: "Session",
    max_fetch: int = 100,
) -> list[HighlightArticleConnection]:
    """
    Return article-grouped connections for a single highlight.

    Returns at most 2 best passages per connected article.
    Shared tags are shown when they exist but do not gate inclusion.
    """
    if highlight.embedding is None:
        return []

    embedding_str = "[" + ",".join(map(str, highlight.embedding)) + "]"

    connection_query = text(
        """
        SELECT
            h.id,
            h.text,
            h.content_item_id,
            ci.title        AS article_title,
            ci.author       AS article_author,
            ci.original_url AS article_url,
            ci.tags         AS article_tags,
            (1 - (h.embedding <=> CAST(:source_embedding AS vector))) AS similarity
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

    rows = db.execute(
        connection_query,
        {
            "source_embedding": embedding_str,
            "user_id": highlight.user_id,
            "source_article_id": highlight.content_item_id,
            "threshold": threshold,
            "limit": max_fetch,
        },
    ).fetchall()

    # Group rows by connected article
    article_groups: dict[str, dict] = {}
    for row in rows:
        article_id = str(row.content_item_id)
        if article_id not in article_groups:
            article_groups[article_id] = {
                "article_id": article_id,
                "article_title": row.article_title or "",
                "article_author": row.article_author,
                "article_url": row.article_url or "",
                "article_tags": list(row.article_tags or []),
                "passages": [],
            }
        article_groups[article_id]["passages"].append(
            (str(row.id), float(row.similarity), row.text)
        )

    # Resolve shared tags and rank passages per article group
    enriched = []
    for group in article_groups.values():
        shared = sorted(source_tags & set(group["article_tags"]))
        top_passages = sorted(group["passages"], key=lambda x: -x[1])[:2]
        top_sim = top_passages[0][1] if top_passages else 0.0
        parsed = urlparse(group["article_url"])
        domain = parsed.netloc.removeprefix("www.") if parsed.netloc else ""
        enriched.append(
            (
                len(shared),
                top_sim,
                HighlightArticleConnection(
                    article_id=group["article_id"],
                    article_title=group["article_title"],
                    article_author=group["article_author"],
                    article_domain=domain,
                    shared_tags=shared,
                    passages=[t for _, _, t in top_passages],
                    passage_highlight_ids=[hid for hid, _, _ in top_passages],
                    connection_score=round(top_sim, 3),
                ),
            )
        )

    # Sort: shared-tag connections first, then by best passage similarity
    enriched.sort(key=lambda x: (-x[0], -x[1]))
    return [conn for _, _, conn in enriched]


@router.get("/semantic", response_model=SearchResponse)
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

    highlight_results = _search_highlights(query, current_user.id, db, limit=10)

    return SearchResponse(articles=search_results, highlights=highlight_results)


@router.post("/telemetry", status_code=204)
def record_search_event(
    payload: SearchTelemetryEvent,
    current_user: User = Depends(get_current_active_user),
):
    """Record a search interaction event. Logged server-side; no DB write."""
    logger.info(
        json.dumps(
            {
                "event": "search_click",
                "surface": payload.surface,
                "item_id": str(payload.item_id),
                "shared_tag": payload.shared_tag,
                "action": payload.action,
                "user_id": str(current_user.id),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
    )


@router.get(
    "/connections/{highlight_id}", response_model=ConnectionsForHighlightResponse
)
def find_highlight_connections(
    highlight_id: str,
    threshold: float = Query(settings.SIMILARITY_THRESHOLD_CONNECTIONS, ge=0, le=1),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Find connections for a single highlight, grouped by connected article.

    Returns article metadata (author, domain, shared tags) and matched passages.
    Shared tags are shown when they exist but do not gate inclusion.
    """
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

    source_article = (
        db.query(ContentItem)
        .filter(ContentItem.id == source_highlight.content_item_id)
        .first()
    )
    source_tags = set(source_article.tags or []) if source_article else set()

    connections = _connections_for_highlight(
        source_highlight, source_tags, threshold, db
    )

    return ConnectionsForHighlightResponse(
        source_note=source_highlight.note,
        connections=connections,
    )


# ── Insight helpers (isolated so tests can patch them) ──────────────────────


def _get_redis_client():
    """Return a Redis client, or raise if unavailable."""
    import redis as redis_lib

    return redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)


def _call_insight(source_text: str, passages: list[str], article_title: str) -> str:
    """Return a one-sentence connection insight via llm_client."""
    from app.core.llm_client import llm_client, TASK_INSIGHT

    passages_block = "\n".join(f"- {p}" for p in passages)
    prompt = (
        f'Highlight: "{source_text}"\n\n'
        f'From "{article_title}":\n{passages_block}\n\n'
        "In one sentence, explain the specific idea that connects the highlight to these passages. "
        "Be precise, not generic. Reply with only the sentence."
    )
    result = llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        task=TASK_INSIGHT,
        max_tokens=120,
        temperature=0.3,
    )
    return result.content.strip()


class InsightResponse(BaseModel):
    insight: str | None = None


@router.get(
    "/connections/{highlight_id}/insight/{article_id}",
    response_model=InsightResponse,
)
def generate_highlight_insight(
    highlight_id: str,
    article_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Return a one-sentence insight explaining the connection between a highlight
    and a connected article. Generated by gpt-4o-mini, cached in Redis for 7 days.

    Returns {insight: null} on any generation failure — never 500.
    """
    # Verify ownership
    source_highlight = (
        db.query(Highlight)
        .filter(Highlight.id == highlight_id, Highlight.user_id == current_user.id)
        .first()
    )
    if not source_highlight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Highlight not found"
        )

    cache_key = f"insight:{highlight_id}:{article_id}"

    try:
        redis_client = _get_redis_client()
        cached = redis_client.get(cache_key)
        if cached is not None:
            return InsightResponse(insight=cached.decode("utf-8"))
    except Exception:
        redis_client = None

    # Load connected highlight passages for context
    connected_highlights = (
        db.query(Highlight)
        .filter(
            Highlight.content_item_id == article_id,
            Highlight.user_id == current_user.id,
        )
        .limit(3)
        .all()
    )
    connected_article = (
        db.query(ContentItem)
        .filter(ContentItem.id == article_id, ContentItem.user_id == current_user.id)
        .first()
    )

    passages = [h.text for h in connected_highlights]
    article_title = connected_article.title if connected_article else ""

    try:
        insight = _call_insight(source_highlight.text, passages, article_title)
        if redis_client is not None:
            try:
                redis_client.setex(cache_key, 604800, insight)  # 7 days
            except Exception:
                pass
        return InsightResponse(insight=insight)
    except Exception:
        logger.warning(
            "Insight generation failed for highlight=%s article=%s",
            highlight_id,
            article_id,
        )
        return InsightResponse(insight=None)


@router.get(
    "/connections/article/{content_id}/highlights",
    response_model=list[HighlightWithConnections],
)
def find_highlight_grouped_connections(
    content_id: str,
    threshold: float = Query(settings.SIMILARITY_THRESHOLD_CONNECTIONS, ge=0, le=1),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Find connections for all highlights in an article, grouped by source highlight.

    Only highlights that have at least one connection are returned.
    Used by ConnectionsPanel Mode 2.
    """
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

    highlights = (
        db.query(Highlight)
        .filter(
            Highlight.content_item_id == content_id,
            Highlight.user_id == current_user.id,
            Highlight.embedding.isnot(None),
        )
        .order_by(Highlight.start_offset)
        .limit(30)  # cap to keep response time bounded
        .all()
    )

    source_tags = set(article.tags or [])
    result: list[HighlightWithConnections] = []

    for highlight in highlights:
        connections = _connections_for_highlight(highlight, source_tags, threshold, db)
        if not connections:
            continue
        result.append(
            HighlightWithConnections(
                highlight_id=str(highlight.id),
                highlight_text=highlight.text,
                connections=connections,
            )
        )

    return result


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
                ci.tags as article_tags,
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
            LIMIT 20
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
                    "article_tags": list(row.article_tags or []),
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

    # Keep only the best highlight pair per connected article
    source_tags = set(article.tags or [])
    result = []
    for conn in article_connections.values():
        shared = sorted(source_tags & set(conn.pop("article_tags", [])))
        best_pair = max(conn["highlight_pairs"], key=lambda p: p["similarity"])
        conn["highlight_pairs"] = [best_pair]
        conn["total_similarity"] = best_pair["similarity"]
        conn["shared_tags"] = shared
        result.append(conn)

    # Sort: shared-tag connections first, then by similarity
    return sorted(
        result, key=lambda x: (-len(x["shared_tags"]), -x["total_similarity"])
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
    source_tags = set(source_item.tags or [])

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
        shared = sorted(source_tags & set(row.tags or []))
        similar_items.append(
            {
                "item": item_dict,
                "similarity_score": float(row.similarity),
                "shared_tags": shared,
            }
        )

    return similar_items
