"""Tests for GET /memory/profile and POST /memory/consolidate."""

from unittest.mock import patch

import pytest

from app.models.memory import UserProfile, ReadingVelocity
from app.tasks.memory import ConsolidationResult, consolidate_memory


@pytest.fixture
def articles(db_session, test_user):
    from app.models.content import ContentItem

    items = []
    for i, title in enumerate(["Article A", "Article B", "Article C"]):
        item = ContentItem(
            original_url=f"https://example.com/mem-api-{i}",
            title=title,
            user_id=test_user.id,
            processing_status="completed",
        )
        db_session.add(item)
        items.append(item)
    db_session.commit()
    return items


class TestMemoryProfileAPI:
    def test_get_profile_404_when_none(self, client, auth_headers):
        resp = client.get("/memory/profile", headers=auth_headers)
        assert resp.status_code == 404
        assert "No memory profile" in resp.json()["detail"]

    def test_get_profile_returns_data_after_consolidation(
        self, client, auth_headers, db_session, test_user, articles
    ):
        with patch("app.tasks.memory.llm_client") as mock:
            mock.structured_chat.return_value = ConsolidationResult(
                current_focus="distributed systems",
                reading_velocity="deep",
                memory_text="User is studying distributed systems deeply.",
            )
            consolidate_memory(str(test_user.id), db=db_session)

        resp = client.get("/memory/profile", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_focus"] == "distributed systems"
        assert data["reading_velocity"] == "deep"
        assert "distributed systems" in data["memory_text"]
        assert data["last_consolidated"] is not None

    def test_consolidate_queues_task(self, client, auth_headers):
        with patch("app.tasks.memory.consolidate_memory_task") as mock_task:
            mock_task.delay.return_value = None
            resp = client.post("/memory/consolidate", headers=auth_headers)
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"
        mock_task.delay.assert_called_once()
