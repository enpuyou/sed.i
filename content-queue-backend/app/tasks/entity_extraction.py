"""
Celery task: extract_entities.

Extracts named entities and relations from article text using an LLM,
then writes them to the entity graph tables (entities, entity_mentions,
entity_relations). Runs after generate_chunk_embeddings in the ingestion
pipeline.

Entity extraction is idempotent: re-running on the same article upserts
rather than duplicating. Entity names are case-insensitively deduplicated
per user across all articles.

Dispatch: extract_entities_task.delay(content_item_id)
Direct call (tests): extract_entities(content_item_id, db=session)
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.entity_graph import upsert_entity, upsert_mention
from app.core.llm_client import llm_client, TASK_ENTITY_EXTRACTION
from app.core.llm_schemas import EntityExtractionResponse
from app.models.content import ContentItem
from app.models.entity import EntityRelation
from app.tasks.base import DatabaseTask, html_to_plain

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract the key named entities and relationships from this article for a \
personal knowledge graph. Focus on entities that would be useful for \
connecting this article to related reading.

Entity types:
- PERSON: real people (authors, researchers, public figures)
- CONCEPT: abstract ideas, techniques, theories
- ORGANIZATION: companies, institutions, research labs
- PAPER: academic papers or books referenced by title
- TOOL: software, frameworks, platforms

Relation types (source → target):
- DEVELOPED: person/org created a concept/tool/paper
- INTRODUCES: paper/article introduces a concept
- USES: concept/tool uses another concept/tool
- EXTENDS: builds on or extends another concept
- CITES: paper cites another paper
- CONTRADICTS: concept contradicts another
- ENABLES: concept enables or underpins another

Rules:
- Extract 3-8 entities. Prefer named, specific entities over generic ones.
- Extract 2-5 relations. Only include high-confidence connections stated in the text.
- Entity names must match exactly between entities list and relations source/target.

Article title: {title}

Article text:
{text}

Return JSON with "entities" (list of {{name, type, description}}) and \
"relations" (list of {{source, target, predicate, description}})."""


def extract_entities_with_llm(title: str, text: str) -> EntityExtractionResponse:
    """Call LLM to extract entities and relations. Separated for test patching."""
    prompt = _EXTRACTION_PROMPT.format(title=title, text=text[:3000])
    return llm_client.structured_chat(
        messages=[{"role": "user", "content": prompt}],
        response_model=EntityExtractionResponse,
        task=TASK_ENTITY_EXTRACTION,
        max_tokens=800,
    )


def extract_entities(content_item_id: str, db: Session | None = None) -> dict:
    """
    Extract entities and relations from an article and write to the entity graph.

    Returns a dict with status, entities_written, relations_written.
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
        if not text.strip():
            logger.warning(f"No text to extract entities from for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_text"}

        logger.info(f"Extracting entities for {item.original_url}")

        result = extract_entities_with_llm(item.title or "", text)

        # Upsert entities and their mentions
        entity_map: dict[str, object] = {}  # name → Entity row
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
                context_text=e.description,
                db=db,
            )
            entity_map[e.name.lower().strip()] = entity
            entities_written += 1

        # Insert relations (skip if either entity wasn't extracted)
        relations_written = 0
        for r in result.relations:
            source = entity_map.get(r.source.lower().strip())
            target = entity_map.get(r.target.lower().strip())
            if source is None or target is None:
                logger.debug(
                    f"Skipping relation {r.source!r}→{r.target!r}: entity not found"
                )
                continue
            predicate = r.predicate or ""
            rel = EntityRelation(
                source_entity_id=source.id,
                target_entity_id=target.id,
                relation_type=predicate,
                description=r.description,
                content_item_id=item.id,
            )
            db.add(rel)
            relations_written += 1

        db.commit()
        logger.info(
            f"Extracted {entities_written} entities, {relations_written} relations "
            f"for {item.original_url}"
        )
        return {
            "content_item_id": content_item_id,
            "status": "completed",
            "entities_written": entities_written,
            "relations_written": relations_written,
        }

    except Exception as e:
        logger.error(f"Failed to extract entities for {content_item_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}
    finally:
        if own_session:
            db.close()


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def extract_entities_task(self, content_item_id: str):
    return extract_entities(content_item_id, db=self.db)
