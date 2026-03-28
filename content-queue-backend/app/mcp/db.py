"""
Database session management for the MCP server.

The MCP server is a long-lived process (unlike FastAPI's request-scoped sessions),
so we manage sessions explicitly per tool call using a context manager.
"""

from contextlib import contextmanager
from sqlalchemy.orm import Session
from app.core.database import SessionLocal


@contextmanager
def get_db() -> Session:
    """
    Yield a database session for a single tool call, then close it.

    Usage:
        with get_db() as db:
            results = db.query(MyModel).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
