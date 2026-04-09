"""
Module-scoped fixtures for eval tests.

Evals need articles to persist across all test methods in a class,
so we use module scope instead of function scope.
"""

import pytest
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

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
