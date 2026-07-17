"""Tests for UserMemoryEvent and UserProfile models (Step 1.1)."""

import pytest

from app.models.memory import UserMemoryEvent, UserProfile, ReadingVelocity
from app.models.user import User
from app.core.security import get_password_hash


class TestUserMemoryModels:
    def test_can_create_memory_event(self, db_session, test_user):
        event = UserMemoryEvent(
            user_id=test_user.id,
            event_type="deep_read",
            metadata_={"highlight_count": 5},
        )
        db_session.add(event)
        db_session.commit()
        assert event.id is not None
        assert event.occurred_at is not None

    def test_memory_event_requires_user_id(self, db_session):
        with pytest.raises(Exception):
            db_session.add(UserMemoryEvent(event_type="deep_read"))
            db_session.commit()
        db_session.rollback()

    def test_can_upsert_user_profile(self, db_session, test_user):
        profile = UserProfile(
            user_id=test_user.id,
            current_focus="agent evaluation design",
            reading_velocity=ReadingVelocity.deep,
        )
        db_session.merge(profile)
        db_session.commit()
        fetched = db_session.get(UserProfile, test_user.id)
        assert fetched.current_focus == "agent evaluation design"

    def test_profile_upsert_overwrites_fields(self, db_session, test_user):
        db_session.merge(UserProfile(user_id=test_user.id, current_focus="topic A"))
        db_session.commit()
        db_session.merge(UserProfile(user_id=test_user.id, current_focus="topic B"))
        db_session.commit()
        fetched = db_session.get(UserProfile, test_user.id)
        assert fetched.current_focus == "topic B"

    def test_user_scoped_events_not_visible_across_users(self, db_session, test_user):
        other = User(
            email="other@test.com",
            username="other_memory_test",
            hashed_password=get_password_hash("x"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        db_session.add(UserMemoryEvent(user_id=other.id, event_type="deep_read"))
        db_session.commit()

        my_events = (
            db_session.query(UserMemoryEvent).filter_by(user_id=test_user.id).all()
        )
        assert len(my_events) == 0

    def test_reading_velocity_enum_stored_correctly(self, db_session, test_user):
        db_session.merge(
            UserProfile(user_id=test_user.id, reading_velocity=ReadingVelocity.fast)
        )
        db_session.commit()
        fetched = db_session.get(UserProfile, test_user.id)
        assert fetched.reading_velocity == ReadingVelocity.fast

    def test_memory_event_with_content_item(self, db_session, test_user, test_content):
        event = UserMemoryEvent(
            user_id=test_user.id,
            event_type="deep_read",
            content_item_id=test_content.id,
            metadata_={"word_count": 500},
        )
        db_session.add(event)
        db_session.commit()
        fetched = (
            db_session.query(UserMemoryEvent).filter_by(user_id=test_user.id).first()
        )
        assert fetched.content_item_id == test_content.id
