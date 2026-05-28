"""
Redis-backed cache for query embeddings.

Avoids redundant API calls for repeated or near-identical search queries.
Cache key: qemb:{sha256(normalized_query)[:16]}
TTL: 3600 seconds (1 hour)
"""

from __future__ import annotations

import hashlib
import json


def call_embed(query: str) -> list[float]:
    """
    Embed a single query string via llm_client and return the float vector.
    Isolated as its own function so it can be patched in tests.
    """
    from app.core.llm_client import llm_client

    result = llm_client.embed(query)
    return result.embeddings[0]


def get_or_create_query_embedding(
    query: str,
    *,
    redis_client,
) -> list[float]:
    """
    Return the embedding for a query, using Redis as a cache.

    On a cache miss: calls llm_client.embed and stores the result for 1 hour.
    On a cache hit: returns the stored vector without an API call.
    """
    normalized = query.lower().strip()
    cache_key = "qemb:" + hashlib.sha256(normalized.encode()).hexdigest()[:16]

    cached = redis_client.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    embedding = call_embed(query)
    redis_client.setex(cache_key, 3600, json.dumps(embedding))
    return embedding
