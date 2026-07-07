"""
Celery task: analyze_article_task.

Single LLM call that replaces the separate generate_tags_task and
extract_entities_task. Returns domain tags, concept tags, named entities,
and entity relations in one gpt-4o-mini round-trip.

Writes:
  - content_items.tags        (domain_tags + concept_tags concatenated)
  - tag_embeddings            (with tag_type='domain' or 'concept')
  - entities / entity_mentions / entity_relations

Idempotent: re-running on the same article upserts rather than duplicating.

Dispatch: analyze_article_task.delay(content_item_id)
Direct call (tests): analyze_article(content_item_id, db=session)
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.entity_graph import upsert_entity, upsert_mention
from app.core.llm_client import llm_client, TASK_ARTICLE_ANALYSIS
from app.core.llm_schemas import ArticleAnalysisResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.content import ContentItem
from app.models.entity import EntityRelation
from app.models.tag_embedding import TagEmbedding
from app.tasks.base import DatabaseTask, html_to_plain

logger = logging.getLogger(__name__)

_MAX_LABEL_WORDS = 6
_MAX_LABEL_CHARS = 100
_MAX_TEXT_WORDS = 2500

_ANALYSIS_PROMPT = """\
Analyze this article for a personal reading library.

TASK 1 — TAGGING
Generate tags at two levels:
  DOMAIN (1-2 tags): The specific field or area this article belongs to.
    Specific enough to cluster related articles; broad enough to group more than one.
    Examples: "distributed systems", "sleep science", "venture capital", "documentary filmmaking"
  CONCEPTS (3-4 tags): Precise ideas the article actually discusses.
    Two articles sharing a concept tag should genuinely discuss the same idea.
    Examples: "context window limits", "circadian rhythm disruption", "loss aversion bias"
  Rules: 2-4 words per tag. No single-word tags. No generic filler ("Technology", "Business").

TASK 2 — ENTITY AND RELATION EXTRACTION
Extract named entities and the relationships between them.

  Entity types: PERSON | CONCEPT | ORGANIZATION | PAPER | TOOL
  Entity rules:
    - Extract 3-8 entities total. Choose entities that are CENTRAL to the article's argument.
    - Priority order for what to extract:
        1. CONCEPT entities: named ideas, frameworks, phenomena, or cognitive patterns that
           the article analyzes or builds an argument around. These are the most valuable.
           Good: "availability heuristic", "context anxiety", "reverse centaur",
                 "algorithmic recommendation fatigue", "optimisation culture"
           Bad: generic labels like "AI", "technology", "efficiency"
        2. TOOL entities: specific software products actively discussed (not just mentioned
           as passing examples).
           Good: "ChatGPT" (if the article analyzes its behavior)
           Bad: "Do Not Disturb" (iOS feature used as a throwaway example)
        3. PAPER/ORGANIZATION/PERSON: only when they originate, define, or exemplify a
           key idea in the article. Skip quote sources, passing examples, and article bylines.
    - Skip incidental details used only as illustrations:
        Bad: a city name in "he moved to New York City"; a venue used as one example
        Bad: an organization mentioned only as the source of a quote
        Bad: a person quoted once without contributing an idea to the article
    - Full canonical name only — not abbreviations. "New York Times" not "NYT".
    - mention_context: copy one verbatim sentence where this entity's role is clearest.
    - description: 1 sentence explaining WHY this entity matters to this article's argument.
      Do not leave description empty.

  Relation rules:
    - A relation connects two CONCEPT/TOOL/PAPER entities from your list — not people to orgs.
    - Only extract when the article explicitly states the connection (cause, contrast,
      inspiration, evolution, mechanism). Two entities in the same article is NOT a relation.
    - The most valuable relations for a reading library:
        One idea is a form of / instance of another idea
        One idea caused / enabled / is a response to another idea
        One framework explains / predicts / contradicts another
        A tool embodies / applies / exemplifies a concept
    - For each relation write:
        predicate: 3-8 words completing "<source> ___ <target>"
        strength: 1-5 integer (5 = explicitly stated, 3 = strongly implied, 1 = weak inference)
        description: verbatim sentence(s) from article supporting this relation
    - Extract 0-4 relations. Zero is correct when no clear connection is stated.
    - Prefer strength ≥ 3. Do not extract strength 1 relations.

  Predicate examples (good — specific, grounded):
    "is a cognitive mechanism for"       → availability heuristic / LLM reliability overestimation
    "exemplifies the problem of"         → slot machine / LLM coding assistant experience
    "inspired the architecture of"       → GAN design / multi-agent planner-generator-evaluator
    "created demand for"                 → algorithmic recommendations / human curation
    "directly shaped the development of" → McLuhan / Californian Ideology

  Predicate examples (bad — do not extract):
    "said about"                    → quote attribution, not a relation
    "addressed correspondence to"   → administrative contact info
    "is mentioned alongside"        → co-mention, not a relation
    "works at"                      → person–org affiliation, not an idea connection

