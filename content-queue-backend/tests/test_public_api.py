"""Public (unauthenticated) profile routes must not leak owner internals."""

from app.models.content import ContentItem
from app.models.user import User
from app.models.vinyl import VinylRecord


def _user(db, **kw):
    u = User(
        email=kw.pop("email", "owner@example.com"),
        username=kw.pop("username", "owner"),
        hashed_password="x",
        full_name="Owner Name",
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_public_profile_exact_keys_no_email(client, db_session):
    _user(db_session, is_public=True)
    r = client.get("/public/u/owner")
    assert r.status_code == 200
    assert set(r.json()) == {"username", "full_name"}
    assert "owner@example.com" not in r.text


def test_private_and_missing_profile_indistinguishable(client, db_session):
    _user(db_session, is_public=False)
    private = client.get("/public/u/owner")
    missing = client.get("/public/u/nobody")
    assert private.status_code == missing.status_code == 404
    assert private.json() == missing.json() == {"detail": "Profile not found"}


def test_public_content_hides_owner_id_and_errors(client, db_session):
    u = _user(db_session, is_public=True, is_queue_public=True)
    item = ContentItem(
        user_id=u.id,
        original_url="https://example.com/a",
        title="A",
        is_public=True,
        processing_status="completed",
        processing_error="internal boom",
    )
    db_session.add(item)
    db_session.commit()
    listing = client.get("/public/u/owner/content").json()["items"][0]
    detail = client.get(f"/public/u/owner/content/{item.id}").json()
    for body in (listing, detail):
        assert "user_id" not in body
        assert "processing_error" not in body
        assert body["title"] == "A"


def test_public_vinyl_hides_owner_id_and_errors(client, db_session):
    u = _user(db_session, is_crates_public=True)
    db_session.add(
        VinylRecord(
            user_id=u.id,
            discogs_url="https://discogs.com/release/1",
            title="T",
            processing_status="completed",
        )
    )
    db_session.commit()
    rec = client.get("/public/u/owner/vinyl").json()[0]
    assert "user_id" not in rec
    assert "processing_error" not in rec
    assert rec["title"] == "T"
