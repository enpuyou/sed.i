"""
Entity deduplication task.

Finds near-duplicate entity nodes (same user, similar embedding) and merges
them — redirecting all mentions and relations from the loser to the winner.

Pipeline per run:
  1. Load all embedded entities for the user.
  2. pgvector similarity scan: find pairs with cosine sim >= threshold.
  3. LLM verification: confirm each candidate pair is truly the same entity.
  4. merge_entity(winner, loser) for confirmed pairs.

Cost: ~$0.001 per run at 1,500 entities. Scales linearly in entities.
Runtime: ~15s at 1,500 entities, ~30min at 120,000 (5,000 articles).

Dispatch: deduplicate_entities_task.delay(user_id)
Direct:   deduplicate_entities(user_id, db=session)
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.entity_graph import merge_entity
from app.core.llm_client import llm_client
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

# Cosine similarity floor for candidate pairs — mirrors LightRAG's 0.85 but
# slightly lower because our entity strings include type + description, making
# the embedding space richer and pairs embed slightly further apart than names alone.
_SIM_THRESHOLD = 0.82

# gpt-4o-mini verification prompt — single YES/NO question per candidate pair.
# Deliberately short: we only want to confirm identical real-world referents,
# not semantic relatives ("attention mechanism" vs "transformer" should stay separate).
_VERIFY_PROMPT = """\
Are the following two entity names referring to exactly the same real-world entity?
Answer YES or NO only. Do not explain.

Entity A: {a}
Entity B: {b}"""


def _verify_pair(name_a: str, name_b: str) -> bool:
    """Ask gpt-4o-mini whether two entity names are the same entity."""
    try:
        result = llm_client.chat(
            messages=[
                {"role": "user", "content": _VERIFY_PROMPT.format(a=name_a, b=name_b)}
            ],
            task="entity_dedup",
            max_tokens=5,
            temperature=0.0,
        )
        return result.content.strip().upper().startswith("YES")
    except Exception as e:
        logger.warning(
            f"Entity dedup LLM verification failed for ({name_a!r}, {name_b!r}): {e}"
        )
        return False


def deduplicate_entities(
    user_id: str,
    db: Session,
    sim_threshold: float = _SIM_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """
    Find and merge near-duplicate entity nodes for one user.

    Args:
        user_id: UUID string. Dedup is scoped per user — users never share entities.
        db: Database session.
        sim_threshold: Cosine similarity floor for candidate pairs (default 0.82).
        dry_run: If True, find and verify candidates but do not merge. Returns
                 the candidate list for inspection.

    Returns:
        {"candidates": N, "merged": M, "skipped": S, "status": "completed"}
    """
    uid = str(user_id)

    # Find all candidate pairs: entities from the same user where cosine sim >= threshold.
    # Self-pairs excluded (a.id < b.id enforces each pair once).
    # Only entities with embeddings participate — unembedded entities stay untouched.
    rows = db.execute(
        text(
            """
        SELECT
            a.id   AS id_a,
            a.name AS name_a,
            b.id   AS id_b,
            b.name AS name_b,
            1 - (a.embedding <=> b.embedding) AS sim
        FROM entities a
        JOIN entities b ON a.id < b.id
        WHERE a.user_id = CAST(:uid AS uuid)
          AND b.user_id = CAST(:uid AS uuid)
          AND a.embedding IS NOT NULL
          AND b.embedding IS NOT NULL
          AND 1 - (a.embedding <=> b.embedding) >= :thresh
        ORDER BY sim DESC
    """
        ),
        {"uid": uid, "thresh": sim_threshold},
    ).fetchall()

    candidates = len(rows)
    merged = 0
    skipped = 0

    # Track which entity IDs have already been merged in this run — if entity A
    # was merged into B, don't try to merge A into C separately.
    already_merged: set[str] = set()

    for row in rows:
        id_a, id_b = str(row.id_a), str(row.id_b)

        if id_a in already_merged or id_b in already_merged:
            skipped += 1
            continue

        logger.debug(
            f"Candidate pair (sim={row.sim:.3f}): {row.name_a!r} vs {row.name_b!r}"
        )

        if not _verify_pair(row.name_a, row.name_b):
            skipped += 1
            continue

        if dry_run:
            logger.info(
                f"[dry-run] Would merge {row.name_b!r} → {row.name_a!r} (sim={row.sim:.3f})"
            )
            merged += 1
            already_merged.add(id_b)
            continue

        try:
            # Winner = id_a (lower UUID string = older entity, first seen)
            merge_entity(uuid.UUID(id_a), uuid.UUID(id_b), db)
            db.commit()
            logger.info(f"Merged {row.name_b!r} → {row.name_a!r} (sim={row.sim:.3f})")
            merged += 1
            already_merged.add(id_b)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to merge {row.name_b!r} → {row.name_a!r}: {e}")
            skipped += 1

    logger.info(
        f"Entity dedup for user {uid}: "
        f"{candidates} candidates, {merged} merged, {skipped} skipped"
        + (" [dry-run]" if dry_run else "")
    )
    return {
        "candidates": candidates,
        "merged": merged,
        "skipped": skipped,
        "status": "completed",
        "dry_run": dry_run,
    }


@celery_app.task(base=DatabaseTask, bind=True, max_retries=2)
def deduplicate_entities_task(
    self,
    user_id: str | None = None,
    sim_threshold: float = _SIM_THRESHOLD,
    dry_run: bool = False,
):
    """
    Deduplicate entity nodes for one user or all users.

    user_id=None runs dedup for every user that has entity embeddings.
    Called by the weekly Celery beat schedule.
    """
    if user_id is not None:
        return deduplicate_entities(
            user_id=user_id,
            db=self.db,
            sim_threshold=sim_threshold,
            dry_run=dry_run,
        )

    # All users with at least one embedded entity
    rows = self.db.execute(
        text(
            """
        SELECT DISTINCT user_id FROM entities WHERE embedding IS NOT NULL
    """
        )
    ).fetchall()

    totals = {"candidates": 0, "merged": 0, "skipped": 0}
    for row in rows:
        result = deduplicate_entities(
            user_id=str(row.user_id),
            db=self.db,
            sim_threshold=sim_threshold,
            dry_run=dry_run,
        )
        for k in totals:
            totals[k] += result.get(k, 0)

    return {**totals, "users": len(rows), "status": "completed", "dry_run": dry_run}
