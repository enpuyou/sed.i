import logging
import os
import time
import asyncio
from collections import defaultdict, deque
from typing import Dict, Deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """In-memory sliding-window rate limiter. Process-local — does not
    coordinate across multiple backend instances. Kept for local dev/tests
    where Redis may not be running; production traffic goes through
    RedisRateLimiter (see below) via RateLimitMiddleware."""

    def __init__(self):
        self.requests: Dict[str, Deque[float]] = defaultdict(deque)
        self.locks: Dict[str, asyncio.Lock] = {}

    async def is_allowed(
        self, identifier: str, max_requests: int, window_seconds: int
    ) -> bool:
        """Check if request is allowed within rate limit"""

        if identifier not in self.locks:
            self.locks[identifier] = asyncio.Lock()

        async with self.locks[identifier]:
            now = time.time()
            window_start = now - window_seconds

            times = self.requests[identifier]

            # Remove old requests
            while times and times[0] < window_start:
                times.popleft()

            # Check limit
            if len(times) < max_requests:
                times.append(now)
                return True

            return False


def _get_redis_client():
    """Lazy Redis client, matching the pattern in app/core/llm_client.py.
    Returns None (not raises) on connection failure — a rate-limit check
    that can't reach Redis should not block the request it's guarding."""
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception as e:
        logger.warning(f"Redis unavailable for rate limiting ({e}), degrading open")
        return None


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets, so limits are
    enforced across every backend instance rather than per-process.

    Each identifier+window gets a ZSET keyed by `ratelimit:{identifier}:{window}`,
    members are unique per-request tokens scored by request timestamp. A ZADD +
    ZREMRANGEBYSCORE (evict anything older than the window) + ZCARD per check
    keeps the set self-trimming without a separate cleanup job. TTL on the key
    matches the window so abandoned buckets expire on their own.

    Degrades open (allows the request) if Redis is unreachable — consistent
    with the LLM budget checker's fail-open behavior (app/core/llm_client.py):
    Redis is already a hard dependency for the Celery broker, so an outage
    already stops the pipeline elsewhere; blocking requests on top of that
    would add a second failure mode for no safety gain.
    """

    async def is_allowed(
        self, identifier: str, max_requests: int, window_seconds: int
    ) -> bool:
        r = _get_redis_client()
        if r is None:
            return True

        key = f"ratelimit:{identifier}:{window_seconds}"
        now = time.time()
        window_start = now - window_seconds

        try:
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            _, count = pipe.execute()

            if count >= max_requests:
                return False

            pipe = r.pipeline()
            pipe.zadd(key, {f"{now}:{os.urandom(4).hex()}": now})
            pipe.expire(key, window_seconds)
            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Redis error during rate limit check ({e}), degrading open")
            return True


# Global instances
rate_limiter = RateLimiter()
redis_rate_limiter = RedisRateLimiter()


# path -> (method, per-minute limit, per-hour limit)
# Covers the routes with real abuse/cost surface: content creation (existing),
# auth (brute-force surface), and the most expensive read path (fans out to
# up to 4 retrieval lanes plus an LLM insight call).
_LIMITED_ROUTES: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/content"): (10, 50),
    ("POST", "/auth/login"): (10, 30),
    ("POST", "/auth/register"): (5, 15),
    ("GET", "/search/semantic"): (30, 300),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware class for rate limiting. Backed by Redis so limits hold
    across multiple backend instances; see RedisRateLimiter above."""

    async def dispatch(self, request: Request, call_next):
        limits = _LIMITED_ROUTES.get((request.method, request.url.path))

        if limits is not None:
            per_minute, per_hour = limits

            # Get identifier
            user_id = "unknown"

            if hasattr(request.state, "user"):
                user = request.state.user
                if user and hasattr(user, "id"):
                    user_id = f"user:{user.id}"
            elif request.client:
                user_id = f"ip:{request.client.host}"

            route_key = f"{request.method}:{request.url.path}:{user_id}"

            # Check limits
            allowed_minute = await redis_rate_limiter.is_allowed(
                f"{route_key}:minute", per_minute, 60
            )
            allowed_hour = await redis_rate_limiter.is_allowed(
                f"{route_key}:hour", per_hour, 3600
            )

            if not (allowed_minute and allowed_hour):
                retry_after = 60 if not allowed_minute else 3600
                allowed_origins = os.getenv(
                    "ALLOWED_ORIGINS", "http://localhost:3000"
                ).split(",")
                origin = request.headers.get("origin", "")
                cors_origin = (
                    origin if origin in allowed_origins else allowed_origins[0]
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please try again later.",
                    },
                    headers={
                        "Access-Control-Allow-Origin": cors_origin,
                        "Access-Control-Allow-Credentials": "true",
                        "Retry-After": str(retry_after),
                    },
                )

        # Process request
        response = await call_next(request)
        return response
