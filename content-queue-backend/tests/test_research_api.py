"""
Tests for GET /research/{run_id} — research run status endpoint.
"""

import uuid

import pytest

from app.core.security import get_password_hash
from app.models.research import ResearchRun
from app.models.user import User

DEFAULT_BUDGET = {
    "max_tokens": 10000,
    "max_iterations": 3,
    "max_subagents": 5,
    "timeout_s": 300,
}

DONE_RESULT = {
    "summary": "Found 3 articles.",
    "sub_question_findings": [],
    "cross_cutting_tensions": [],
    "gaps": [],
    "engagement_note": "",
    "confidence": "medium",
}


@pytest.fixture
def other_user(db_session):
    user = User(
        email="other@test.com",
        username="otheruser",
        hashed_password=get_password_hash("x"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class TestResearchRunStatus:
    def test_returns_status_for_own_run(
        self, client, auth_headers, db_session, test_user
    ):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="queued",
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_returns_404_for_unknown_id(self, client, auth_headers):
        resp = client.get(f"/research/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404

    def test_cannot_read_other_users_run(
        self, client, auth_headers, db_session, other_user
    ):
        run = ResearchRun(
            user_id=other_user.id,
            question="secret",
            mode="deep",
            status="done",
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code in (403, 404)

    def test_returns_result_when_done(
        self, client, auth_headers, db_session, test_user
    ):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="done",
            result=DONE_RESULT,
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["result"]["summary"] == "Found 3 articles."

    def test_result_has_research_brief_shape(
        self, client, auth_headers, db_session, test_user
    ):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="done",
            result=DONE_RESULT,
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        result = resp.json()["result"]
        assert "sub_question_findings" in result
        assert "gaps" in result
        assert "engagement_note" in result
        assert "perspectives" not in result
        assert "key_concepts" not in result

    def test_returns_progress_with_sub_questions(
        self, client, auth_headers, db_session, test_user
    ):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="searching",
            iteration_count=2,
            sub_questions=["What is X?", "What is Y?"],
            searches_run=[{"subagent_id": "a"}, {"subagent_id": "b"}],
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code == 200
        progress = resp.json()["progress"]
        assert progress["iteration"] == 2
        assert progress["searches_run_count"] == 2
        assert "What is X?" in progress["sub_questions"]

    def test_requires_auth(self, client, db_session, test_user):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="queued",
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}")
        assert resp.status_code == 401

    def test_returns_null_result_when_not_done(
        self, client, auth_headers, db_session, test_user
    ):
        run = ResearchRun(
            user_id=test_user.id,
            question="test",
            mode="deep",
            status="planning",
            budget=DEFAULT_BUDGET,
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get(f"/research/{run.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["result"] is None
