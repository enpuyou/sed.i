"""
Unified search entry point.

Classifies a query via search_router and routes to keyword (tsvector), filter
(SQL WHERE), semantic (pgvector), or RRF-fused hybrid. Returns [] on any failure
— never raises. Does NOT embed queries itself; delegates to OpenAI/Bedrock.
"""

from __future__ import annotations

import math as _math
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.content import ContentItem
from app.models.user import User

_DATE_OPERATOR_RE = re.compile(r"\b(after|before):\d{4}-\d{2}-\d{2}", re.IGNORECASE)


def hydrate_items(
    rows: list,
    db: Session,
    *,
    scores: dict[str, float] | None = None,
    include_full_text: bool = False,
) -> list[dict]:
    """
    Bulk-load ContentItems for a list of DB rows that have an .id attribute.

    Fetches all items in a single query (no N+1), then applies _format_item.
    Optionally merges a scores dict keyed by str(id).

    Args:
        rows: Raw SQL rows with an .id attribute (UUID or str).
        db: Database session.
        scores: Optional {str(id): float} map; merged as 'score' on each result.
        include_full_text: Passed through to _format_item.

    Returns:
        List of item dicts in the same order as rows, skipping missing items.
    """
    from app.mcp.tools.content import _format_item

    row_ids = [row.id for row in rows]
    if not row_ids:
        return []

    items_by_id = {
        str(item.id): item
        for item in db.query(ContentItem).filter(ContentItem.id.in_(row_ids)).all()
    }

    results = []
    for row in rows:
        item = items_by_id.get(str(row.id))
        if not item:
            continue
        d = _format_item(item, include_full_text=include_full_text)
        if scores is not None:
            d["score"] = float(scores.get(str(row.id), 0.0))
        results.append(d)
    return results


def _strip_date_operators(query: str) -> str:
    """Remove after:/before: operators from a query string."""
    return _DATE_OPERATOR_RE.sub("", query).strip()


