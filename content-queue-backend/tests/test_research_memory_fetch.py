"""_fetch_research_memory against the real test DB (psycopg 3) with seeded rows."""

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.conftest import SQLALCHEMY_TEST_DATABASE_URL
from app.models.research import ResearchRun
from app.models.research_memory import ResearchMemory
from app.tasks.research import _fetch_research_memory


@pytest.fixture
def pg3_session():
    """Production runs psycopg 3 (app/core/database.py rewrites the URL); the
    shared test engine resolves to psycopg2, so build a psycopg 3 session."""
    url = SQLALCHEMY_TEST_DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = create_engine(url)
    assert engine.dialect.driver == "psycopg"
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _vec(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(1536)]


def _run(db, user):
    run = ResearchRun(
        user_id=user.id,
        question="q",
        mode="deep",
        status="done",
        budget={"max_tokens": 1, "max_llm_calls": 1, "timeout_s": 1},
    )
    db.add(run)
    db.commit()
    return run


def _mem(db, user, run, sub_q, emb, age_days=0):
    row = ResearchMemory(
        user_id=user.id,
        run_id=run.id,
        sub_question=sub_q,
        topic_embedding=emb,
        coverage="full",
        topic_summary="s",
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    db.add(row)
    db.commit()
    return row


def test_returns_similar_recent_rows(db_session, pg3_session, test_user):
    run = _run(db_session, test_user)
    _mem(db_session, test_user, run, "match", _vec(1))
    _mem(db_session, test_user, run, "unrelated", _vec(2))
    _mem(db_session, test_user, run, "too old", _vec(1), age_days=400)

    rows = _fetch_research_memory(
        test_user.id, _vec(1), pg3_session, k=5, max_age_days=90
    )

    assert [r.sub_question for r in rows] == ["match"]


def test_query_failure_is_logged_at_warning(caplog):
    db = MagicMock()
    db.execute.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.WARNING, logger="app.tasks.research"):
        assert _fetch_research_memory(uuid.uuid4(), _vec(1), db) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
