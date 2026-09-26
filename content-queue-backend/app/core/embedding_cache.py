"""
Redis-backed cache for query embeddings.

Avoids redundant API calls for repeated or near-identical search queries.
Cache key: qemb:{sha256(normalized_query)[:16]}
TTL: 3600 seconds (1 hour)
"""

from __future__ import annotations

import hashlib
import json


def call_embed(query: str, *, user_id: str | None = None) -> list[float]:
    """
    Embed a single query string via llm_client and return the float vector.
    Isolated as its own function so it can be patched in tests.

    user_id: if set, wraps the call in a braintrust_span (cost-by-user
    rollups) and passes it through to llm_client.embed for daily budget
    enforcement. Optional because this is a shared cross-cutting utility
    called from places that don't always have a user in scope (e.g. a
    request-scoped search helper called before auth is resolved) — omit to
    skip both tracing metadata and budget enforcement, matching the
    omit-to-skip convention used throughout llm_client.py.
    """
    from app.core.llm_client import llm_client, braintrust_span

    with braintrust_span(
        "query_embed",
        input={"query": query},
        metadata={"user_id": user_id} if user_id else None,
    ):
        result = llm_client.embed(query, user_id=user_id)
    return result.embeddings[0]


def get_or_create_query_embedding(
    query: str,
    *,
    redis_client,
    user_id: str | None = None,
) -> list[float]:
    """
    Return the embedding for a query, using Redis as a cache.

    On a cache miss: calls llm_client.embed and stores the result for 1 hour.
    On a cache hit: returns the stored vector without an API call — no
    braintrust_span or budget check either, since no LLM call happens.

    user_id: see call_embed — passed through on a cache miss only.
    """
    normalized = query.lower().strip()
    cache_key = "qemb:" + hashlib.sha256(normalized.encode()).hexdigest()[:16]

    cached = redis_client.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    embedding = call_embed(query, user_id=user_id)
    redis_client.setex(cache_key, 3600, json.dumps(embedding))
    return embedding
