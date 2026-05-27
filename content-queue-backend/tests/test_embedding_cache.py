"""
Tests for query embedding cache.

Uses mock Redis to verify caching behavior without a real Redis instance.
"""

import json
from unittest.mock import MagicMock, patch
from app.core.embedding_cache import get_or_create_query_embedding


class TestEmbeddingCache:
    def test_returns_embedding_list(self):
        """Should return a list of floats."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Cache miss

        fake_embedding = [0.1] * 1536
        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            result = get_or_create_query_embedding(
                "test query", redis_client=mock_redis
            )
        assert result == fake_embedding
        assert len(result) == 1536

    def test_caches_result_in_redis(self):
        """After a cache miss, the embedding should be stored in Redis."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        fake_embedding = [0.1] * 1536
        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 3600  # TTL = 1 hour

    def test_returns_cached_result_on_hit(self):
        """On cache hit, should NOT call OpenAI."""
        fake_embedding = [0.1] * 1536
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(fake_embedding)

        with patch("app.core.embedding_cache.call_embed") as mock_openai:
            result = get_or_create_query_embedding(
                "test query", redis_client=mock_redis
            )
        mock_openai.assert_not_called()
        assert result == fake_embedding

    def test_same_query_same_cache_key(self):
        """Same query text -> same cache key (deterministic)."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 == key2

    def test_different_query_different_cache_key(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("query one", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("query two", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 != key2

    def test_normalizes_query_whitespace_and_case(self):
        """'  Test Query  ' and 'test query' should hit same cache key."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        fake_embedding = [0.1] * 1536

        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("  Test Query  ", redis_client=mock_redis)
            key1 = mock_redis.get.call_args[0][0]

        mock_redis.reset_mock()
        mock_redis.get.return_value = None
        with patch(
            "app.core.embedding_cache.call_embed",
            return_value=fake_embedding,
        ):
            get_or_create_query_embedding("test query", redis_client=mock_redis)
            key2 = mock_redis.get.call_args[0][0]

        assert key1 == key2
