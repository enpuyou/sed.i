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


_ENTITY_SIM_THRESHOLD = 0.40
# Only use anchors above this sim for neighbor expansion — prevents hub entities
# (e.g. Claude ×6, Anthropic ×10) from flooding results via low-sim matches.
_ENTITY_EXPAND_THRESHOLD = 0.45
# Cap: entities appearing in this many articles are too generic to expand from.
_ENTITY_HUB_ARTICLE_CAP = 4


def _entity_search(
    query: str,
    user: User,
    db: Session,
    limit: int,
) -> list[dict]:
    """
    Entity-augmented search lane for mode="full".

    1. Embed the query and find top entity nodes by vector similarity.
    2. Gate: if top entity similarity < _ENTITY_SIM_THRESHOLD, return [].
    3. Split anchors into two tiers:
       - Strong (sim >= _ENTITY_EXPAND_THRESHOLD, article_count <= hub cap):
         eligible for 1-hop graph expansion.
       - Weak (sim >= threshold but below expand threshold, or hub entities):
         only their own articles are included, no expansion.
    4. Score articles by Σ(anchor_sim / log2(2 + entity_article_count)) across
       all matched entities — IDF-like dampening so hub entities (Claude, Anthropic)
       contribute proportionally less than precise single-article entities.

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

        # Query entity linking: exact case-insensitive name match bypasses the
        # similarity threshold entirely. "enshittification" → guaranteed entity hit.
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

        try:
            import redis as redis_lib
            from app.core.config import settings as _settings

            r = redis_lib.from_url(_settings.REDIS_URL, socket_connect_timeout=1)
            r.ping()
            query_embedding = get_or_create_query_embedding(query, redis_client=r)
        except Exception:
            query_embedding = call_embed(query)

        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

        # Top-8 candidates — article_count from mentions table (article_count column is stale)
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
                GROUP BY e.id, e.embedding
                ORDER BY sim DESC
                LIMIT 8
            """
            ),
            {"q": embedding_str, "uid": user.id},
        ).fetchall()

        # Merge exact matches (sim=1.0) with similarity results, deduplicating by id.
        seen_ids: set[str] = {str(r.id) for r in exact_rows}
        rows = list(exact_rows) + [r for r in sim_rows if str(r.id) not in seen_ids]

        if not rows:
            return []

        # Gate passes if ANY exact match OR top similarity match exceeds threshold.
        top_sim = max(float(r.sim) for r in rows)
        if top_sim < _ENTITY_SIM_THRESHOLD and not exact_rows:
            return []

        # Split into expand-eligible vs direct-only
        expand_ids = []
        direct_ids = []
        sim_map: dict[str, float] = {}
        article_count_map: dict[str, int] = {}

        for row in rows:
            if row.sim < _ENTITY_SIM_THRESHOLD:
                continue
            eid = str(row.id)
            sim_map[eid] = float(row.sim)
            article_count_map[eid] = int(row.article_count or 0)
            if (
                row.sim >= _ENTITY_EXPAND_THRESHOLD
                and row.article_count <= _ENTITY_HUB_ARTICLE_CAP
            ):
                expand_ids.append(row.id)
            else:
                direct_ids.append(row.id)

        # 1-hop expansion only from non-hub, high-confidence anchors
        neighbor_ids: list = []
        if expand_ids:
            neighbor_ids = get_entity_neighbors(expand_ids, db, hops=1)

        # Gather all entity ids that contribute to article scoring
        all_anchor_ids = list(sim_map.keys())
        all_entity_ids = list({*all_anchor_ids, *[str(n) for n in neighbor_ids]})

        # Fetch per-article, per-entity mention rows to compute weighted score
        mention_rows = db.execute(
            text(
                """
                SELECT em.content_item_id AS article_id,
                       em.entity_id,
                       COUNT(em.content_item_id) OVER (PARTITION BY em.entity_id) AS entity_article_count
                FROM entity_mentions em
                WHERE em.entity_id = ANY(CAST(:eids AS uuid[]))
                  AND em.user_id = :uid
            """
            ),
            {"eids": all_entity_ids, "uid": str(user.id)},
        ).fetchall()

        if not mention_rows:
            return []

        # Score: capped-sum of per-entity contributions per article.
        # contribution = anchor_sim / log2(2 + entity_article_count)
        # Final score = best_contribution + 0.3 * sum(remaining)
        # This prevents runaway accumulation when many Claude-family or generic
        # entities all fire on the same article, while still rewarding genuine
        # multi-entity co-occurrence with a small additive bonus.
        min_anchor_sim = min(sim_map.values())
        neighbor_sim = 0.5 * min_anchor_sim
        article_contributions: dict[str, list[float]] = {}
        for mr in mention_rows:
            aid = str(mr.article_id)
            eid = str(mr.entity_id)
            sim = sim_map.get(eid, neighbor_sim)
            count = int(mr.entity_article_count)
            article_contributions.setdefault(aid, []).append(
                sim / _math.log2(2 + count)
            )

        _SECONDARY_WEIGHT = 0.3
        article_scores: dict[str, float] = {}
        for aid, contribs in article_contributions.items():
            contribs.sort(reverse=True)
            article_scores[aid] = contribs[0] + _SECONDARY_WEIGHT * sum(contribs[1:])

        if not article_scores:
            return []

        # Build ordered rows for hydration
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
        entity_results = (
            _entity_search(clean_query, user, db, fetch_limit) if clean_query else []
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
