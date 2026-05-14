"""
Reading Themes endpoints.

GET /themes — returns the current user's reading clusters.
"""

import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.reading_cluster import ReadingCluster
from app.models.content import ContentItem
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/themes", tags=["themes"])


class TopArticle(BaseModel):
    id: str
    title: str | None
    thumbnail: str | None = None


class ClusterResponse(BaseModel):
    id: str
    label: str
    article_count: int
    tag_labels: list[str]
    top_articles: list[TopArticle]


class ThemesResponse(BaseModel):
    clusters: list[ClusterResponse]


@router.get("", response_model=ThemesResponse)
def get_themes(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> ThemesResponse:
    """Return reading clusters for the authenticated user."""
    clusters = (
        db.query(ReadingCluster)
        .filter(ReadingCluster.user_id == current_user.id)
        .order_by(ReadingCluster.updated_at.desc())
        .all()
    )

    result: list[ClusterResponse] = []
    for cluster in clusters:
        top_ids = (cluster.article_ids or [])[:3]
        top_items = (
            db.query(ContentItem).filter(ContentItem.id.in_(top_ids)).all()
            if top_ids
            else []
        )
        top_articles = [
            TopArticle(id=str(item.id), title=item.title, thumbnail=item.thumbnail_url)
            for item in top_items
        ]
        result.append(
            ClusterResponse(
                id=str(cluster.id),
                label=cluster.label,
                article_count=len(cluster.article_ids or []),
                tag_labels=cluster.tag_labels or [],
                top_articles=top_articles,
            )
        )

    return ThemesResponse(clusters=result)
