"""Double-submit race: two concurrent saves of the same URL must yield one row
and a duplicate error (409 / 'exists'), never an unhandled IntegrityError."""

import threading
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.mcp.tools.write import add_content
from app.models.content import ContentItem
from app.services import content as content_service
from app.services.content import DuplicateContentError, ingest_url
from tests.conftest import TestingSessionLocal

URL = "https://example.com/race-article"


def _run_concurrently(fn, n=2):
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        s = TestingSessionLocal()
        try:
            barrier.wait()
            results[i] = fn(s)
        except Exception as e:  # noqa: BLE001
            results[i] = e
        finally:
            s.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return results


def _blind_find(monkeypatch):
    """Make the pre-check miss so both threads reach the INSERT (forces the race)."""
    real = content_service.find_existing_active_item
    calls = threading.local()

    def fake(**kw):
        if not getattr(calls, "seen", False):
            calls.seen = True
            return None
        return real(**kw)

    monkeypatch.setattr(content_service, "find_existing_active_item", fake)
    monkeypatch.setattr("app.mcp.tools.write.find_existing_active_item", fake)


def _count(db_session, user):
    db_session.expire_all()
    return db_session.query(ContentItem).filter_by(user_id=user.id).count()


def test_ingest_url_concurrent_one_row_one_duplicate(
    db_session, test_user, monkeypatch
):
    _blind_find(monkeypatch)
    with patch("app.tasks.extraction.extract_metadata.delay"):
        results = _run_concurrently(
            lambda s: ingest_url(
                url=URL, user=s.get(type(test_user), test_user.id), db=s
            )
        )
    dups = [r for r in results if isinstance(r, DuplicateContentError)]
    oks = [r for r in results if isinstance(r, ContentItem)]
    assert len(oks) == 1 and len(dups) == 1, results
    assert dups[0].existing_id == str(oks[0].id)
    assert _count(db_session, test_user) == 1


def test_ingest_url_integrity_error_becomes_duplicate(db_session, test_user):
    existing = ContentItem(
        user_id=test_user.id, original_url=URL, processing_status="pending"
    )
    db_session.add(existing)
    db_session.commit()
    calls = {"n": 0}
    real = content_service.find_existing_active_item

    def first_miss(**kw):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(**kw)

    with patch.object(content_service, "find_existing_active_item", first_miss):
        with pytest.raises(DuplicateContentError) as ei:
            ingest_url(url=URL, user=test_user, db=db_session)
    assert ei.value.existing_id == str(existing.id)


def test_ingest_url_reraises_integrity_error_without_duplicate(test_user, db_session):
    with patch.object(
        db_session, "commit", side_effect=IntegrityError("x", {}, Exception("y"))
    ):
        with pytest.raises(IntegrityError):
            ingest_url(url=URL, user=test_user, db=db_session)


def test_mcp_add_content_concurrent_exists(db_session, test_user, monkeypatch):
    _blind_find(monkeypatch)
    with patch("app.mcp.tools.write.process_url_task.delay"):
        results = _run_concurrently(
            lambda s: add_content(
                url=URL, user=s.get(type(test_user), test_user.id), db=s
            )
        )
    assert all(isinstance(r, dict) for r in results), results
    assert sorted(r["status"] for r in results) == ["exists", "queued"]
    assert len({r["item_id"] for r in results}) == 1
    assert _count(db_session, test_user) == 1
