"""
Celery task: generate_auto_tags.

Suggests tags for a ContentItem using pgvector similarity against existing
user tags (cheap path) with gpt-4o-mini fallback. Stores suggestions in
ContentItem.auto_tags. User accepts/dismisses from the UI.

Dispatch: generate_auto_tags.delay(item_id)
"""

from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.content import ContentItem
from app.tasks.base import DatabaseTask, html_to_plain
from uuid import UUID
import logging
from openai import OpenAI
import json
import re

logger = logging.getLogger(__name__)


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def generate_tags(self, content_item_id: str):
    """
    Auto-generate tags for a content item using hybrid approach.

    1. First pass (free): Check embedding similarity to tagged articles
    2. Second pass (cheap LLM): Call Haiku if no good matches found
    """
    try:
        # Get content item
        item = (
            self.db.query(ContentItem)
            .filter(ContentItem.id == UUID(content_item_id))
            .first()
        )
        if not item:
            logger.error(f"Content item {content_item_id} not found")
            return

        # Skip if no embedding available
        if item.embedding is None:
            logger.warning(f"No embedding for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_embedding"}

        # Guard: validate embedding is a flat 1-D sequence (pgvector requirement).
        # SQLAlchemy/pgvector may return a numpy array — check ndim if available,
        # otherwise fall back to checking the first element type.
        emb = item.embedding
        try:
            import numpy as np

            if isinstance(emb, np.ndarray):
                if emb.ndim != 1:
                    logger.warning(
                        f"Malformed embedding for {content_item_id} — skipping similarity pass"
                    )
                    emb = None
            elif not isinstance(emb, (list, tuple)) or (
                len(emb) > 0 and isinstance(emb[0], (list, tuple))
            ):
                logger.warning(
                    f"Malformed embedding for {content_item_id} — skipping similarity pass"
                )
                emb = None
        except Exception:
            emb = None

        logger.info(f"Generating tags for {item.original_url}")

        # PASS 1: Embedding-based similarity (free)
        suggested_tags = (
            find_similar_tags_by_embedding(self.db, item) if emb is not None else []
        )

        if suggested_tags and should_accept_tags(suggested_tags):
            item.auto_tags = suggested_tags
            item.tags = suggested_tags  # Auto-accept if high confidence
            item.processing_status = "completed"
            self.db.commit()
            logger.info(f"Auto-tagged {item.original_url} with {suggested_tags}")
            return {
                "content_item_id": content_item_id,
                "tags": suggested_tags,
                "source": "embedding_similarity",
                "status": "completed",
            }

        # PASS 2: LLM-based tagging (cheap with gpt-4o-mini)
        user_vocabulary = get_user_tag_vocabulary(self.db, item.user_id)
        llm_tags = generate_tags_with_llm(
            item.title, item.description, item.full_text, user_vocabulary
        )

        if llm_tags:
            item.auto_tags = llm_tags
            item.tags = llm_tags  # Auto-accept from LLM
            item.processing_status = "completed"
            self.db.commit()
            logger.info(f"LLM-tagged {item.original_url} with {llm_tags}")
            return {
                "content_item_id": content_item_id,
                "tags": llm_tags,
                "source": "llm_tagging",
                "status": "completed",
            }

        # No tags generated, but processing is done
        item.processing_status = "completed"
        self.db.commit()
        return {
            "content_item_id": content_item_id,
            "status": "completed",
            "message": "No tags generated",
        }

    except Exception as e:
        logger.error(f"Failed to generate tags for {content_item_id}: {str(e)}")
        # Ensure we don't get stuck in processing
        try:
            # re-query item to avoid detached instance issues if session closed?
            # But self.db is session.
            item = (
                self.db.query(ContentItem)
                .filter(ContentItem.id == UUID(content_item_id))
                .first()
            )
            if item:
                item.processing_status = "completed"
                # Optional: item.processing_error = f"Tagging error: {str(e)}"
                self.db.commit()
        except Exception as db_e:
            logger.error(f"Failed to update status on tagging error: {db_e}")

        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}


