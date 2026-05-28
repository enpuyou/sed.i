"""
Celery task: cluster_user_tags_task.

Groups a user's semantic tags into reading clusters by cosine similarity.
Uses numpy union-find — no sklearn dependency required.

Dispatch: cluster_user_tags_task.delay(user_id)
Direct call (tests): cluster_user_tags(user_id, db=session)
"""

import logging
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.content import ContentItem
from app.models.tag_embedding import TagEmbedding
from app.models.reading_cluster import ReadingCluster
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

_MIN_TAGGED_ARTICLES = 10
_MIN_CLUSTER_SIZE = 3
_SIMILARITY_THRESHOLD = 0.60


def cluster_user_tags(user_id: str, db: Session | None = None) -> dict:
    """
    Cluster semantic tags for a single user and persist to reading_clusters.

    Requires ≥10 articles with tags. Clusters with <3 articles are dropped.
    Existing clusters for the user are replaced on every run (idempotent).
    """
    import numpy as np

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        uid = UUID(user_id)

        from sqlalchemy.orm import load_only

        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.user_id == uid,
                ContentItem.deleted_at.is_(None),
                func.cardinality(ContentItem.tags) > 0,
            )
            .options(load_only(ContentItem.id, ContentItem.tags))
            .all()
        )

        if len(items) < _MIN_TAGGED_ARTICLES:
            logger.info(
                f"Skipping clustering for {user_id}: only {len(items)} tagged articles"
            )
            return {
                "user_id": user_id,
                "status": "skipped",
                "reason": "fewer than 10 tagged articles",
            }

        # Map each unique tag → list of article UUIDs that carry it
        tag_to_articles: dict[str, list[UUID]] = {}
        for item in items:
            for tag in item.tags or []:
                tag_to_articles.setdefault(tag, []).append(item.id)

        unique_tags = list(tag_to_articles.keys())

        # Fetch embeddings — only tags that have been embedded can be clustered
        emb_rows = (
            db.query(TagEmbedding).filter(TagEmbedding.label.in_(unique_tags)).all()
        )
        label_to_emb = {row.label: row.embedding for row in emb_rows}
        clusterable = [t for t in unique_tags if t in label_to_emb]

        if len(clusterable) < 2:
            return {
                "user_id": user_id,
                "status": "skipped",
                "reason": "not enough embeddings",
            }

        # Build embedding matrix and compute cosine similarity
        embs = np.array([list(label_to_emb[t]) for t in clusterable], dtype=np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = embs / norms
        sim = normed @ normed.T  # shape (N, N)

        # Union-Find: merge tags with similarity >= threshold
        parent = list(range(len(clusterable)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            pa, pb = find(a), find(b)
            if pa != pb:
                parent[pa] = pb

        for i in range(len(clusterable)):
            for j in range(i + 1, len(clusterable)):
                if sim[i, j] >= _SIMILARITY_THRESHOLD:
                    union(i, j)

        del embs, normed, sim  # free N×N matrices before building output

        # Group tag indices by cluster root
        cluster_map: dict[int, list[int]] = {}
        for i in range(len(clusterable)):
            cluster_map.setdefault(find(i), []).append(i)

        new_clusters: list[dict] = []
        for indices in cluster_map.values():
            tag_group = [clusterable[i] for i in indices]
            article_ids: set[UUID] = set()
            for tag in tag_group:
                article_ids.update(tag_to_articles.get(tag, []))
            if len(article_ids) < _MIN_CLUSTER_SIZE:
                continue
            # Label = tag with most articles in this cluster
            label = max(tag_group, key=lambda t: len(tag_to_articles.get(t, [])))
            new_clusters.append(
                {
                    "label": label,
                    "article_ids": list(article_ids),
                    "tag_labels": tag_group,
                }
            )

        # Replace existing clusters for this user
        db.query(ReadingCluster).filter(ReadingCluster.user_id == uid).delete()
        for c in new_clusters:
            db.add(
                ReadingCluster(
                    user_id=uid,
                    label=c["label"],
                    article_ids=c["article_ids"],
                    tag_labels=c["tag_labels"],
                )
            )
        db.commit()

        logger.info(f"Clustered {len(new_clusters)} themes for user {user_id}")
        return {
            "user_id": user_id,
            "clusters": len(new_clusters),
            "status": "completed",
        }

    except Exception as e:
        logger.error(f"cluster_user_tags failed for {user_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"user_id": user_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


# ---------------------------------------------------------------------------
# Celery task wrappers
# ---------------------------------------------------------------------------


@celery_app.task(base=DatabaseTask, bind=True)
def cluster_user_tags_task(self, user_id: str):
    return cluster_user_tags(user_id, db=self.db)


@celery_app.task(base=DatabaseTask, bind=True)
def cluster_all_users_task(self):
    """Weekly beat task: cluster tags for all active users."""
    from app.models.user import User as UserModel

    users = self.db.query(UserModel.id).all()
    for (uid,) in users:
        cluster_user_tags_task.delay(str(uid))
    return {"dispatched": len(users)}
