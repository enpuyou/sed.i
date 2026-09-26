"""
Unit tests for the in-memory RateLimiter.

Tests the sliding-window dequeue logic directly (no HTTP, no DB).
Covers:
- Requests within limit are allowed
- Exceeding limit is rejected
- Old requests age out of the window (window expiry)
- Different identifiers have independent buckets
"""

import asyncio
from unittest.mock import patch

from app.middleware.rate_limit import RateLimiter


def run(coro):
    """Helper: run an async coroutine synchronously in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_allows_requests_within_limit():
    """5 requests within a 5-request limit are all allowed."""
    limiter = RateLimiter()
    for _ in range(5):
        assert run(limiter.is_allowed("user:test", max_requests=5, window_seconds=60))


def test_blocks_request_over_limit():
    """The 6th request when limit is 5 is rejected."""
    limiter = RateLimiter()
    for _ in range(5):
        run(limiter.is_allowed("user:test", max_requests=5, window_seconds=60))
    assert not run(limiter.is_allowed("user:test", max_requests=5, window_seconds=60))


def test_window_expiry_allows_new_requests():
    """
    Requests older than the window are evicted, freeing up capacity.

    Uses a mocked clock so the test does not sleep — deterministic and instant.
    """
    limiter = RateLimiter()
    t0 = 1_000_000.0

    with patch("app.middleware.rate_limit.time.time", return_value=t0):
        for _ in range(3):
            run(limiter.is_allowed("user:expiry", max_requests=3, window_seconds=1))
        # Immediately at t0: should be blocked
        assert not run(
            limiter.is_allowed("user:expiry", max_requests=3, window_seconds=1)
        )

    # Advance clock past the 1-second window
    with patch("app.middleware.rate_limit.time.time", return_value=t0 + 1.1):
        assert run(limiter.is_allowed("user:expiry", max_requests=3, window_seconds=1))


def test_different_identifiers_are_independent():
    """Two different identifiers have separate rate limit buckets."""
    limiter = RateLimiter()

    # Fill user A's bucket
    for _ in range(3):
        run(limiter.is_allowed("user:A", max_requests=3, window_seconds=60))
    assert not run(limiter.is_allowed("user:A", max_requests=3, window_seconds=60))

    # User B's bucket is untouched
    assert run(limiter.is_allowed("user:B", max_requests=3, window_seconds=60))


def test_single_request_limit():
    """Edge case: limit of 1 allows exactly one request."""
    limiter = RateLimiter()
    assert run(limiter.is_allowed("user:strict", max_requests=1, window_seconds=60))
    assert not run(limiter.is_allowed("user:strict", max_requests=1, window_seconds=60))
