"""
Redis-backed cache for OpenAI query embeddings.

Avoids redundant API calls for repeated or near-identical search queries.
Cache key: qemb:{sha256(normalized_query)[:16]}
TTL: 3600 seconds (1 hour)
"""

from __future__ import annotations

import hashlib
import json


def call_openai_embedding(query: str) -> list[float]:
    """
    Call the OpenAI embeddings API and return the 1536-dim float vector.

    Isolated as its own function so it can be patched in tests.
    """
    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=query,
        encoding_format="float",
    )
    return response.data[0].embedding


def get_or_create_query_embedding(
    query: str,
    *,
    redis_client,
) -> list[float]:
    """
    Return the embedding for a query, using Redis as a cache.

    On a cache miss: calls OpenAI and stores the result in Redis for 1 hour.
    On a cache hit: returns the stored vector without calling OpenAI.

    Args:
        query: The raw search query string.
        redis_client: A redis.Redis (or compatible) client instance.

    Returns:
        List of 1536 floats (text-embedding-3-small dimensions).
    """
    normalized = query.lower().strip()
    cache_key = "qemb:" + hashlib.sha256(normalized.encode()).hexdigest()[:16]

    cached = redis_client.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    embedding = call_openai_embedding(query)
    redis_client.setex(cache_key, 3600, json.dumps(embedding))
    return embedding
