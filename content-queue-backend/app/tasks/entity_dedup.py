"""
Entity deduplication task.

Finds near-duplicate entity nodes (same user, similar embedding) and merges
them — redirecting all mentions and relations from the loser to the winner.

Pipeline per run:
  1. For each embedded entity, use HNSW ANN to find its top-K nearest neighbors
     above the similarity threshold. This is O(N × K × log N) instead of the
     previous O(N²) self-join.
  2. Deduplicate the resulting candidate pairs (each pair is found twice via
     the symmetric ANN lookup; we normalise by always putting the lower UUID first).
  3. LLM verification: confirm each candidate pair is truly the same entity.
  4. merge_entity(winner, loser) for confirmed pairs.

Cost: ~$0.001 per run at 1,500 entities. Scales as O(N log N) in entities.
Runtime: ~2s at 1,500 entities (vs ~15s for the old O(N²) self-join).

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

# Maximum ANN neighbors to retrieve per entity. At K=20 and N=2,000 entities,
# this yields at most 40,000 pair comparisons before dedup — vs 2M for O(N²).
_ANN_K = 20

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


def _find_candidate_pairs(
    user_id: str, db: Session, sim_threshold: float
) -> list[tuple[str, str, str, str, float]]:
    """
    Return (id_a, name_a, id_b, name_b, sim) pairs above sim_threshold for one user.

    Uses pgvector ANN (HNSW index on entities.embedding) to find each entity's
    top-K nearest neighbors, then filters to those above the threshold. Each pair
    is normalised to (lower_uuid, higher_uuid) order so duplicates from the
    symmetric lookup are collapsed.

    Falls back gracefully when the HNSW index doesn't exist yet (sequential scan).
    """
    # Load all embedded entity ids + names for this user in one query.
    seed_rows = db.execute(
        text(
            """
            SELECT id, name
            FROM entities
            WHERE user_id = CAST(:uid AS uuid)
              AND embedding IS NOT NULL
            ORDER BY id
            """
        ),
        {"uid": user_id},
    ).fetchall()

    if not seed_rows:
        return []

    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, str, str, float]] = []

    for seed in seed_rows:
        sid = str(seed.id)
        # ANN query: for this entity, find its K nearest neighbors among the
        # same user's entities, excluding itself. The HNSW index makes this
        # O(log N) per entity instead of O(N).
        neighbor_rows = db.execute(
            text(
                """
                SELECT b.id   AS id_b,
                       b.name AS name_b,
                       1 - (a.embedding <=> b.embedding) AS sim
                FROM entities a
                JOIN entities b
                  ON b.user_id = CAST(:uid AS uuid)
                 AND b.id != a.id
                 AND b.embedding IS NOT NULL
                WHERE a.id = CAST(:seed_id AS uuid)
                  AND 1 - (a.embedding <=> b.embedding) >= :thresh
                ORDER BY a.embedding <=> b.embedding
                LIMIT :k
                """
            ),
            {
                "uid": user_id,
                "seed_id": sid,
                "thresh": sim_threshold,
                "k": _ANN_K,
            },
        ).fetchall()

        for nb in neighbor_rows:
            nid = str(nb.id_b)
            # Normalise pair order so (A,B) and (B,A) map to the same key
            pair_key = (min(sid, nid), max(sid, nid))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            id_a, id_b = pair_key
            name_a = seed.name if sid == id_a else nb.name_b
            name_b = nb.name_b if sid == id_a else seed.name
            candidates.append((id_a, name_a, id_b, name_b, float(nb.sim)))

    # Sort highest-sim first so most-likely duplicates are processed first
    candidates.sort(key=lambda r: r[4], reverse=True)
    return candidates


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
        dry_run: If True, find and verify candidates but do not merge.

    Returns:
        {"candidates": N, "merged": M, "skipped": S, "status": "completed"}
    """
    uid = str(user_id)
    rows = _find_candidate_pairs(uid, db, sim_threshold)

    candidates = len(rows)
    merged = 0
    skipped = 0

    # Track which entity IDs have already been merged in this run — if entity A
    # was merged into B, don't try to merge A into C separately.
    already_merged: set[str] = set()

    for id_a, name_a, id_b, name_b, sim in rows:
        if id_a in already_merged or id_b in already_merged:
            skipped += 1
            continue

        logger.debug(f"Candidate pair (sim={sim:.3f}): {name_a!r} vs {name_b!r}")

        if not _verify_pair(name_a, name_b):
            skipped += 1
            continue

        if dry_run:
            logger.info(
                f"[dry-run] Would merge {name_b!r} → {name_a!r} (sim={sim:.3f})"
            )
            merged += 1
            already_merged.add(id_b)
            continue

        try:
            # Winner = id_a (lower UUID string — deterministic, not creation-order)
            merge_entity(uuid.UUID(id_a), uuid.UUID(id_b), db)
            db.commit()
            logger.info(f"Merged {name_b!r} → {name_a!r} (sim={sim:.3f})")
            merged += 1
            already_merged.add(id_b)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to merge {name_b!r} → {name_a!r}: {e}")
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

    rows = self.db.execute(
        text("SELECT DISTINCT user_id FROM entities WHERE embedding IS NOT NULL")
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
