"""
Celery task: generate_tags_task.

Extracts 3-5 fine-grained semantic labels from article content and stores
them in ContentItem.tags. Labels are specific multi-word phrases (e.g.
"deceptive alignment") not broad categories (e.g. "AI").

After writing tags, upserts each label into the tag_embeddings lookup table
so they can be used for tag-level similarity queries.

Dispatch: generate_tags_task.delay(content_item_id)
Direct call (tests): generate_tags(content_item_id, db=session)
"""

import logging
import time
from uuid import UUID

from sqlalchemy import func, text as sa_text
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm_client import llm_client, TASK_TAGGING
from app.core.llm_schemas import TagResponse
from app.models.content import ContentItem
from app.models.tag_embedding import TagEmbedding
from app.tasks.base import DatabaseTask, html_to_plain

logger = logging.getLogger(__name__)

_MAX_LABEL_WORDS = 6
_MAX_LABEL_CHARS = 100


# ---------------------------------------------------------------------------
# Public interface — call directly in tests with db=session
# ---------------------------------------------------------------------------


def generate_tags(content_item_id: str, db: Session | None = None) -> dict:
    """
    Extract semantic tags for a content item and store in item.tags.

    Pass 1 (free): embedding similarity to already-tagged articles.
    Pass 2 (LLM): gpt-4o-mini extraction if Pass 1 yields no results.

    After writing tags, upserts label embeddings to tag_embeddings.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        item = (
            db.query(ContentItem)
            .filter(ContentItem.id == UUID(content_item_id))
            .first()
        )
        if not item:
            logger.error(f"Content item {content_item_id} not found")
            return {"content_item_id": content_item_id, "status": "not_found"}

        if item.embedding is None:
            logger.warning(f"No embedding for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_embedding"}

        logger.info(f"Generating tags for {item.original_url}")

        # Always use LLM extraction for semantic labels.
        # The old embedding-similarity pass propagated coarse tags from similar
        # articles, which blocks fine-grained extraction even on the first run.
        tags = generate_tags_with_llm(
            item.title,
            item.description,
            item.full_text,
            existing_tags=item.tags or [],
        )

        if tags:
            item.tags = tags
            db.commit()
            logger.info(f"Tagged {item.original_url}: {tags}")
            upsert_tag_embeddings(tags, db=db)
            return {
                "content_item_id": content_item_id,
                "tags": tags,
                "status": "completed",
            }

        return {
            "content_item_id": content_item_id,
            "status": "completed",
            "message": "no tags generated",
        }

    except Exception as e:
        logger.error(f"Failed to generate tags for {content_item_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


def upsert_tag_embeddings(labels: list[str], db: Session | None = None) -> None:
    """
    Embed any labels not yet in tag_embeddings and upsert them.
    Labels already present are skipped (no redundant API calls).
    """
    if not labels:
        return

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        # Find which labels are already embedded
        existing = {
            row.label
            for row in db.query(TagEmbedding.label)
            .filter(TagEmbedding.label.in_(labels))
            .all()
        }
        new_labels = [lbl for lbl in labels if lbl not in existing]

        if not new_labels:
            return

        result = llm_client.embed(new_labels)

        for label, embedding in zip(new_labels, result.embeddings):
            row = TagEmbedding(label=label, embedding=embedding)
            db.merge(row)  # upsert on unique label

        db.commit()

    except Exception as e:
        logger.error(f"upsert_tag_embeddings failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        if own_session:
            db.close()


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def generate_tags_task(self, content_item_id: str):
    return generate_tags(content_item_id, db=self.db)


# ---------------------------------------------------------------------------
# Backfill — re-tag existing articles without semantic tags
# ---------------------------------------------------------------------------


@celery_app.task(base=DatabaseTask, bind=True)
def backfill_semantic_tags(self, user_id: str | None = None):
    """
    Re-run generate_tags for articles that have no tags yet.
    Writes only to tags (not auto_tags). Rate-limited to 50/min.
    """
    query = self.db.query(ContentItem).filter(
        ContentItem.deleted_at.is_(None),
        ContentItem.embedding.isnot(None),
        func.cardinality(ContentItem.tags) == 0,
    )
    if user_id:
        query = query.filter(ContentItem.user_id == UUID(user_id))

    items = query.all()
    logger.info(f"Backfilling {len(items)} articles")

    for i, item in enumerate(items):
        generate_tags_task.delay(str(item.id))
        if (i + 1) % 50 == 0:
            time.sleep(60)  # 50/min rate limit

    return {"backfilled": len(items)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_label(label: str) -> str | None:
    """Return cleaned label if valid, else None."""
    label = label.strip()[:_MAX_LABEL_CHARS]
    words = label.split()
    if not words or len(words) > _MAX_LABEL_WORDS:
        return None
    return label


def find_similar_tags_by_embedding(db: Session, item: ContentItem) -> list[str]:
    """Find similar already-tagged articles and suggest their tags (free path)."""
    threshold = 1.0 - settings.SIMILARITY_THRESHOLD_TAGS

    try:
        import numpy as np

        emb = item.embedding
        emb_list = emb.tolist() if isinstance(emb, np.ndarray) else list(emb)
        emb_str = str(emb_list)
    except Exception:
        return []

    rows = db.execute(
        sa_text(
            """
            SELECT id FROM content_items
            WHERE user_id = :user_id
              AND id != :item_id
              AND tags IS NOT NULL
              AND array_length(tags, 1) > 0
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
            "threshold": threshold,
        },
    ).fetchall()

    if not rows:
        return []

    ids = [r[0] for r in rows]
    similar_items = db.query(ContentItem).filter(ContentItem.id.in_(ids)).all()

    tag_counts: dict[str, int] = {}
    for si in similar_items:
        for tag in si.tags or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    return [tag for tag, count in sorted_tags[:5] if count >= 2]


