"""Base task classes used across all Celery tasks."""

import re
from celery import Task
from sqlalchemy.orm import Session
from app.core.database import SessionLocal


def html_to_plain(html: str) -> str:
    """
    Strip HTML tags and collapse whitespace, returning plain text.
    Used by embedding and tagging tasks so they operate on actual content
    rather than HTML markup — applies to all paths (trafilatura, extension, PDF).
    """
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


class DatabaseTask(Task):
    """
    Base task that provides a database session.
    Automatically closes session after task completes.
    """

    _db: Session = None

    def after_return(self, *args, **kwargs):
        if self._db is not None:
            self._db.close()
            self._db = None

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db
