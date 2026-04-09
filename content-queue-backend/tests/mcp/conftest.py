"""
Shared fixtures for MCP tool tests.

MCP tools take a (user, db) pair directly rather than going through HTTP,
so we reuse the existing test DB infrastructure and inject them explicitly.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import os

from app.core.database import Base
from app.models.user import User
from app.models.content import ContentItem
from app.models.list import List, content_list_membership
from app.models.highlight import Highlight
from app.models.draft import Draft
from app.core.security import get_password_hash


SQLALCHEMY_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue_test",
)

engine = create_engine(SQLALCHEMY_TEST_DATABASE_URL, poolclass=NullPool)


@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    with dbapi_conn.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        dbapi_conn.commit()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_SEARCH_VECTOR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION content_items_search_vector_update()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('simple',  COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.author, '')), 'A') ||
        setweight(to_tsvector('simple',  COALESCE(NEW.author, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
        setweight(to_tsvector('simple',  COALESCE(NEW.description, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(array_to_string(NEW.tags, ' '), '')), 'B') ||
        setweight(to_tsvector('simple',  COALESCE(array_to_string(NEW.tags, ' '), '')), 'B');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tsvector_update ON content_items;
CREATE TRIGGER tsvector_update
BEFORE INSERT OR UPDATE OF title, author, description, tags
ON content_items
FOR EACH ROW EXECUTE FUNCTION content_items_search_vector_update();
"""


@pytest.fixture(scope="session")
def setup_database():
    Base.metadata.create_all(bind=engine)
    # Install the search_vector trigger (not created by create_all)
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text(_SEARCH_VECTOR_TRIGGER_SQL))
        conn.commit()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db(setup_database):
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    # Start a nested transaction (SAVEPOINT)
    nested = connection.begin_nested()

    # If the application calls session.commit(), it will only commit the SAVEPOINT
    @event.listens_for(session, "after_transaction_end")
    def end_savepoint(session, transaction):
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