def generate_tags_with_llm(
    title: str,
    description: str,
    full_text: str,
    existing_tags: list[str] | None = None,
) -> list[str]:
    """
    Extract semantic tags at two levels:
    - 1-2 domain labels: the field this belongs to (specific enough to cluster, broad enough to group)
    - 3-4 concept labels: precise ideas actually discussed in the article

    existing_tags are treated as the user's categorization context — preserved
    where meaningful, replaced where too generic.
    """
    parts = [p for p in [title, description] if p]
    if full_text:
        plain = html_to_plain(full_text)
        parts.append(" ".join(plain.split()[:800]))
    article_context = "\n\n".join(parts)

    existing_block = ""
    if existing_tags:
        existing_block = (
            f"\nThe user has already categorized this article with: {existing_tags}. "
            "Keep any that are meaningful category labels. Replace or extend with "
            "more specific concepts where those tags are too vague.\n"
        )

    prompt = f"""Tag this article for a personal reading library.

Generate tags at two levels:

DOMAIN (1-2 tags): The specific field or area this article belongs to.
  Examples across domains:
  - Tech: "distributed systems", "LLM fine-tuning", "iOS development"
  - Food: "fermented foods", "Japanese cuisine", "home baking"
  - Finance: "personal investing", "venture capital", "behavioral economics"
  - Politics: "electoral reform", "climate policy", "urban planning"
  - Health: "sleep science", "strength training", "mental health"
  - Arts: "documentary filmmaking", "literary fiction", "music theory"
  Specific enough to cluster related articles; broad enough to group more than one.

CONCEPTS (3-4 tags): Precise ideas the article actually discusses.
  Examples across domains:
  - Tech: "context window limits", "zero-shot prompting", "memory-mapped files"
  - Food: "maillard reaction", "sourdough starter", "umami flavor balance"
  - Finance: "loss aversion bias", "compound interest mechanics", "tax loss harvesting"
  - Politics: "ranked choice voting", "gerrymandering effects", "voter turnout drivers"
  - Health: "circadian rhythm disruption", "progressive overload", "gut microbiome diversity"
  - Arts: "unreliable narrator", "negative space composition", "leitmotif technique"
  Two articles sharing a concept tag should genuinely discuss the same idea.

Rules:
- 2-4 words per tag. No single-word tags ("AI", "Food", "Politics" say nothing).
- No ultra-narrow jargon that only appears in this one article.
- No generic filler: "Technology", "Business", "Opinion", "Culture".
- Match the domain of the article — don't force tech vocabulary onto non-tech content.
{existing_block}
Article:
{article_context}

Return JSON: {{"tags": ["domain1", "concept1", "concept2", ...]}} — 4-6 tags total."""

    try:
        tag_response = llm_client.structured_chat(
            messages=[{"role": "user", "content": prompt}],
            response_model=TagResponse,
            task=TASK_TAGGING,
            max_tokens=200,
        )
        validated = [_validate_label(str(t)) for t in tag_response.tags[:6]]
        return [t for t in validated if t]

    except Exception as e:
        logger.error(f"LLM tag extraction failed: {e}")
        return []


def get_user_tag_vocabulary(db: Session, user_id: UUID) -> list[str]:
    """All unique tags a user has confirmed (kept for future use)."""
    rows = (
        db.query(func.unnest(ContentItem.tags))
        .filter(ContentItem.user_id == user_id)
        .distinct()
        .all()
    )
    return [r[0] for r in rows if r[0]]
