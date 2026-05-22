"""
Fixtures for eval tests.

Module-scoped fixtures (db_module, user_module) keep articles seeded across
all methods in a class (needed for search/hybrid evals).

Function-scoped fixtures (db, user, other_user) give MCP contract tests a
clean slate per test — same pattern as the main conftest.
"""

import pytest
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.models.user import User
from app.core.security import get_password_hash

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


@pytest.fixture(scope="module")
def db_module(setup_database):
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def end_savepoint(session, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def user_module(db_module):
    u = User(
        email="eval@example.com",
        username="evaluser",
        hashed_password=get_password_hash("password"),
        full_name="Eval User",
        is_active=True,
    )
    db_module.add(u)
    db_module.commit()
    db_module.refresh(u)
    return u


# ── Function-scoped fixtures for MCP contract tests ──────────────────────────
# These mirror the main conftest pattern so MCP tests get a clean DB per test.


@pytest.fixture(scope="function")
def db(setup_database):
    from tests.conftest import TestingSessionLocal, engine

    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def end_savepoint(session, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def user(db):
    u = User(
        email="mcp_eval_user@example.com",
        username="mcp_eval_user",
        hashed_password=get_password_hash("password"),
        full_name="MCP Eval User",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(scope="function")
def other_user(db):
    u = User(
        email="mcp_eval_other@example.com",
        username="mcp_eval_other",
        hashed_password=get_password_hash("password"),
        full_name="Other MCP Eval User",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u