Article title: {title}

Article text:
{text}

Return JSON:
{{
  "domain_tags": ["...", "..."],
  "concept_tags": ["...", "...", "..."],
  "entities": [
    {{"name": "...", "type": "...", "description": "one sentence on why this entity matters to the argument", "mention_context": "verbatim sentence"}}
  ],
  "relations": [
    {{"source": "...", "predicate": "3-8 words", "target": "...", "strength": 1-5, "description": "verbatim or near-verbatim sentence from article"}}
  ]
}}"""


def _validate_label(label: str) -> str | None:
    label = label.strip()[:_MAX_LABEL_CHARS]
    words = label.split()
    if not words or len(words) > _MAX_LABEL_WORDS:
        return None
    return label


def analyze_article_with_llm(title: str, text: str) -> ArticleAnalysisResponse:
    """Single-pass extraction: tags, entities, and grounded relations together."""
    words = text.split()
    excerpt = " ".join(words[:_MAX_TEXT_WORDS])
    return llm_client.structured_chat(
        messages=[
            {
                "role": "user",
                "content": _ANALYSIS_PROMPT.format(title=title, text=excerpt),
            }
        ],
        response_model=ArticleAnalysisResponse,
        task=TASK_ARTICLE_ANALYSIS,
        max_tokens=1000,
    )


def _upsert_tag_embeddings(labels: list[str], tag_type: str, db: Session) -> None:
    """Embed new labels and upsert into tag_embeddings with their type."""
    if not labels:
        return

    existing = {
        row.label
        for row in db.query(TagEmbedding.label)
        .filter(TagEmbedding.label.in_(labels))
        .all()
    }
    new_labels = [lbl for lbl in labels if lbl not in existing]

    if new_labels:
        result = llm_client.embed(new_labels)
        for label, embedding in zip(new_labels, result.embeddings):
            db.merge(TagEmbedding(label=label, embedding=embedding, tag_type=tag_type))

    # Update tag_type for labels that exist but have no type yet (legacy rows)
    legacy = (
        db.query(TagEmbedding)
        .filter(
            TagEmbedding.label.in_(labels),
            TagEmbedding.tag_type.is_(None),
        )
        .all()
    )
    for row in legacy:
        row.tag_type = tag_type


def analyze_article(
    content_item_id: str,
    db: Session | None = None,
    skip_tags: bool = False,
) -> dict:
    """
    Run combined tag + entity extraction for one article.

    Writes tags to content_items.tags, tag type to tag_embeddings,
    entities/mentions/relations to the entity graph tables.

    Args:
        skip_tags: When True, skip tag generation and do not overwrite
                   content_items.tags. Used by backfill for articles that
                   were already tagged by the previous pipeline.
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

        text = ""
        if item.full_text:
            text = html_to_plain(item.full_text)

        # Fall back to title + description when full_text is absent
        if not text.strip():
            parts = [p for p in [item.title, item.description] if p]
            text = " ".join(parts)

        if not text.strip():
            logger.warning(f"No text for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_text"}

        logger.info(f"Analyzing article {item.original_url} (skip_tags={skip_tags})")

        result = analyze_article_with_llm(item.title or "", text)

        # ── Tags ─────────────────────────────────────────────────────────────
        all_tags: list[str] = []
        concept_tags: list[str] = []
        if not skip_tags:
            domain_tags = [
                t for t in (_validate_label(t) for t in result.domain_tags[:2]) if t
            ]
            concept_tags = [
                t for t in (_validate_label(t) for t in result.concept_tags[:4]) if t
            ]
            all_tags = domain_tags + concept_tags
            if all_tags:
                item.tags = all_tags
                _upsert_tag_embeddings(domain_tags, "domain", db)
                _upsert_tag_embeddings(concept_tags, "concept", db)

        # ── Entities + mentions ───────────────────────────────────────────────
        entity_map: dict[str, object] = {}
        entities_written = 0
        for e in result.entities:
            entity = upsert_entity(
                user_id=item.user_id,
                name=e.name,
                entity_type=e.type,
                description=e.description,
                db=db,
            )
            upsert_mention(
                entity_id=entity.id,
                content_item_id=item.id,
                user_id=item.user_id,
                context_text=e.mention_context or e.description,
                db=db,
            )
            entity_map[e.name.lower().strip()] = entity
            entities_written += 1

        # Promote concept tags to CONCEPT entity nodes for tags not already covered
        # by an extracted entity. This gives every article searchable concept nodes
        # even when the extraction prompt used different names for the same idea.
        if concept_tags:
            for tag in concept_tags:
                tag_key = tag.lower().strip()
                if tag_key in entity_map:
                    continue
                entity = upsert_entity(
                    user_id=item.user_id,
                    name=tag,
                    entity_type="CONCEPT",
                    description="",
                    db=db,
                )
                upsert_mention(
                    entity_id=entity.id,
                    content_item_id=item.id,
                    user_id=item.user_id,
                    context_text="",
                    db=db,
                )
                entity_map[tag_key] = entity
                entities_written += 1

        # ── Relations ────────────────────────────────────────────────────────
        relations_written = 0
        for r in result.relations:
            source = entity_map.get(r.source.lower().strip())
            target = entity_map.get(r.target.lower().strip())
            if source is None or target is None:
                logger.debug(
                    f"Skipping relation {r.source!r}→{r.target!r}: entity not found"
                )
                continue
            predicate = r.predicate.strip()[:120] if r.predicate else ""
            if not predicate:
                continue
            strength = max(1, min(5, int(r.strength))) if r.strength else 3
            stmt = (
                pg_insert(EntityRelation.__table__)
                .values(
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type=predicate,
                    description=r.description,
                    weight=strength
                    / 5.0,  # normalise to [0.2, 1.0] for PPR edge weighting
                    content_item_id=item.id,
                )
                .on_conflict_do_nothing(
                    constraint="uq_entity_relation_source_target_type_article"
                )
            )
            db.execute(stmt)
            relations_written += 1

        db.commit()
        logger.info(
            f"Analyzed {item.original_url}: {len(all_tags)} tags, "
            f"{entities_written} entities, {relations_written} relations"
        )

        # Embed any new entity nodes asynchronously (no-op if already embedded)
        if entities_written > 0:
            try:
                from app.tasks.entity_embedding import embed_new_entities_task

                embed_new_entities_task.delay(str(item.user_id))
            except Exception as broker_err:
                # Broker unavailable (e.g. no Redis in test env) — not fatal.
                logger.warning(
                    f"Could not enqueue embed_new_entities_task: {broker_err}"
                )

        return {
            "content_item_id": content_item_id,
            "status": "completed",
            "tags": all_tags,
            "entities_written": entities_written,
            "relations_written": relations_written,
        }

    except Exception as e:
        logger.error(f"Failed to analyze article {content_item_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def analyze_article_task(self, content_item_id: str, skip_tags: bool = False):
    return analyze_article(content_item_id, db=self.db, skip_tags=skip_tags)


@celery_app.task(base=DatabaseTask, bind=True)
def backfill_entity_extraction(self, user_id: str | None = None, batch_size: int = 50):
    """
    Dispatch analyze_article_task for articles that have no entity mentions yet.

    Targets articles with full_text and an embedding (fully ingested) that have
    never been through entity extraction. Safe to re-run: articles that already
    have entity mentions are skipped.

    Args:
        user_id: Limit to a specific user. None = all users.
        batch_size: Max articles to dispatch per invocation (default 50).
                    Call repeatedly until dispatched=0 to process everything.

    Returns:
        {"dispatched": N, "status": "completed"}
    """
    from sqlalchemy import text as sa_text

    uid_filter = "AND ci.user_id = CAST(:uid AS uuid)" if user_id else ""
    params: dict = {"lim": batch_size}
    if user_id:
        params["uid"] = user_id

    rows = self.db.execute(
        sa_text(
            f"""
            SELECT ci.id,
                   (ci.tags IS NOT NULL AND array_length(ci.tags, 1) > 0) AS has_tags
            FROM content_items ci
            WHERE ci.embedding IS NOT NULL
              AND ci.full_text IS NOT NULL
              AND ci.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM entity_mentions em
                  WHERE em.content_item_id = ci.id
              )
              {uid_filter}
            ORDER BY ci.created_at DESC
            LIMIT :lim
        """
        ),
        params,
    ).fetchall()

    for row in rows:
        # Articles already tagged by the previous pipeline: only extract entities,
        # don't overwrite their tags. New articles: full analysis.
        analyze_article_task.apply_async(
            args=[str(row.id)],
            kwargs={"skip_tags": bool(row.has_tags)},
        )

    logger.info(f"Backfill dispatched {len(rows)} articles for entity extraction")
    return {"dispatched": len(rows), "status": "completed"}
