"""
Pytest configuration and fixtures for testing.

This module provides reusable test fixtures for database setup,
test client creation, and authenticated user sessions.

Note: Tests use PostgreSQL (not SQLite) because the ContentItem model
uses PostgreSQL-specific features (ARRAY, Vector types).
"""

import pytest
import os
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

import app.models  # noqa: F401 — ensures all models register with Base before create_all
from app.main import app
from app.core.database import Base, get_db
from app.models.user import User
from app.models.content import ContentItem
from app.core.security import get_password_hash, create_access_token


# Use PostgreSQL test database
# Make sure you have a test database created: createdb content_queue_test
SQLALCHEMY_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue_test",
)

# Use a small QueuePool (not NullPool). NullPool opens a new TCP connection per
# session — with slow bcrypt hashing and multiple open sessions in flight,
# Postgres row/table locks accumulate and the next test's DELETE blocks forever.
engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)


# Enable pgvector extension for testing
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Enable pgvector extension when connecting to test database"""
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
    """
    Create tables once for the entire test session.
    """
    # Create tables
    Base.metadata.create_all(bind=engine)
    # Install search_vector trigger (not created by create_all — trigger lives in migration)
    from sqlalchemy import text as sa_text

    with engine.connect() as conn:
        conn.execute(sa_text(_SEARCH_VECTOR_TRIGGER_SQL))
        conn.commit()
    yield
    # Drop all at the end of session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(setup_database):
    """
    Create a fresh database session for each test.
    Uses DELETE FROM on setup to avoid TRUNCATE lock contention.
    """
    from sqlalchemy import text

    session = TestingSessionLocal()
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(text(f"DELETE FROM {table.name};"))
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_session):
    """
    Create a test client with dependency injection.

    The `get_db` override opens a fresh session per request (not the same
    db_session object) so that endpoint commits don't contend with the
    test's own open write transaction.

    db_session is still available for test assertions — it shares the same
    underlying engine and sees committed rows.
    """

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    # Dispose pool immediately after TestClient exits so all endpoint sessions
    # are fully closed. The next test's db_session will then get a fresh
    # connection with no lingered row locks.
    engine.dispose()


@pytest.fixture(scope="function")
def test_user(db_session):
    """
    Create a test user in the database.

    This user can be used for authentication tests and
    creating content/highlights.

    Args:
        db_session: The test database session fixture

    Returns:
        User: A test user object with email test@example.com
    """
    user = User(
        email="testuser@example.com",
        username="testuser",
        hashed_password=get_password_hash("testpassword"),
        full_name="Test User",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(scope="function")
def auth_headers(test_user):
    """
    Generate authentication headers for API requests.

    Creates a JWT token for the test user and returns
    properly formatted authorization headers.

    Args:
        test_user: The test user fixture

    Returns:
        dict: Headers with Bearer token authentication
    """
    access_token = create_access_token(data={"sub": test_user.email})
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture(scope="function")
def test_content(db_session, test_user):
    """
    Create a test content item for highlight testing.

    This provides a sample article that highlights can be
    attached to during tests.

    Args:
        db_session: The test database session fixture
        test_user: The test user fixture

    Returns:
        ContentItem: A test content item with sample text
    """
    content = ContentItem(
        original_url="https://example.com/article",
        title="Test Article",
        author="Test Author",
        full_text="This is a test article with enough content to highlight. " * 10,
        user_id=test_user.id,
        processing_status="completed",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content
