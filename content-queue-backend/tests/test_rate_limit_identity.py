"""Rate-limit identity (user vs IP) and atomicity of the Redis limiter."""

import asyncio
import threading
from datetime import timedelta

from app.core.auth_cookies import ACCESS_TOKEN_COOKIE, CSRF_COOKIE, CSRF_HEADER
from app.core.security import create_access_token
from app.middleware.rate_limit import RedisRateLimiter


def _token(email, **kw):
    return create_access_token({"sub": email}, **kw)


def _burn(client, n, headers=None, cookies=None):
    """POST /content n times (limit 10/min); return list of status codes."""
    return [
        client.post("/content", json={}, headers=headers, cookies=cookies).status_code
        for _ in range(n)
    ]


def test_two_users_same_ip_have_separate_buckets(client):
    a = {"Authorization": f"Bearer {_token('a@example.com')}"}
    b = {"Authorization": f"Bearer {_token('b@example.com')}"}
    codes = _burn(client, 11, headers=a)
    assert codes[-1] == 429 and 429 not in codes[:10]
    assert _burn(client, 1, headers=b)[0] != 429


def test_cookie_and_bearer_for_same_user_share_bucket(client):
    tok = _token("same@example.com")
    _burn(client, 6, headers={"Authorization": f"Bearer {tok}"})
    codes = _burn(
        client,
        5,
        headers={CSRF_HEADER: "csrf"},
        cookies={ACCESS_TOKEN_COOKIE: tok, CSRF_COOKIE: "csrf"},
    )
    assert codes[-1] == 429 and 429 not in codes[:4]


def test_invalid_or_expired_token_falls_back_to_ip(client):
    bad = {"Authorization": "Bearer not-a-jwt"}
    expired = {
        "Authorization": f"Bearer {_token('e@example.com', expires_delta=timedelta(seconds=-5))}"
    }
    _burn(client, 5, headers=bad)
    codes = _burn(client, 6, headers=expired)  # same ip bucket as the bad ones
    assert codes[-1] == 429 and 429 not in codes[:5]
    # a valid user is not affected by the exhausted IP bucket
    ok = {"Authorization": f"Bearer {_token('v@example.com')}"}
    assert _burn(client, 1, headers=ok)[0] != 429


def test_anonymous_login_still_limited_per_ip(client):
    codes = [
        client.post("/auth/login", data={"username": "x", "password": "y"}).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429 and 429 not in codes[:10]


def test_concurrent_requests_cannot_exceed_limit():
    limiter = RedisRateLimiter()
    results = []
    barrier = threading.Barrier(40)

    def worker():
        barrier.wait()
        results.append(
            asyncio.run(
                limiter.is_allowed("atomic:test", max_requests=10, window_seconds=60)
            )
        )

    ts = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 10
