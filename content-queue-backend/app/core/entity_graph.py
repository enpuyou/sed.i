"""
Entity graph access layer — thin wrapper over the three entity tables.

All graph queries live here so that callers (tasks, search, MCP tools)
never issue raw SQL against entity tables directly. If we ever swap
Postgres for a graph DB, only this module changes.

Public interface:
    upsert_entity()           — insert or return existing entity (case-insensitive)
    get_article_entities()    — entity ids mentioned in an article
    get_entity_neighbors()    — entity ids reachable via EntityRelation in N hops
    articles_for_entities()   — article ids that mention any of the given entities
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entity import Entity, EntityMention


def upsert_entity(
    user_id: uuid.UUID,
    name: str,
    entity_type: str,
    description: str,
    db: Session,
) -> Entity:
    """
    Return existing Entity for (user_id, lower(name)), or create a new one.

    Case-insensitive: "Backpropagation" and "backpropagation" are the same entity.
    The canonical stored name is the first form seen.
    """

    def _query_existing() -> Entity | None:
        return (
            db.query(Entity)
            .filter(
                Entity.user_id == user_id,
                func.lower(Entity.name) == name.lower().strip(),
            )
            .first()
        )

    existing = _query_existing()
    if existing:
        return existing

    entity = Entity(
        user_id=user_id,
        name=name.strip(),
        entity_type=entity_type,
        description=description,
    )
    db.add(entity)
    try:
        with (
            db.begin_nested()
        ):  # SAVEPOINT — only rolls back this insert, not the outer tx
            db.flush()
    except IntegrityError:
        # Concurrent worker inserted the same entity between our SELECT and INSERT.
        existing = _query_existing()
        if existing:
            return existing
        raise  # genuine unexpected constraint violation
    return entity


def upsert_mention(
    entity_id: uuid.UUID,
    content_item_id: uuid.UUID,
    user_id: uuid.UUID,
    context_text: str,
    db: Session,
) -> EntityMention:
    """
    Return existing mention for (entity_id, content_item_id) or create one.
    Idempotent: re-running extract_entities on the same article is safe.
    """
    existing = (
        db.query(EntityMention)
        .filter(
            EntityMention.entity_id == entity_id,
            EntityMention.content_item_id == content_item_id,
        )
        .first()
    )
    if existing:
        return existing

    mention = EntityMention(
        entity_id=entity_id,
        content_item_id=content_item_id,
        user_id=user_id,
        context_text=context_text,
    )
    db.add(mention)
    db.flush()
    return mention


def get_article_entities(content_item_id: uuid.UUID, db: Session) -> list[uuid.UUID]:
    """Return entity ids for all entities mentioned in a given article."""
    rows = (
        db.query(EntityMention.entity_id)
        .filter(EntityMention.content_item_id == content_item_id)
        .all()
    )
    return [r.entity_id for r in rows]


def get_entity_neighbors(
    entity_ids: Sequence[uuid.UUID],
    db: Session,
    hops: int = 1,
) -> list[uuid.UUID]:
    """
    Return entity ids reachable from seed entity_ids via EntityRelation in N hops.

    hops=1: direct neighbors only (entities connected by one relation).
    hops=2: neighbors of neighbors included.

    Uses a recursive CTE for arbitrary hop depth without loading the full graph.
    Seeds are excluded from results.
    """
    if not entity_ids:
        return []

    seed_ids = [str(eid) for eid in entity_ids]

    # Recursive CTE: traverse entity_relations up to `hops` levels deep
    sql = text(
        """
        WITH RECURSIVE neighbors(entity_id, depth) AS (
            -- Base: 1-hop neighbors from seeds
            SELECT DISTINCT
                CASE
                    WHEN er.source_entity_id = ANY(CAST(:seeds AS uuid[]))
                        THEN er.target_entity_id
                    ELSE er.source_entity_id
                END AS entity_id,
                1 AS depth
            FROM entity_relations er
            WHERE er.source_entity_id = ANY(CAST(:seeds AS uuid[]))
               OR er.target_entity_id = ANY(CAST(:seeds AS uuid[]))

            UNION

            -- Recursive: extend from current frontier
            SELECT DISTINCT
                CASE
                    WHEN er.source_entity_id = n.entity_id
                        THEN er.target_entity_id
                    ELSE er.source_entity_id
                END,
                n.depth + 1
            FROM entity_relations er
            JOIN neighbors n ON (
                er.source_entity_id = n.entity_id OR
                er.target_entity_id = n.entity_id
            )
            WHERE n.depth < :max_hops
        )
        SELECT DISTINCT entity_id FROM neighbors
        WHERE entity_id != ALL(CAST(:seeds AS uuid[]))
    """
    )

    rows = db.execute(sql, {"seeds": seed_ids, "max_hops": hops}).fetchall()
    return [r.entity_id for r in rows]


def merge_entity(
    winner_id: uuid.UUID,
    loser_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Merge loser entity into winner atomically.

    Redirects all mentions and relations from loser to winner, then deletes
    the loser. Conflicts (winner already has the same edge) are dropped rather
    than duplicated — the unique constraints on both tables enforce this.

    Does NOT commit — caller is responsible for commit/rollback.
    """
    w, loser = str(winner_id), str(loser_id)

    # Mentions: redirect non-conflicting rows, delete the rest
    db.execute(
        text(
            """
        UPDATE entity_mentions SET entity_id = :w
        WHERE entity_id = :l
          AND content_item_id NOT IN (
              SELECT content_item_id FROM entity_mentions WHERE entity_id = :w
          )
    """
        ),
        {"w": w, "l": loser},
    )
    db.execute(text("DELETE FROM entity_mentions WHERE entity_id = :l"), {"l": loser})

    # Relations — source side: redirect non-conflicting, delete the rest
    db.execute(
        text(
            """
        UPDATE entity_relations SET source_entity_id = :w
        WHERE source_entity_id = :l
          AND NOT EXISTS (
              SELECT 1 FROM entity_relations r2
              WHERE r2.source_entity_id = :w
                AND r2.target_entity_id = entity_relations.target_entity_id
                AND r2.relation_type = entity_relations.relation_type
                AND r2.content_item_id IS NOT DISTINCT FROM entity_relations.content_item_id
          )
    """
        ),
        {"w": w, "l": loser},
    )
    db.execute(
        text("DELETE FROM entity_relations WHERE source_entity_id = :l"), {"l": loser}
    )

    # Relations — target side: redirect non-conflicting, delete the rest
    db.execute(
        text(
            """
        UPDATE entity_relations SET target_entity_id = :w
        WHERE target_entity_id = :l
          AND NOT EXISTS (
              SELECT 1 FROM entity_relations r2
              WHERE r2.target_entity_id = :w
                AND r2.source_entity_id = entity_relations.source_entity_id
                AND r2.relation_type = entity_relations.relation_type
                AND r2.content_item_id IS NOT DISTINCT FROM entity_relations.content_item_id
          )
    """
        ),
        {"w": w, "l": loser},
    )
    db.execute(
        text("DELETE FROM entity_relations WHERE target_entity_id = :l"), {"l": loser}
    )

    # Delete the loser
    db.execute(text("DELETE FROM entities WHERE id = :l"), {"l": loser})


def articles_for_entities(
    entity_ids: Sequence[uuid.UUID],
    db: Session,
    exclude_item_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """
    Return article (content_item) ids that mention any of the given entities.

    Optionally excludes a source article (e.g. when finding related articles
    for a given article's entity neighborhood).
    """
    if not entity_ids:
        return []

    query = (
        db.query(EntityMention.content_item_id)
        .filter(EntityMention.entity_id.in_(entity_ids))
        .distinct()
    )
    if exclude_item_id is not None:
        query = query.filter(EntityMention.content_item_id != exclude_item_id)

    rows = query.all()
    return [r.content_item_id for r in rows]
