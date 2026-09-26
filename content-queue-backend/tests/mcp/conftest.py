"""
Shared fixtures for MCP tool tests.

MCP tools take a (user, db) pair directly rather than going through HTTP,
so we reuse the root conftest engine (QueuePool) rather than creating a
second engine — two engines against the same test DB caused fixture races
and test-order-dependent failures (WP3 from test audit).
"""

import pytest

from app.models.user import User
from app.models.content import ContentItem
from app.models.list import List, content_list_membership
from app.models.highlight import Highlight
from app.models.draft import Draft
from app.core.security import get_password_hash

# Re-export root conftest fixtures so mcp tests see setup_database
from tests.conftest import engine, TestingSessionLocal, setup_database  # noqa: F401


@pytest.fixture(scope="function")
def db(setup_database):
    """
    Function-scoped DB session for MCP tests using SAVEPOINT rollback.

    Uses the same engine as the root conftest (no second engine).
    Each test gets a clean slate via SAVEPOINT rollback — no DELETE storm.
    """
    from sqlalchemy import event as sa_event

    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    nested = connection.begin_nested()

    @sa_event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def user(db):
    u = User(
        email="mcp@example.com",
        username="mcpuser",
        hashed_password=get_password_hash("password"),
        full_name="MCP User",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def other_user(db):
    u = User(
        email="other@example.com",
        username="otheruser",
        hashed_password=get_password_hash("password"),
        full_name="Other User",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def article(db, user):
    item = ContentItem(
        original_url="https://example.com/article",
        title="Test Article",
        description="A test article",
        summary="Summary of test article",
        full_text="<p>Full text content here.</p>",
        author="Test Author",
        word_count=100,
        reading_time_minutes=1,
        user_id=user.id,
        processing_status="completed",
        tags=["tech", "ai"],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def second_article(db, user):
    item = ContentItem(
        original_url="https://example.com/article2",
        title="Second Article",
        description="Another article",
        summary="Summary of second article",
        full_text="<p>Second article full text.</p>",
        word_count=200,
        reading_time_minutes=2,
        user_id=user.id,
        processing_status="completed",
        tags=["science"],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def reading_list(db, user):
    lst = List(name="My List", description="Test list", owner_id=user.id)
    db.add(lst)
    db.commit()
    db.refresh(lst)
    return lst


@pytest.fixture
def list_with_articles(db, user, reading_list, article, second_article):
    db.execute(
        content_list_membership.insert(),
        [
            {
                "content_item_id": article.id,
                "list_id": reading_list.id,
                "added_by": user.id,
            },
            {
                "content_item_id": second_article.id,
                "list_id": reading_list.id,
                "added_by": user.id,
            },
        ],
    )
    db.commit()
    return reading_list


@pytest.fixture
def highlight(db, user, article):
    h = Highlight(
        content_item_id=article.id,
        user_id=user.id,
        text="important highlighted text",
        note="my note",
        color="yellow",
        start_offset=10,
        end_offset=40,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@pytest.fixture
def draft(db, user, reading_list):
    d = Draft(
        list_id=reading_list.id,
        user_id=user.id,
        title="My Draft",
        content="# Draft\n\nSome content here.",
        word_count=5,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d