def _apply_date_filter(
    results: list[dict], after: str | None, before: str | None
) -> list[dict]:
    """Post-filter results by created_at using after/before date strings (YYYY-MM-DD)."""
    if not after and not before:
        return results
    after_dt = (
        datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if after
        else None
    )
    before_dt = (
        datetime.strptime(before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if before
        else None
    )

    filtered = []
    for r in results:
        raw = r.get("created_at")
        if not raw:
            continue
        try:
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
        if after_dt and created < after_dt:
            continue
        if before_dt and created >= before_dt:
            continue
        filtered.append(r)
    return filtered


# ---------------------------------------------------------------------------
# Keyword search (tsvector)
# ---------------------------------------------------------------------------


def keyword_search(
    *,
    query: str,
    user: User,
    db: Session,
    limit: int = 10,
) -> list[dict]:
    """
    Full-text keyword search using PostgreSQL tsvector + ts_rank_cd.

    No OpenAI API call. Uses the search_vector column (maintained by trigger)
    with weighted fields: title/author = A (higher), description/tags = B.

    Args:
        query: Raw search string. Supports websearch syntax: OR, NOT, "phrase".
        user: Authenticated user — results scoped to their library.
        db: Database session.
        limit: Maximum number of results.

    Returns:
        List of dicts with standard item fields + 'score' (ts_rank_cd float).
        Empty list if no matches.
    """
    limit = min(limit, 50)

    # Build a prefix query for the simple dictionary so "llm" matches "llms", "api" matches "apis", etc.
    # websearch_to_tsquery doesn't support :* prefix, so we construct it manually for single tokens.
    # For multi-word queries we fall back to plain websearch_to_tsquery('simple').
    import re as _re

    words = query.strip().split()
    _safe_token = _re.compile(r"^[a-zA-Z0-9_]+$")
    if len(words) == 1 and _safe_token.match(words[0]):
        # Single alphanumeric token: use prefix matching so acronyms/partial words hit.
        # to_tsquery raises a syntax error on special chars (e.g. "c++"), so only use
        # it when the token is safe.
        simple_query_sql = "to_tsquery('simple', :simple_token)"
        params = {
            "query": query,
            "uid": user.id,
            "lim": limit,
            "simple_token": words[0].lower() + ":*",
        }
    else:
        simple_query_sql = "websearch_to_tsquery('simple', :query)"
        params = {"query": query, "uid": user.id, "lim": limit, "simple_token": query}

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                ts_rank_cd(
                    search_vector,
                    websearch_to_tsquery('english', :query) || {simple_query_sql},
                    32
                ) AS rank
            FROM content_items
            WHERE user_id = :uid
                AND deleted_at IS NULL
                AND search_vector IS NOT NULL
                AND title IS NOT NULL AND title != ''
                AND search_vector @@ (
                    websearch_to_tsquery('english', :query) || {simple_query_sql}
                )
            ORDER BY rank DESC
            LIMIT :lim
        """
        ),
        params,
    ).fetchall()

    scores = {str(row.id): float(row.rank) for row in rows}
    return hydrate_items(rows, db, scores=scores)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def rrf_fuse(
    *lists: list[str],
    k: int = 60,
    limit: int | None = None,
) -> list[str]:
    """
    Merge N ranked ID lists using Reciprocal Rank Fusion (RRF).

    RRF score for each ID = sum(1 / (k + rank)) across all lists simultaneously.
    Rank is 1-indexed. IDs not present in a list contribute 0 from that list.

    Args:
        *lists: Any number of ordered ID lists (rank 1 = index 0).
        k: Smoothing constant (default 60, from the original RRF paper).
        limit: If provided, truncate output to this length.

    Returns:
        IDs sorted by descending RRF score.
    """
    scores: dict[str, float] = {}
    for ranked_list in lists:
        for rank, id_ in enumerate(ranked_list, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)

    fused = sorted(scores, key=lambda x: scores[x], reverse=True)
    return fused[:limit] if limit is not None else fused


# ---------------------------------------------------------------------------
# User search context
# ---------------------------------------------------------------------------


def get_user_search_context(user: User, db: Session) -> tuple[set[str], set[str]]:
    """
    Load the user's known authors and tags for query classification.

    Used by classify_query() to detect when a plain-text query matches
    a known author or tag and route it to a filter instead of keyword search.

    Returns:
        (authors_set, tags_set) — both contain lowercased strings.
    """
    author_rows = db.execute(
        text(
            """
            SELECT DISTINCT author
            FROM content_items
            WHERE user_id = :uid
                AND deleted_at IS NULL
                AND author IS NOT NULL
                AND author != ''
        """
        ),
        {"uid": user.id},
    ).fetchall()
    authors = {row.author.lower() for row in author_rows}

    tag_rows = db.execute(
        text(
            """
            SELECT DISTINCT unnest(tags) AS tag
            FROM content_items
            WHERE user_id = :uid
                AND deleted_at IS NULL
                AND tags IS NOT NULL
        """
        ),
        {"uid": user.id},
    ).fetchall()
    tags = {row.tag.lower() for row in tag_rows}

    return authors, tags


# ---------------------------------------------------------------------------
# Unified hybrid search
# ---------------------------------------------------------------------------


def _semantic_search(
    query: str,
    user: User,
    db: Session,
    limit: int,
) -> list[dict]:
    """
    Run semantic search using OpenAI embeddings + pgvector.

    Returns [] on any failure (missing API key, network error, no embeddings).
    Never raises.
    """
    from app.core.embedding_cache import get_or_create_query_embedding

    try:
        from app.core.config import settings

        # Check if any embeddings exist before calling the embed provider
        has_any = (
            db.query(ContentItem)
            .filter(
                ContentItem.user_id == user.id,
                ContentItem.embedding.isnot(None),
                ContentItem.deleted_at.is_(None),
            )
            .first()
        )
        if not has_any:
            return []

        try:
            import redis as redis_lib

            r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            r.ping()
            query_embedding = get_or_create_query_embedding(query, redis_client=r)
        except Exception:
            # Redis unavailable — call OpenAI directly
            from app.core.embedding_cache import call_embed

            query_embedding = call_embed(query)

        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

        # Use chunk-level embeddings when available (MAX similarity across chunks).
        # Falls back to item-level embedding for articles without chunks.
        rows = db.execute(
            text(
                """
                WITH chunk_scores AS (
                    SELECT cc.content_item_id AS id,
                           MAX(1 - (cc.embedding <=> CAST(:q AS vector))) AS similarity
                    FROM content_chunks cc
                    JOIN content_items ci ON ci.id = cc.content_item_id
                    WHERE cc.user_id = :uid
                      AND ci.deleted_at IS NULL
                      AND cc.embedding IS NOT NULL
                      AND ci.title IS NOT NULL AND ci.title != ''
                    GROUP BY cc.content_item_id
                ),
                item_scores AS (
                    SELECT ci.id,
                           (1 - (ci.embedding <=> CAST(:q AS vector))) AS similarity
                    FROM content_items ci
                    WHERE ci.user_id = :uid
                      AND ci.deleted_at IS NULL
                      AND ci.embedding IS NOT NULL
                      AND ci.title IS NOT NULL AND ci.title != ''
                      AND NOT EXISTS (
                          SELECT 1 FROM content_chunks cc2
                          WHERE cc2.content_item_id = ci.id AND cc2.embedding IS NOT NULL
                      )
                ),
                combined AS (
                    SELECT id, similarity FROM chunk_scores
                    UNION ALL
                    SELECT id, similarity FROM item_scores
                )
                SELECT id, similarity
                FROM combined
                ORDER BY similarity DESC
                LIMIT :lim
            """
            ),
            {"q": embedding_str, "uid": user.id, "lim": limit},
        ).fetchall()

        scores = {str(row.id): float(row.similarity) for row in rows}
        return hydrate_items(rows, db, scores=scores)

    except Exception:
        return []


# Gate: minimum cosine similarity for an entity to contribute to results.
# Entities below this threshold are ignored even if they match the query.
_ENTITY_SIM_THRESHOLD = 0.40

# Gate: minimum cosine similarity for an anchor entity to trigger 1-hop expansion.
# Higher than _ENTITY_SIM_THRESHOLD — only high-confidence anchors expand.
_ENTITY_EXPAND_THRESHOLD = 0.45

# Weight for secondary entity contributions on the same article.
# Score = best_contribution + _SECONDARY_WEIGHT * sum(rest).
_ENTITY_SECONDARY_WEIGHT = 0.3


def _score_entity_articles(
    mention_rows: list,
    sim_map: dict[str, float],
    secondary_weight: float = _ENTITY_SECONDARY_WEIGHT,
    name_map: dict[str, str] | None = None,
) -> tuple[dict[str, float], dict[str, list[dict]]]:
    """
    Pure function: article_id → entity-lane score + matched_via breakdown.

    contribution = sim / log2(2 + entity_article_count)  — IDF dampening.
    final_score  = best_contribution + secondary_weight * sum(rest).

    Hub entities (high article_count) contribute less due to IDF, not via
    a binary exclusion gate. No DB access, no side effects.

    Args:
        mention_rows: rows with .article_id, .entity_id, .entity_article_count.
        sim_map: entity_id (str) → cosine similarity to query.
        secondary_weight: weight for contributions beyond the best one.
        name_map: entity_id (str) → entity name (for matched_via payload).

    Returns:
        (scores, matched_via) where:
          scores     — str(article_id) → float score
          matched_via — str(article_id) → [{name, sim}] sorted by sim desc
    """
    article_contributions: dict[str, list[float]] = {}
    article_entity_sims: dict[str, dict[str, float]] = {}  # aid → {eid: sim}
    for mr in mention_rows:
        aid = str(mr.article_id)
        eid = str(mr.entity_id)
        sim = sim_map.get(eid)
        if sim is None:
            continue
        count = int(mr.entity_article_count)
        article_contributions.setdefault(aid, []).append(sim / _math.log2(2 + count))
        article_entity_sims.setdefault(aid, {})[eid] = sim

    scores: dict[str, float] = {}
    matched_via: dict[str, list[dict]] = {}
    for aid, contribs in article_contributions.items():
        contribs.sort(reverse=True)
        scores[aid] = contribs[0] + secondary_weight * sum(contribs[1:])
        eid_sim = article_entity_sims[aid]
        matched_via[aid] = sorted(
            [
                {"name": (name_map or {}).get(eid, eid), "sim": round(sim, 4)}
                for eid, sim in eid_sim.items()
            ],
            key=lambda x: x["sim"],
            reverse=True,
        )
    return scores, matched_via


def _entity_search(
    query: str,
    user: User,
    db: Session,
    limit: int,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """
    Entity-augmented search lane for mode="full".

    1. Accept pre-computed query_embedding from hybrid_search (avoids re-embedding).
    2. Exact name match path: always included regardless of sim threshold.
    3. Threshold-based candidate selection: all entities above _ENTITY_SIM_THRESHOLD,
       no hardcoded count limit. Scales to thousands of entities per user.
    4. 1-hop expansion from high-confidence anchors (sim >= _ENTITY_EXPAND_THRESHOLD).
       No binary hub cap — IDF dampening in scoring handles high-frequency entities.
    5. Neighbor sims: fetched via direct cosine query against stored embeddings,
       not a proxy derived from anchor sims.
    6. Scoring via _score_entity_articles() — pure, unit-testable function.

    Returns [] if entity graph is empty, embeddings not built, or gate fails.
    Never raises.
    """
    from app.core.embedding_cache import get_or_create_query_embedding, call_embed
    from app.core.entity_graph import get_entity_neighbors
    from app.models.entity import Entity

    try:
        has_embeddings = (
            db.query(Entity)
            .filter(
                Entity.user_id == user.id,
                Entity.embedding.isnot(None),
            )
            .first()
        )
        if not has_embeddings:
            return []

        # Exact case-insensitive name match bypasses the similarity threshold.
        exact_rows = db.execute(
            text(
                """
                SELECT e.id,
                       1.0 AS sim,
                       COUNT(em.content_item_id) AS article_count
                FROM entities e
                LEFT JOIN entity_mentions em ON em.entity_id = e.id
                WHERE e.user_id = :uid
                  AND LOWER(e.name) = LOWER(:q)
                GROUP BY e.id
                """
            ),
            {"uid": user.id, "q": query.strip()},
        ).fetchall()

        # Use pre-computed embedding from hybrid_search if available.
        if query_embedding is None:
            try:
                import redis as redis_lib
                from app.core.config import settings as _settings

                r = redis_lib.from_url(_settings.REDIS_URL, socket_connect_timeout=1)
                r.ping()
                query_embedding = get_or_create_query_embedding(query, redis_client=r)
            except Exception:
                query_embedding = call_embed(query)

        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

        # All entities above threshold — no LIMIT. The threshold gates quality;
        # a fixed count would silently drop relevant entities as library grows.
        sim_rows = db.execute(
            text(
                """
                SELECT e.id,
                       1 - (e.embedding <=> CAST(:q AS vector)) AS sim,
                       COUNT(em.content_item_id) AS article_count
                FROM entities e
                LEFT JOIN entity_mentions em ON em.entity_id = e.id
                WHERE e.user_id = :uid
                  AND e.embedding IS NOT NULL
                  AND 1 - (e.embedding <=> CAST(:q AS vector)) >= :thresh
                GROUP BY e.id, e.embedding
                ORDER BY sim DESC
                """
            ),
            {"q": embedding_str, "uid": user.id, "thresh": _ENTITY_SIM_THRESHOLD},
        ).fetchall()

        # Merge exact matches with threshold results, deduplicating by id.
        seen_ids: set[str] = {str(r.id) for r in exact_rows}
        rows = list(exact_rows) + [r for r in sim_rows if str(r.id) not in seen_ids]

        if not rows:
            return []

        # Gate: require at least one entity above threshold (exact matches always pass).
        top_sim = max(float(r.sim) for r in rows)
        if top_sim < _ENTITY_SIM_THRESHOLD and not exact_rows:
            return []

        # Build sim_map, name_map, and collect expand-eligible anchors.
        # No hub cap — IDF dampening in _score_entity_articles handles high-frequency
        # entities proportionally. Only the sim threshold gates expansion.
        expand_ids = []
        sim_map: dict[str, float] = {}
        name_map: dict[str, str] = {}

        # Fetch entity names for matched_via payload
        all_entity_ids_for_names = [str(r.id) for r in rows]
        name_rows = db.execute(
            text("SELECT id, name FROM entities WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": all_entity_ids_for_names},
        ).fetchall()
        for nr in name_rows:
            name_map[str(nr.id)] = nr.name

        for row in rows:
            eid = str(row.id)
            sim_map[eid] = float(row.sim)
            if row.sim >= _ENTITY_EXPAND_THRESHOLD:
                expand_ids.append(row.id)

        # 1-hop graph expansion from high-confidence anchors.
        neighbor_ids: list = []
        if expand_ids:
            neighbor_ids = get_entity_neighbors(expand_ids, db, hops=1)

        # Fetch real cosine sims for neighbor entities against the query.
        # This replaces the old proxy (0.5 × min_anchor_sim) with actual measured
        # similarity — neighbors that are genuinely query-relevant score correctly.
        if neighbor_ids:
            neighbor_id_strs = [str(n) for n in neighbor_ids]
            new_neighbors = [n for n in neighbor_id_strs if n not in sim_map]
            if new_neighbors:
                nb_sim_rows = db.execute(
                    text(
                        """
                        SELECT e.id,
                               1 - (e.embedding <=> CAST(:q AS vector)) AS sim
                        FROM entities e
                        WHERE e.id = ANY(CAST(:nb_ids AS uuid[]))
                          AND e.embedding IS NOT NULL
                        """
                    ),
                    {"q": embedding_str, "nb_ids": new_neighbors},
                ).fetchall()
                for nb in nb_sim_rows:
                    nb_eid = str(nb.id)
                    sim_map[nb_eid] = float(nb.sim)

                # Fetch names for neighbor entities not already in name_map
                new_nb_ids = [n for n in new_neighbors if n not in name_map]
                if new_nb_ids:
                    nb_name_rows = db.execute(
                        text(
                            "SELECT id, name FROM entities WHERE id = ANY(CAST(:ids AS uuid[]))"
                        ),
                        {"ids": new_nb_ids},
                    ).fetchall()
                    for nnr in nb_name_rows:
                        name_map[str(nnr.id)] = nnr.name

        all_entity_ids = list(sim_map.keys())

        # Fetch mention rows for all contributing entities (anchors + neighbors).
        mention_rows = db.execute(
            text(
                """
                SELECT em.content_item_id AS article_id,
                       em.entity_id,
                       COUNT(*) OVER (PARTITION BY em.entity_id) AS entity_article_count
                FROM entity_mentions em
                WHERE em.entity_id = ANY(CAST(:eids AS uuid[]))
                  AND em.user_id = :uid
                """
            ),
            {"eids": all_entity_ids, "uid": str(user.id)},
        ).fetchall()

        if not mention_rows:
            return []

        article_scores, matched_via = _score_entity_articles(
            mention_rows, sim_map, name_map=name_map
        )

        if not article_scores:
            return []

        sorted_ids = sorted(
            article_scores, key=lambda x: article_scores[x], reverse=True
        )[:limit]

        class _Row:
            def __init__(self, id_):
                self.id = id_

        article_id_rows = [_Row(aid) for aid in sorted_ids]
        results = hydrate_items(article_id_rows, db, scores=article_scores)
        for r in results:
            r["match_type"] = "entity"
            r["matched_via"] = matched_via.get(r["id"], [])
        return results

    except Exception:
        return []


def hybrid_search(
    *,
    query: str,
    user: User,
    db: Session,
    limit: int = 10,
    offset: int = 0,
    mode: str = "auto",
    user_authors: set[str] | None = None,
    user_tags: set[str] | None = None,
) -> list[dict]:
    """
    Unified search entry point.

    mode="auto"  — classify query and dispatch to cheapest path (navbar search)
    mode="full"  — always run keyword + filter + semantic and RRF-fuse all three
                   (modal search: maximum recall, no shortcuts)

    Args:
        query: Raw search string from the user.
        user: Authenticated user.
        db: Database session.
        limit: Maximum results.
        offset: Skip first N results (for pagination).
        mode: "auto" (default) or "full".
        user_authors: Lowercased known authors for the classifier (optional).
        user_tags: Lowercased known tags for the classifier (optional).

    Returns:
        List of item dicts with 'score' and 'match_type' fields, ordered by relevance.
    """
    from app.core.search_router import classify_query, parse_filter_query

    fetch = offset + limit  # fetch enough to slice after offset

    # Extract date operators once — applied as post-filter across all engines
    from app.core.search_router import _extract_operators

    all_meta = _extract_operators(query)
    after_date = all_meta.get("after")
    before_date = all_meta.get("before")
    clean_query = _strip_date_operators(
        query
    )  # query without after:/before: for keyword/semantic

    if mode == "full":
        # Run all four lanes regardless of query type, fuse with RRF
        fetch_limit = fetch * 3
        filter_meta = classify_query(query, user_authors=user_authors)[1]
        filter_results = (
            parse_filter_query(meta=filter_meta, user=user, db=db, limit=fetch_limit)
            if filter_meta
            else []
        )
        kw_results = (
            keyword_search(query=clean_query, user=user, db=db, limit=fetch_limit)
            if clean_query
            else []
        )
        sem_results = (
            _semantic_search(clean_query, user, db, fetch_limit) if clean_query else []
        )
        # Compute embedding once for the entity lane. _semantic_search already
        # cached it in Redis; retrieve from cache to avoid a second API call.
        _entity_embedding: list[float] | None = None
        if clean_query:
            try:
                from app.core.embedding_cache import get_or_create_query_embedding
                import redis as _redis_lib
                from app.core.config import settings as _cfg

                _r = _redis_lib.from_url(_cfg.REDIS_URL, socket_connect_timeout=1)
                _r.ping()
                _entity_embedding = get_or_create_query_embedding(
                    clean_query, redis_client=_r
                )
            except Exception:
                pass
        entity_results = (
            _entity_search(
                clean_query, user, db, fetch_limit, query_embedding=_entity_embedding
            )
            if clean_query
            else []
        )

        item_lookup: dict[str, dict] = {}
        for r in filter_results:
            r.setdefault("score", 1.0)
            r["match_type"] = "filter"
            item_lookup[r["id"]] = r
        for r in kw_results:
            r["match_type"] = "keyword"
            item_lookup.setdefault(r["id"], r)
        for r in sem_results:
            r["match_type"] = "semantic"
            r["semantic_score"] = r.get("score", 0.0)  # preserve before RRF overwrites
            item_lookup.setdefault(r["id"], r)
        for r in entity_results:
            # Entity lane can promote articles not found by other lanes
            if r["id"] not in item_lookup:
                item_lookup[r["id"]] = r
            else:
                item_lookup[r["id"]]["match_type"] = "entity+semantic"

        filter_ids = [r["id"] for r in filter_results]
        kw_ids = [r["id"] for r in kw_results]
        sem_ids = [r["id"] for r in sem_results]

        # Four-way fusion: filter/keyword/semantic use rank-based RRF (k=60).
        # Entity lane uses its IDF-dampened similarity score directly (already
        # capped-sum inside _entity_search), scaled to sit alongside RRF weights.
        # A typical top-RRF score is ~1/61 ≈ 0.0164; entity scores range 0.2–0.7,
        # so we scale by 0.025 to keep entity contribution comparable but subordinate.
        _ENTITY_SCORE_SCALE = 0.025
        from collections import defaultdict as _dd

        _scores: dict[str, float] = _dd(float)
        for _rank, _id in enumerate(filter_ids, 1):
            _scores[_id] += 1.0 / (60 + _rank)
        for _rank, _id in enumerate(kw_ids, 1):
            _scores[_id] += 1.0 / (60 + _rank)
        for _rank, _id in enumerate(sem_ids, 1):
            _scores[_id] += 1.0 / (60 + _rank)
        for r in entity_results:
            _scores[r["id"]] += r.get("score", 0.0) * _ENTITY_SCORE_SCALE
        all_fused = sorted(_scores, key=lambda x: _scores[x], reverse=True)[:fetch]

        fused_results = []
        for rank, id_ in enumerate(all_fused, start=1):
            if id_ in item_lookup:
                item = dict(item_lookup[id_])
                item["score"] = 1.0 / (60 + rank)
                fused_results.append(item)

        paged = fused_results[offset : offset + limit]
        return _apply_date_filter(paged, after_date, before_date)

    search_type, meta = classify_query(query, user_authors=user_authors)

    # For non-full mode: strip date operators from query passed to keyword/semantic
    # (filter path handles dates natively via parse_filter_query)
    kw_query = clean_query if clean_query else query

    if search_type == "filter":
        results = parse_filter_query(meta=meta, user=user, db=db, limit=fetch)
        for r in results:
            r.setdefault("score", 1.0)
            r["match_type"] = "filter"
        return results[offset:]

    if search_type == "keyword":
        results = keyword_search(query=kw_query, user=user, db=db, limit=fetch)
        if not results:
            sem = _semantic_search(kw_query, user, db, fetch)
            if sem:
                for r in sem:
                    r["match_type"] = "semantic_fallback"
                return _apply_date_filter(sem[offset:], after_date, before_date)
        for r in results:
            r["match_type"] = "keyword"
        return _apply_date_filter(results[offset:], after_date, before_date)

    if search_type == "semantic":
        results = _semantic_search(kw_query, user, db, fetch)
        if not results:
            results = keyword_search(query=kw_query, user=user, db=db, limit=fetch)
            for r in results:
                r["match_type"] = "keyword"
            return _apply_date_filter(results[offset:], after_date, before_date)
        for r in results:
            r["match_type"] = "semantic"
        return _apply_date_filter(results[offset:], after_date, before_date)

    # "hybrid" — run both and fuse
    fetch_limit = fetch * 3
    kw_results = keyword_search(query=kw_query, user=user, db=db, limit=fetch_limit)
    sem_results = _semantic_search(kw_query, user, db, fetch_limit)

    if not sem_results:
        for r in kw_results:
            r["match_type"] = "keyword"
        return _apply_date_filter(
            kw_results[offset : offset + limit], after_date, before_date
        )

    kw_ids = [r["id"] for r in kw_results]
    sem_ids = [r["id"] for r in sem_results]
    fused_ids = rrf_fuse(kw_ids, sem_ids, k=60, limit=fetch)

    item_lookup: dict[str, dict] = {}
    for r in kw_results:
        item_lookup[r["id"]] = r
    for r in sem_results:
        if r["id"] not in item_lookup:
            item_lookup[r["id"]] = r

    fused_results = []
    for rank, id_ in enumerate(fused_ids, start=1):
        if id_ in item_lookup:
            item = dict(item_lookup[id_])
            item["score"] = 1.0 / (60 + rank)
            item["match_type"] = "hybrid"
            fused_results.append(item)

    paged = fused_results[offset : offset + limit]
    return _apply_date_filter(paged, after_date, before_date)
