"""
Tests for the MCP OAuth 2.1 + PKCE endpoints.

Covers:
- GET /.well-known/oauth-authorization-server  (discovery)
- GET /.well-known/oauth-protected-resource
- POST /mcp-transport/register  (dynamic client registration)
- GET /mcp-transport/authorize  (login form)
- POST /mcp-transport/authorize (credential validation + code issuance)
- POST /mcp-transport/token     (authorization_code grant with PKCE S256)
- POST /mcp-transport/token     (refresh_token grant)
- PKCE verification edge cases  (wrong verifier, expired code, replay)

Redis is mocked with a simple in-memory dict so tests run without a real Redis.
"""

import base64
import hashlib
import json
import secrets
from unittest.mock import patch

import pytest

from app.core.security import get_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256_challenge) pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class FakeRedis:
    """In-memory Redis substitute — supports setex, get, delete."""

    def __init__(self):
        self._store: dict[str, tuple[str, int]] = {}  # key → (value, ttl)

    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = (value, ttl)

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        return entry[0] if entry else None

    def delete(self, key: str):
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def oauth_user(db_session):
    from app.models.user import User

    u = User(
        email="oauth@example.com",
        username="oauthuser",
        hashed_password=get_password_hash("correct-password"),
        full_name="OAuth User",
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


# ---------------------------------------------------------------------------
# Discovery endpoints
# ---------------------------------------------------------------------------


class TestOAuthDiscovery:
    def test_authorization_server_metadata(self, client):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert data["authorization_endpoint"].endswith("/mcp-transport/authorize")
        assert data["token_endpoint"].endswith("/mcp-transport/token")
        assert data["registration_endpoint"].endswith("/mcp-transport/register")
        assert "S256" in data["code_challenge_methods_supported"]
        assert "authorization_code" in data["grant_types_supported"]

    def test_protected_resource_metadata(self, client):
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert "resource" in data
        assert data["resource"].endswith("/mcp-transport")
        assert "authorization_servers" in data
        assert "bearer_methods_supported" in data
        assert "header" in data["bearer_methods_supported"]


# ---------------------------------------------------------------------------
# Dynamic client registration
# ---------------------------------------------------------------------------


class TestDynamicClientRegistration:
    def test_registers_client_and_returns_201(self, client):
        resp = client.post(
            "/mcp-transport/register",
            json={
                "redirect_uris": ["http://localhost:5173/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert data["redirect_uris"] == ["http://localhost:5173/callback"]

    def test_accepts_provided_client_id(self, client):
        resp = client.post(
            "/mcp-transport/register",
            json={
                "client_id": "my-custom-client",
                "redirect_uris": ["http://localhost/cb"],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["client_id"] == "my-custom-client"

    def test_generates_client_id_when_not_provided(self, client):
        resp = client.post("/mcp-transport/register", json={})
        assert resp.status_code == 201
        assert len(resp.json()["client_id"]) > 0

    def test_empty_body_is_accepted(self, client):
        resp = client.post(
            "/mcp-transport/register",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /mcp-transport/authorize
# ---------------------------------------------------------------------------


class TestAuthorizeGet:
    def _params(self, **overrides):
        _, challenge = _pkce_pair()
        base = {
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        }
        base.update(overrides)
        return base

    def test_returns_html_login_form(self, client):
        resp = client.get("/mcp-transport/authorize", params=self._params())
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<form" in resp.text
        assert 'type="email"' in resp.text
        assert 'type="password"' in resp.text

    def test_rejects_wrong_response_type(self, client):
        resp = client.get(
            "/mcp-transport/authorize",
            params=self._params(response_type="token"),
        )
        assert resp.status_code == 400

    def test_rejects_non_s256_challenge_method(self, client):
        resp = client.get(
            "/mcp-transport/authorize",
            params=self._params(code_challenge_method="plain"),
        )
        assert resp.status_code == 400

    def test_state_is_embedded_in_form(self, client):
        resp = client.get(
            "/mcp-transport/authorize", params=self._params(state="mystate")
        )
        assert "mystate" in resp.text

    def test_missing_required_params_returns_422(self, client):
        resp = client.get("/mcp-transport/authorize", params={"client_id": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /mcp-transport/authorize
# ---------------------------------------------------------------------------


class TestAuthorizePost:
    def _form(self, fake_redis, **overrides):
        verifier, challenge = _pkce_pair()
        base = {
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "email": "oauth@example.com",
            "password": "correct-password",
        }
        base.update(overrides)
        return base, verifier

    def test_valid_credentials_redirect_with_code(self, client, oauth_user, fake_redis):
        form, _ = self._form(fake_redis)
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("http://localhost/callback")
        assert "code=" in location

    def test_state_included_in_redirect(self, client, oauth_user, fake_redis):
        form, _ = self._form(fake_redis, state="my-state-value")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        assert "state=my-state-value" in resp.headers["location"]

    def test_wrong_password_returns_401_html(self, client, oauth_user, fake_redis):
        form, _ = self._form(fake_redis, password="wrong")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.text

    def test_unknown_email_returns_401_html(self, client, fake_redis):
        form, _ = self._form(fake_redis, email="nobody@example.com")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        assert resp.status_code == 401

    def test_code_stored_in_redis(self, client, oauth_user, fake_redis):
        form, _ = self._form(fake_redis)
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        # Extract code from redirect URL
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(resp.headers["location"]).query)
        code = qs["code"][0]
        stored = fake_redis.get(f"mcp:code:{code}")
        assert stored is not None
        payload = json.loads(stored)
        assert payload["email"] == "oauth@example.com"
        assert "code_challenge" in payload

    def test_inactive_user_rejected(self, client, db_session, fake_redis):
        from app.models.user import User

        u = User(
            email="inactive@example.com",
            username="inactiveuser",
            hashed_password=get_password_hash("password"),
            is_active=False,
        )
        db_session.add(u)
        db_session.commit()

        form, _ = self._form(
            fake_redis, email="inactive@example.com", password="password"
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/authorize",
                data=form,
                follow_redirects=False,
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /mcp-transport/token — authorization_code grant
# ---------------------------------------------------------------------------


class TestTokenAuthorizationCode:
    def _store_code(
        self, fake_redis, email: str, challenge: str, redirect_uri: str, client_id: str
    ) -> str:
        code = secrets.token_urlsafe(32)
        fake_redis.setex(
            f"mcp:code:{code}",
            300,
            json.dumps(
                {
                    "email": email,
                    "code_challenge": challenge,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                }
            ),
        )
        return code

    def test_valid_pkce_exchange_returns_tokens(self, client, oauth_user, fake_redis):
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis,
            "oauth@example.com",
            challenge,
            "http://localhost/callback",
            "test-client",
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "test-client",
                    "code": code,
                    "redirect_uri": "http://localhost/callback",
                    "code_verifier": verifier,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    def test_wrong_code_verifier_rejected(self, client, oauth_user, fake_redis):
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis, "oauth@example.com", challenge, "http://localhost/callback", "c"
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "c",
                    "code": code,
                    "redirect_uri": "http://localhost/callback",
                    "code_verifier": "wrong-verifier-value",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_expired_code_rejected(self, client, oauth_user, fake_redis):
        """Code not in Redis (expired) → invalid_grant."""
        verifier, challenge = _pkce_pair()
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "c",
                    "code": "nonexistent-code",
                    "redirect_uri": "http://localhost/callback",
                    "code_verifier": verifier,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_code_is_single_use(self, client, oauth_user, fake_redis):
        """After a successful exchange the code must be deleted."""
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis, "oauth@example.com", challenge, "http://localhost/cb", "c"
        )
        payload = {
            "grant_type": "authorization_code",
            "client_id": "c",
            "code": code,
            "redirect_uri": "http://localhost/cb",
            "code_verifier": verifier,
        }
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            first = client.post("/mcp-transport/token", data=payload)
            second = client.post("/mcp-transport/token", data=payload)
        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"

    def test_redirect_uri_mismatch_rejected(self, client, oauth_user, fake_redis):
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis, "oauth@example.com", challenge, "http://localhost/callback", "c"
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "c",
                    "code": code,
                    "redirect_uri": "http://evil.example/steal",
                    "code_verifier": verifier,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_client_id_mismatch_rejected(self, client, oauth_user, fake_redis):
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis,
            "oauth@example.com",
            challenge,
            "http://localhost/cb",
            "real-client",
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "impostor-client",
                    "code": code,
                    "redirect_uri": "http://localhost/cb",
                    "code_verifier": verifier,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_missing_code_verifier_rejected(self, client, oauth_user, fake_redis):
        verifier, challenge = _pkce_pair()
        code = self._store_code(
            fake_redis, "oauth@example.com", challenge, "http://localhost/cb", "c"
        )
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "c",
                    "code": code,
                    "redirect_uri": "http://localhost/cb",
                    # code_verifier intentionally omitted
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_unsupported_grant_type_rejected(self, client):
        resp = client.post(
            "/mcp-transport/token",
            data={"grant_type": "client_credentials", "client_id": "c"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"


# ---------------------------------------------------------------------------
# POST /mcp-transport/token — refresh_token grant
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    def _store_refresh(self, fake_redis, email: str, client_id: str) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        fake_redis.setex(
            f"mcp:refresh:{digest}",
            86400,
            json.dumps({"email": email, "client_id": client_id}),
        )
        return token

    def test_valid_refresh_returns_new_tokens(self, client, oauth_user, fake_redis):
        refresh = self._store_refresh(fake_redis, "oauth@example.com", "c")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": "c",
                    "refresh_token": refresh,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New refresh token must differ from the old one
        assert data["refresh_token"] != refresh

    def test_refresh_token_is_rotated(self, client, oauth_user, fake_redis):
        """Old refresh token must be invalid after use."""
        refresh = self._store_refresh(fake_redis, "oauth@example.com", "c")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": "c",
                    "refresh_token": refresh,
                },
            )
            # Second use of the same token should fail
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": "c",
                    "refresh_token": refresh,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_expired_refresh_token_rejected(self, client, fake_redis):
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": "c",
                    "refresh_token": "does-not-exist",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_client_id_mismatch_on_refresh_rejected(
        self, client, oauth_user, fake_redis
    ):
        refresh = self._store_refresh(fake_redis, "oauth@example.com", "real-client")
        with patch("app.mcp.oauth._get_redis", return_value=fake_redis):
            resp = client.post(
                "/mcp-transport/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": "other-client",
                    "refresh_token": refresh,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_missing_refresh_token_rejected(self, client):
        resp = client.post(
            "/mcp-transport/token",
            data={"grant_type": "refresh_token", "client_id": "c"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"
