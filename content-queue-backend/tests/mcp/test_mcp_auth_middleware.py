"""
Tests for MCP HTTP server auth layer.

Covers:
- _resolve_user_from_bearer: valid token, missing token, bad format,
  expired token, unknown user, inactive user
- MCPAuthMiddleware: 401 on missing/invalid/expired token,
  WWW-Authenticate header format, valid token passes through
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from app.core.security import create_access_token, get_password_hash
from app.mcp.http_server import MCPAuthMiddleware, _resolve_user_from_bearer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user(db_session):
    from app.models.user import User

    u = User(
        email="mcp-http@example.com",
        username="mcphttpuser",
        hashed_password=get_password_hash("password"),
        full_name="MCP HTTP User",
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def valid_token(auth_user):
    return create_access_token(data={"sub": auth_user.email})


@pytest.fixture
def expired_token(auth_user):
    return create_access_token(
        data={"sub": auth_user.email},
        expires_delta=timedelta(seconds=-1),
    )


# ---------------------------------------------------------------------------
# _resolve_user_from_bearer unit tests (no HTTP layer)
# ---------------------------------------------------------------------------


class TestResolveUserFromBearer:
    def test_valid_token_returns_user(self, db_session, auth_user, valid_token):
        user = _resolve_user_from_bearer(f"Bearer {valid_token}", db_session)
        assert user.email == auth_user.email

    def test_missing_bearer_prefix_raises(self, db_session, valid_token):
        with pytest.raises(ValueError, match="Missing Bearer token"):
            _resolve_user_from_bearer(valid_token, db_session)

    def test_empty_authorization_raises(self, db_session):
        with pytest.raises(ValueError, match="Missing Bearer token"):
            _resolve_user_from_bearer("", db_session)

    def test_garbage_token_raises(self, db_session):
        with pytest.raises(ValueError, match="Invalid token"):
            _resolve_user_from_bearer("Bearer not.a.jwt", db_session)

    def test_expired_token_raises(self, db_session, expired_token):
        with pytest.raises(ValueError, match="Invalid token"):
            _resolve_user_from_bearer(f"Bearer {expired_token}", db_session)

    def test_token_for_unknown_user_raises(self, db_session):
        token = create_access_token(data={"sub": "nobody@example.com"})
        with pytest.raises(ValueError, match="User not found"):
            _resolve_user_from_bearer(f"Bearer {token}", db_session)

    def test_token_for_inactive_user_raises(self, db_session):
        from app.models.user import User

        inactive = User(
            email="inactive-mcp@example.com",
            username="inactivemcp",
            hashed_password=get_password_hash("pw"),
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        token = create_access_token(data={"sub": inactive.email})
        with pytest.raises(ValueError, match="User not found"):
            _resolve_user_from_bearer(f"Bearer {token}", db_session)

    def test_token_missing_sub_claim_raises(self, db_session):
        # Craft a token with no 'sub' field
        token = create_access_token(data={"uid": "some-id"})
        with pytest.raises(ValueError, match="Token missing sub claim"):
            _resolve_user_from_bearer(f"Bearer {token}", db_session)


# ---------------------------------------------------------------------------
# MCPAuthMiddleware integration tests
# ---------------------------------------------------------------------------
# We build a minimal Starlette app with the middleware attached and a single
# route that echoes 200 "ok". Tests drive it through TestClient.
# ---------------------------------------------------------------------------


def _make_app_with_middleware(db_session):
    """Tiny ASGI app with MCPAuthMiddleware using the test DB session."""
    from app.core.database import get_db

    inner = FastAPI()

    @inner.get("/probe")
    def probe():
        return PlainTextResponse("ok")

    # Override get_db so the middleware resolves users from the test DB
    def override_get_db():
        yield db_session

    inner.dependency_overrides[get_db] = override_get_db

    # The middleware calls get_db() as a context manager internally via
    # app.mcp.db.get_db, not FastAPI's dependency system. We patch that
    # module-level function in the middleware's module.
    inner.add_middleware(MCPAuthMiddleware)
    return inner


class TestMCPAuthMiddleware:
    def test_missing_auth_header_returns_401(self, db_session, auth_user):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/probe")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Missing Authorization header"

    def test_missing_auth_header_includes_www_authenticate(self, db_session, auth_user):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/probe")
        assert "WWW-Authenticate" in resp.headers
        assert "Bearer" in resp.headers["WWW-Authenticate"]

    def test_invalid_token_returns_401(self, db_session, auth_user):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/probe", headers={"Authorization": "Bearer bad.token.here"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_token"

    def test_expired_token_returns_401(self, db_session, auth_user, expired_token):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get(
                "/probe",
                headers={"Authorization": f"Bearer {expired_token}"},
            )
        assert resp.status_code == 401

    def test_valid_token_passes_through(self, db_session, auth_user, valid_token):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get(
                "/probe",
                headers={"Authorization": f"Bearer {valid_token}"},
            )
        assert resp.status_code == 200

    def test_www_authenticate_contains_resource_metadata_url(
        self, db_session, auth_user
    ):
        from contextlib import contextmanager

        @contextmanager
        def fake_get_db():
            yield db_session

        app = _make_app_with_middleware(db_session)
        with patch("app.mcp.http_server.get_db", fake_get_db):
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/probe")
        www_auth = resp.headers.get("WWW-Authenticate", "")
        assert "resource_metadata" in www_auth
