"""
TDD tests for /search/{id}/similar shared_tags and /search/telemetry.

Behaviors tested:
- /search/{id}/similar response includes shared_tags list on every result
- shared_tags contains intersection of source and result tags
- shared_tags is [] (not missing) when no overlap exists
- Cross-user isolation: user A cannot see user B's results
- POST /search/telemetry returns 204 for valid payload
- POST /search/telemetry requires auth
"""

from app.models.content import ContentItem


class TestFindSimilarSharedTags:
    def _make_item(self, db, user, url, tags, embedding_val=0.9):
        item = ContentItem(
            original_url=url,
            title=f"Article {url}",
            user_id=user.id,
            processing_status="completed",
            tags=tags,
            embedding=[embedding_val] * 1536,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_response_includes_shared_tags_key(
        self, client, auth_headers, db_session, test_user
    ):
        """Every result has a shared_tags field (even if empty)."""
        source = self._make_item(
            db_session, test_user, "https://example.com/source", ["ai safety"]
        )
        self._make_item(
            db_session, test_user, "https://example.com/other", ["ai safety"], 0.85
        )

        resp = client.get(f"/search/{source.id}/similar", headers=auth_headers)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1
        for r in results:
            assert "shared_tags" in r
            assert isinstance(r["shared_tags"], list)

    def test_shared_tags_contains_overlapping_tags(
        self, client, auth_headers, db_session, test_user
    ):
        """shared_tags lists tags present on both source and result."""
        source = self._make_item(
            db_session,
            test_user,
            "https://example.com/src2",
            ["deceptive alignment", "reward misspecification"],
        )
        self._make_item(
            db_session,
            test_user,
            "https://example.com/res2",
            ["deceptive alignment", "mesa-optimization"],
            0.85,
        )

        resp = client.get(f"/search/{source.id}/similar", headers=auth_headers)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1
        assert "deceptive alignment" in results[0]["shared_tags"]
        assert "mesa-optimization" not in results[0]["shared_tags"]

    def test_shared_tags_empty_when_no_overlap(
        self, client, auth_headers, db_session, test_user
    ):
        """shared_tags is [] not null when there is no tag overlap."""
        source = self._make_item(
            db_session, test_user, "https://example.com/src3", ["ai safety"]
        )
        self._make_item(
            db_session,
            test_user,
            "https://example.com/res3",
            ["distributed systems"],
            0.85,
        )

        resp = client.get(f"/search/{source.id}/similar", headers=auth_headers)
        assert resp.status_code == 200
        for r in resp.json():
            assert r["shared_tags"] == []

    def test_cross_user_isolation(self, client, db_session, test_user):
        """User A cannot retrieve User B's articles via similar."""
        from app.models.user import User
        from app.core.security import get_password_hash, create_access_token
        import uuid

        user_b = User(
            email=f"b_{uuid.uuid4()}@test.com",
            username=f"user_b_{uuid.uuid4().hex[:6]}",
            hashed_password=get_password_hash("pw"),
        )
        db_session.add(user_b)
        db_session.commit()

        source = self._make_item(
            db_session, test_user, "https://example.com/src-iso", []
        )
        self._make_item(db_session, user_b, "https://example.com/other-user", [], 0.85)

        token = create_access_token(data={"sub": test_user.email})
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get(f"/search/{source.id}/similar", headers=headers)
        assert resp.status_code == 200
        result_urls = [r["item"]["original_url"] for r in resp.json()]
        assert "https://example.com/other-user" not in result_urls


class TestSearchTelemetry:
    def test_requires_auth(self, client):
        resp = client.post(
            "/search/telemetry",
            json={
                "surface": "find_related",
                "item_id": "00000000-0000-0000-0000-000000000001",
                "action": "click",
            },
        )
        assert resp.status_code == 401

    def test_returns_204_for_valid_payload(self, client, auth_headers):
        resp = client.post(
            "/search/telemetry",
            json={
                "surface": "find_related",
                "item_id": "00000000-0000-0000-0000-000000000001",
                "shared_tag": "deceptive alignment",
                "action": "click",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_accepts_null_shared_tag(self, client, auth_headers):
        resp = client.post(
            "/search/telemetry",
            json={
                "surface": "find_related",
                "item_id": "00000000-0000-0000-0000-000000000001",
                "action": "click",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 204
