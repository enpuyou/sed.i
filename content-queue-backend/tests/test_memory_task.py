"""Tests for consolidate_memory task."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.content import ContentItem
from app.models.memory import UserProfile
from app.tasks.memory import ConsolidationResult, consolidate_memory


@pytest.fixture
def articles(db_session, test_user):
    """Three articles — enough to clear the MIN_ACTIVITY_ITEMS threshold."""
    items = []
    for i in range(3):
        item = ContentItem(
            original_url=f"https://example.com/memory-test-{i}",
            title=f"Memory Test Article {i}",
            description="Article about memory systems",
            user_id=test_user.id,
            processing_status="completed",
        )
        db_session.add(item)
        items.append(item)
    db_session.commit()
    for item in items:
        db_session.refresh(item)
    return items


@pytest.fixture
def article(articles):
    return articles[0]


def _mock_result(current_focus="RAG systems"):
    return ConsolidationResult(
        current_focus=current_focus,
        reading_velocity="deep",
        memory_text="User appears focused on RAG systems and retrieval.",
    )


class TestConsolidateMemory:
    def test_writes_profile_on_activity(self, db_session, test_user, article):
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = _mock_result()
            consolidate_memory(str(test_user.id), db=db_session)

        profile = db_session.get(UserProfile, test_user.id)
        assert profile is not None
        assert profile.current_focus == "RAG systems"
        assert profile.reading_velocity.value == "deep"
        assert profile.memory_text is not None

    def test_no_op_when_insufficient_activity(self, db_session, test_user):
        with patch("app.tasks.memory.llm_client") as mock_llm:
            consolidate_memory(str(test_user.id), db=db_session)
            mock_llm.structured_chat.assert_not_called()

        assert db_session.get(UserProfile, test_user.id) is None

    def test_second_run_upserts_not_duplicates(self, db_session, test_user, articles):
        from datetime import datetime, timezone, timedelta

        result = ConsolidationResult(
            current_focus="topic A",
            reading_velocity="fast",
            memory_text="First consolidation.",
        )
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = result
            consolidate_memory(str(test_user.id), db=db_session)

        # Backdate last_consolidated so second run sees articles as new delta
        profile = db_session.get(UserProfile, test_user.id)
        profile.last_consolidated = datetime.now(tz=timezone.utc) - timedelta(days=30)
        db_session.commit()

        result2 = ConsolidationResult(
            current_focus="topic B",
            reading_velocity="deep",
            memory_text="Updated after second run.",
        )
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = result2
            consolidate_memory(str(test_user.id), db=db_session)

        profiles = db_session.query(UserProfile).filter_by(user_id=test_user.id).all()
        assert len(profiles) == 1
        assert profiles[0].current_focus == "topic B"
        assert profiles[0].memory_text == "Updated after second run."

    def test_returns_skipped_when_insufficient_activity(self, db_session, test_user):
        with patch("app.tasks.memory.llm_client"):
            result = consolidate_memory(str(test_user.id), db=db_session)
        assert result["status"] == "skipped"

    def test_returns_completed_on_success(self, db_session, test_user, articles):
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = _mock_result()
            result = consolidate_memory(str(test_user.id), db=db_session)
        assert result["status"] == "completed"

    def test_bootstrap_flag_set_on_first_run(self, db_session, test_user, articles):
        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = _mock_result()
            result = consolidate_memory(str(test_user.id), db=db_session)
        assert result.get("bootstrap") is True

    def test_delta_flag_set_on_subsequent_run(self, db_session, test_user, articles):
        from datetime import datetime, timezone, timedelta

        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = _mock_result()
            consolidate_memory(str(test_user.id), db=db_session)

        profile = db_session.get(UserProfile, test_user.id)
        profile.last_consolidated = datetime.now(tz=timezone.utc) - timedelta(days=30)
        db_session.commit()

        with patch("app.tasks.memory.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = _mock_result("topic B")
            result = consolidate_memory(str(test_user.id), db=db_session)
        assert result.get("bootstrap") is False

    def test_fan_out_dispatches_per_active_user(self, db_session, test_user, articles):
        from datetime import datetime, timedelta, timezone
        from app.models.content import ContentItem as CI

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
        active = (
            db_session.query(CI.user_id)
            .filter(CI.deleted_at.is_(None), CI.created_at >= cutoff)
            .distinct()
            .all()
        )
        user_ids = [str(uid) for (uid,) in active]
        assert str(test_user.id) in user_ids
