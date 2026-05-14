"""
Integration tests for the auth API endpoints.

Covers:
- POST /auth/register (success, duplicate email, Celery tasks mocked)
- POST /auth/login (success, wrong password, unknown email)
- GET /auth/me (authenticated, unauthenticated)
- DELETE /auth/me (success, wrong password, unauthenticated, cascade)
"""

from unittest.mock import patch


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


def test_register_success(client):
    """Registering a new user returns 201 and user data."""
    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.discogs.fetch_discogs_metadata.delay"),
        patch("app.api.auth.send_verification_email_task.delay"),
    ):
        response = client.post(
            "/auth/register",
            json={
                "email": "newuser@example.com",
                "username": "newuser",
                "password": "securepass123",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert "id" in data
    assert "hashed_password" not in data  # Never leak password hash


def test_register_creates_onboarding_content(client, db_session):
    """Registration creates the welcome guide article, highlights, example articles, and vinyl."""
    from app.models.content import ContentItem
    from app.models.highlight import Highlight
    from app.models.vinyl import VinylRecord
    from app.models.user import User

    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.discogs.fetch_discogs_metadata.delay"),
        patch("app.api.auth.send_verification_email_task.delay"),
    ):
        response = client.post(
            "/auth/register",
            json={
                "email": "onboard@example.com",
                "username": "onboarder",
                "password": "securepass123",
            },
        )
    assert response.status_code == 201
    user_id = response.json()["id"]

    user = db_session.query(User).filter(User.id == user_id).first()

    # Should have guide article + 2 example articles = 3 content items
    items = db_session.query(ContentItem).filter(ContentItem.user_id == user.id).all()
    assert len(items) >= 3

    # Guide article should be completed immediately (no extraction needed)
    guide = next((i for i in items if i.processing_status == "completed"), None)
    assert guide is not None
    assert "Getting Started" in (guide.title or "")

    # Should have demo highlights on guide article
    highlights = db_session.query(Highlight).filter(Highlight.user_id == user.id).all()
    assert len(highlights) >= 1

    # Should have default vinyl record
    vinyl = db_session.query(VinylRecord).filter(VinylRecord.user_id == user.id).first()
    assert vinyl is not None
    assert vinyl.processing_status == "pending"


def test_register_duplicate_email_returns_400(client):
    """Registering with an already-taken email returns 400."""
    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.discogs.fetch_discogs_metadata.delay"),
        patch("app.api.auth.send_verification_email_task.delay"),
    ):
        client.post(
            "/auth/register",
            json={
                "email": "dup@example.com",
                "username": "dupuser",
                "password": "pass1",
            },
        )
        response = client.post(
            "/auth/register",
            json={
                "email": "dup@example.com",
                "username": "dupuser2",
                "password": "pass2",
            },
        )

    assert response.status_code == 400
    assert "already registered" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_success_returns_token(client):
    """Correct credentials return a JWT access token."""
    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.discogs.fetch_discogs_metadata.delay"),
        patch("app.api.auth.send_verification_email_task.delay"),
    ):
        client.post(
            "/auth/register",
            json={
                "email": "login@example.com",
                "username": "loginuser",
                "password": "mypassword",
            },
        )

    response = client.post(
        "/auth/login",
        data={"username": "login@example.com", "password": "mypassword"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert len(data["access_token"]) > 10


def test_login_wrong_password_returns_401(client):
    """Wrong password returns 401."""
    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.discogs.fetch_discogs_metadata.delay"),
        patch("app.api.auth.send_verification_email_task.delay"),
    ):
        client.post(
            "/auth/register",
            json={
                "email": "wrongpw@example.com",
                "username": "wrongpw",
                "password": "realpassword",
            },
        )

    response = client.post(
        "/auth/login",
        data={"username": "wrongpw@example.com", "password": "WRONGPASSWORD"},
    )
    assert response.status_code == 401


def test_login_unknown_email_returns_401(client):
    """Unknown email returns 401 (not 404 — avoid user enumeration)."""
    response = client.post(
        "/auth/login",
        data={"username": "nobody@example.com", "password": "anything"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


def test_get_me_returns_current_user(client, auth_headers, test_user):
    """GET /auth/me returns the authenticated user's data."""
    response = client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == test_user.email
    assert "hashed_password" not in data


def test_get_me_unauthenticated_returns_401(client):
    """GET /auth/me without token returns 401."""
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_get_me_tampered_token_returns_401(client):
    """GET /auth/me with a fake/tampered token returns 401."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer this.is.not.a.real.token"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /auth/me
# ---------------------------------------------------------------------------


def test_delete_account_success(client, test_user, auth_headers, db_session):
    """Correct password deletes the account and returns 204."""
    from app.models.user import User

    response = client.request(
        "DELETE",
        "/auth/me",
        json={"password": "testpassword"},
        headers=auth_headers,
    )
    assert response.status_code == 204
    assert db_session.query(User).filter(User.id == test_user.id).first() is None


def test_delete_account_wrong_password_returns_400(client, auth_headers):
    """Wrong password returns 400 and account is not deleted."""
    response = client.request(
        "DELETE",
        "/auth/me",
        json={"password": "wrongpassword"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "incorrect password" in response.json()["detail"].lower()


def test_delete_account_unauthenticated_returns_401(client):
    """DELETE /auth/me without a token returns 401."""
    response = client.request("DELETE", "/auth/me", json={"password": "anything"})
    assert response.status_code == 401


def test_delete_account_cascades_content(client, test_user, auth_headers, db_session):
    """Deleting account removes all associated content items, highlights, and vinyl records."""
    from app.models.content import ContentItem
    from app.models.highlight import Highlight
    from app.models.vinyl import VinylRecord

    # Seed content item + highlight
    item = ContentItem(
        original_url="https://example.com/article",
        title="To be deleted",
        full_text="Some text " * 20,
        user_id=test_user.id,
        processing_status="completed",
    )
    db_session.add(item)
    db_session.flush()

    highlight = Highlight(
        content_item_id=item.id,
        user_id=test_user.id,
        text="highlight text",
        start_offset=0,
        end_offset=14,
    )
    db_session.add(highlight)

    vinyl = VinylRecord(
        discogs_url="https://www.discogs.com/release/12345",
        user_id=test_user.id,
    )
    db_session.add(vinyl)
    db_session.commit()

    # Capture id before the DELETE invalidates the SQLAlchemy identity map entry
    user_id = test_user.id

    response = client.request(
        "DELETE",
        "/auth/me",
        json={"password": "testpassword"},
        headers=auth_headers,
    )
    assert response.status_code == 204

    # All child rows must be gone
    assert (
        db_session.query(ContentItem).filter(ContentItem.user_id == user_id).count()
        == 0
    )
    assert db_session.query(Highlight).filter(Highlight.user_id == user_id).count() == 0
    assert (
        db_session.query(VinylRecord).filter(VinylRecord.user_id == user_id).count()
        == 0
    )


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


def test_login_returns_refresh_token(client, test_user):
    """Login response includes a refresh_token alongside the access_token."""
    resp = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["refresh_token"] is not None


def test_refresh_issues_new_token_pair(client, test_user):
    """POST /auth/refresh returns a new access + refresh token."""
    login = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    )
    refresh_token = login.json()["refresh_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    # Rotated — new token must differ from old
    assert data["refresh_token"] != refresh_token


def test_refresh_rotates_old_token(client, test_user):
    """Using a refresh token a second time (replay) returns 401."""
    login = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    )
    old_token = login.json()["refresh_token"]

    client.post("/auth/refresh", json={"refresh_token": old_token})

    # Replaying the consumed token must fail
    replay = client.post("/auth/refresh", json={"refresh_token": old_token})
    assert replay.status_code == 401


def test_refresh_invalid_token_rejected(client):
    """Random string is not a valid refresh token."""
    resp = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 401


def test_logout_revokes_refresh_token(client, test_user):
    """After logout, the refresh token can no longer be used."""
    login = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    )
    refresh_token = login.json()["refresh_token"]

    logout = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert logout.status_code == 204

    # Revoked token must no longer work
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 401


def test_logout_no_token_is_noop(client):
    """Logout with no token returns 204 without error."""
    resp = client.post("/auth/logout", json={})
    assert resp.status_code == 204
