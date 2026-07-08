"""
Celery task: embed_new_entities_task.

Embeds entity nodes that have no embedding yet, writing vectors to
entities.embedding. Called after every analyze_article run and also
available as a standalone backfill task.

Entities are embedded as "{type}: {name} — {description}" so the vector
captures both the entity name and its contextual description.

Dispatch: embed_new_entities_task.delay(user_id)
Direct call (tests): embed_new_entities(user_id, db=session)
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.llm_client import llm_client
from app.models.entity import Entity
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100  # embed up to 100 entities per call


def _entity_text(entity: Entity) -> str:
    """Format entity for embedding: captures type + name + description."""
    parts = [f"{entity.entity_type}: {entity.name}"]
    if entity.description:
        parts.append(entity.description)
    return " — ".join(parts)


def embed_new_entities(user_id: str, db: Session | None = None) -> dict:
    """
    Embed all entities for a user that have no embedding yet.

    Batches up to _BATCH_SIZE per call to stay within embed API limits.
    Safe to re-run — skips entities that already have embeddings.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        unembedded = (
            db.query(Entity)
            .filter(
                Entity.user_id == uuid.UUID(user_id),
                Entity.embedding.is_(None),
            )
            .limit(_BATCH_SIZE)
            .all()
        )

        if not unembedded:
            return {"user_id": user_id, "status": "nothing_to_embed"}

        texts = [_entity_text(e) for e in unembedded]
        result = llm_client.embed(texts)

        for entity, vector in zip(unembedded, result.embeddings):
            entity.embedding = vector

        db.commit()
        logger.info(f"Embedded {len(unembedded)} entities for user {user_id}")
        return {"user_id": user_id, "status": "completed", "embedded": len(unembedded)}

    except Exception as e:
        logger.error(f"embed_new_entities failed for user {user_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"user_id": user_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def embed_new_entities_task(self, user_id: str):
    return embed_new_entities(user_id, db=self.db)


@celery_app.task(base=DatabaseTask, bind=True)
def backfill_entity_embeddings(self, user_id: str | None = None):
    """
    Re-run embed_new_entities for all users (or a specific user) until
    no unembedded entities remain. Processes in batches.
    """
    from app.models.user import User

    if user_id:
        user_ids = [user_id]
    else:
        user_ids = [str(u.id) for u in self.db.query(User.id).all()]

    total = 0
    for uid in user_ids:
        while True:
            result = embed_new_entities(uid, db=self.db)
            if result["status"] != "completed":
                break
            total += result["embedded"]
            if result["embedded"] < _BATCH_SIZE:
                break  # no more unembedded for this user

    logger.info(f"Backfill complete: {total} entities embedded")
    return {"status": "completed", "total_embedded": total}