def find_similar_tags_by_embedding(db: Session, item: ContentItem) -> list:
    """
    Find similar articles already tagged, suggest their tags.
    Uses pgvector cosine distance — only considers articles within a tight
    similarity threshold to avoid cross-contaminating unrelated content.
    """
    # Cosine distance threshold: e.g. 0.25 means cosine similarity >= 0.75.
    # L2 distance (<->) is used by pgvector's default index; for cosine
    # we use <=> (cosine distance operator).
    SIMILARITY_THRESHOLD = (
        1.0 - settings.SIMILARITY_THRESHOLD_TAGS
    )  # cosine distance; lower = more similar

    # op("<=>")(value) doesn't carry type info so pgvector can't bind the param.
    # Use raw SQL with CAST(:emb AS vector) which works reliably.
    try:
        import numpy as np

        emb_list = (
            item.embedding.tolist()
            if isinstance(item.embedding, np.ndarray)
            else list(item.embedding)
        )
        emb_str = str(emb_list)  # "[0.1, 0.2, ...]" — PostgreSQL vector literal format
    except Exception:
        return []

    from sqlalchemy import text as sa_text

    rows = db.execute(
        sa_text(
            """
            SELECT id FROM content_items
            WHERE user_id = :user_id
              AND id != :item_id
              AND tags IS NOT NULL
              AND embedding IS NOT NULL
              AND embedding <=> CAST(:emb AS vector) < :threshold
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT 3
        """
        ),
        {
            "user_id": str(item.user_id),
            "item_id": str(item.id),
            "emb": emb_str,
            "threshold": SIMILARITY_THRESHOLD,
        },
    ).fetchall()

    if not rows:
        return []

    ids = [r[0] for r in rows]
    similar_items = db.query(ContentItem).filter(ContentItem.id.in_(ids)).all()

    if not similar_items:
        return []

    # Collect tags from similar articles
    tag_counts = {}
    for similar_item in similar_items:
        if similar_item.tags:
            for tag in similar_item.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Return tags that appeared in more than one similar article (higher confidence)
    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    return [tag for tag, count in sorted_tags[:5] if count >= 2]


def should_accept_tags(tags: list) -> bool:
    """Accept embedding-suggested tags only if we found confident matches."""
    return len(tags) >= 2


def get_user_tag_vocabulary(db: Session, user_id: UUID) -> list:
    """Get all unique tags user has ever created"""
    existing_tags = (
        db.query(func.unnest(ContentItem.tags))
        .filter(ContentItem.user_id == user_id)
        .distinct()
        .all()
    )
    return [tag[0] for tag in existing_tags if tag[0]]


def generate_tags_with_llm(
    title: str, description: str, full_text: str, user_vocabulary: list
) -> list:
    """Call OpenAI (gpt-4o-mini) to generate tags."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # Prepare context from article — strip HTML tags first (full_text is always HTML)
    text_parts = [title, description]
    if full_text:
        plain = html_to_plain(full_text)
        words = plain.split()[:800]
        text_parts.append(" ".join(words))

    article_context = "\n\n".join(t for t in text_parts if t)

    prompt = f"""Analyze this article and suggest 3-5 relevant tags.

User's existing tags: {", ".join(user_vocabulary) if user_vocabulary else "None yet"}

Article:
{article_context}

Return ONLY a JSON list of tags (strings). Example: ["Technology", "AI", "Opinion"]
Reuse the user's existing tags when relevant."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        # Parse tags from response
        content = response.choices[0].message.content
        # Try to extract JSON list
        try:
            # Handle potential wrapper object if model outputs {"tags": [...]}
            data = json.loads(content)
            if isinstance(data, list):
                tags = data
            elif isinstance(data, dict):
                # Look for list values
                tags = next((v for v in data.values() if isinstance(v, list)), [])
            else:
                tags = []

            return [str(tag)[:100] for tag in tags[:5]]  # Limit tag length
        except json.JSONDecodeError:
            # If JSON parsing fails, extract quoted strings
            matches = re.findall(r'"([^"]+)"', content)
            if matches:
                return matches[:5]

        return []

    except Exception as e:
        logger.error(f"LLM tagging failed: {str(e)}")
        return []
