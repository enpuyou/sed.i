"""
Memory-bound behaviour of queries that touch a user's whole library.

Acceptance: the fallback duplicate check, /content/recommended and
/lists/{id}/content do not SELECT full_text, embedding or search_vector (the
~280 KB-per-row columns); /lists/{id}/content accepts skip/limit and, with no
parameters, still returns every item (existing clients); responses are
identical with and without the column deferral.
"""

from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.models.content import ContentItem
from app.models.list import List, content_list_membership
from app.services.content import find_existing_active_item

HEAVY = ("full_text", "embedding", "search_vector")


@contextmanager
def captured_sql():
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _item_selects(statements):
    return [
        s
        for s in statements
        if s.lstrip().upper().startswith("SELECT") and "FROM content_items" in s
    ]


def _assert_no_heavy_columns(statements):
    selects = _item_selects(statements)
    assert selects, "expected at least one content_items SELECT"
    for stmt in selects:
        select_list = stmt.split(" FROM ", 1)[0]
        for col in HEAVY:
            assert f"content_items.{col}" not in select_list, (col, select_list[:200])


def _make_items(db, user, n, **extra):
    items = []
    for i in range(n):
        items.append(
            ContentItem(
                user_id=user.id,
                title=f"Item {i}",
                original_url=f"https://example.com/mem-{i}",
                full_text="<p>" + "x" * 2000 + "</p>",
                embedding=[0.1] * 1536,
                is_read=False,
                processing_status="completed",
                **extra,
            )
        )
    db.add_all(items)
    db.commit()
    return items


class TestDeferredHeavyColumns:
    def test_duplicate_check_is_one_query_without_heavy_columns(
        self, db_session, test_user
    ):
        _make_items(db_session, test_user, 3)
        db_session.expire_all()

        with captured_sql() as statements:
            found = find_existing_active_item(
                db=db_session,
                user_id=test_user.id,
                normalized_url="https://example.com/not-saved-yet",
            )

        assert found is None
        _assert_no_heavy_columns(statements)
        assert len(_item_selects(statements)) == 1, "no library scan"

    def test_duplicate_check_still_finds_existing_item(self, db_session, test_user):
        items = _make_items(db_session, test_user, 2)
        db_session.expire_all()

        found = find_existing_active_item(
            db=db_session,
            user_id=test_user.id,
            normalized_url="https://example.com/mem-1",
        )

        assert found is not None
        assert found.id == items[1].id
        assert found.is_archived is False

    def test_recommended_skips_heavy_columns_and_returns_items(
        self, client, auth_headers, db_session, test_user
    ):
        _make_items(db_session, test_user, 4)
        db_session.expire_all()

        with captured_sql() as statements:
            response = client.get("/content/recommended", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["total"] == 4
        _assert_no_heavy_columns(statements)

    def test_list_content_skips_heavy_columns(
        self, client, auth_headers, db_session, test_user
    ):
        _, test_list = _make_list_with_items(db_session, test_user, 3)
        db_session.expire_all()

        with captured_sql() as statements:
            response = client.get(
                f"/lists/{test_list.id}/content", headers=auth_headers
            )

        assert response.status_code == 200
        assert len(response.json()) == 3
        _assert_no_heavy_columns(statements)


def _make_list_with_items(db, user, n):
    items = _make_items(db, user, n)
    test_list = List(name="Paged", owner_id=user.id)
    db.add(test_list)
    db.commit()
    db.refresh(test_list)
    for item in items:
        db.execute(
            content_list_membership.insert().values(
                content_item_id=item.id, list_id=test_list.id, added_by=user.id
            )
        )
    db.commit()
    return items, test_list


class TestListContentPagination:
    def test_no_params_returns_every_item(
        self, client, auth_headers, db_session, test_user
    ):
        _, test_list = _make_list_with_items(db_session, test_user, 7)

        response = client.get(f"/lists/{test_list.id}/content", headers=auth_headers)

        assert response.status_code == 200
        assert len(response.json()) == 7

    def test_limit_and_skip_page_without_overlap(
        self, client, auth_headers, db_session, test_user
    ):
        _, test_list = _make_list_with_items(db_session, test_user, 7)
        url = f"/lists/{test_list.id}/content"

        first = client.get(url, params={"limit": 3}, headers=auth_headers).json()
        second = client.get(
            url, params={"limit": 3, "skip": 3}, headers=auth_headers
        ).json()
        third = client.get(
            url, params={"limit": 3, "skip": 6}, headers=auth_headers
        ).json()

        ids = [i["id"] for page in (first, second, third) for i in page]
        assert [len(first), len(second), len(third)] == [3, 3, 1]
        assert len(set(ids)) == 7

    def test_limit_validated(self, client, auth_headers, db_session, test_user):
        _, test_list = _make_list_with_items(db_session, test_user, 1)
        url = f"/lists/{test_list.id}/content"

        assert (
            client.get(url, params={"limit": 0}, headers=auth_headers).status_code
            == 422
        )
        assert (
            client.get(url, params={"limit": 501}, headers=auth_headers).status_code
            == 422
        )
        assert (
            client.get(url, params={"skip": -1}, headers=auth_headers).status_code
            == 422
        )


class TestResponsesUnchangedByDeferral:
    """Backward compatibility: deferring the heavy columns must not change any
    response body. Each endpoint is called with deferral on and off (the helper
    patched to return no options, i.e. the old behaviour) and the JSON compared.
    """

    @staticmethod
    def _without_deferral():
        targets = (
            "app.services.content.defer_heavy_columns",
            "app.api.content.defer_heavy_columns",
            "app.api.lists.defer_heavy_columns",
        )
        return [patch(t, return_value=()) for t in targets]

    def _get_both(self, client, url, headers, params=None):
        with_defer = client.get(url, headers=headers, params=params)
        patches = self._without_deferral()
        for p in patches:
            p.start()
        try:
            without_defer = client.get(url, headers=headers, params=params)
        finally:
            for p in patches:
                p.stop()
        assert with_defer.status_code == without_defer.status_code == 200
        return with_defer.json(), without_defer.json()

    def test_recommended_response_identical(
        self, client, auth_headers, db_session, test_user
    ):
        _make_items(db_session, test_user, 6, tags=["a", "b"])

        with_defer, without_defer = self._get_both(
            client, "/content/recommended", auth_headers
        )

        assert with_defer == without_defer
        assert with_defer["total"] == 6

    def test_list_content_response_identical(
        self, client, auth_headers, db_session, test_user
    ):
        _, test_list = _make_list_with_items(db_session, test_user, 6)

        with_defer, without_defer = self._get_both(
            client, f"/lists/{test_list.id}/content", auth_headers
        )

        assert with_defer == without_defer
        assert len(with_defer) == 6

    def test_list_content_paged_response_identical(
        self, client, auth_headers, db_session, test_user
    ):
        _, test_list = _make_list_with_items(db_session, test_user, 6)

        with_defer, without_defer = self._get_both(
            client,
            f"/lists/{test_list.id}/content",
            auth_headers,
            params={"limit": 4, "skip": 1},
        )

        assert with_defer == without_defer
        assert len(with_defer) == 4

    def test_content_detail_still_returns_full_text(
        self, client, auth_headers, db_session, test_user
    ):
        """The reader endpoint is not deferred: full_text must still come back."""
        items = _make_items(db_session, test_user, 1)

        response = client.get(f"/content/{items[0].id}/full", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["full_text"].startswith("<p>")
