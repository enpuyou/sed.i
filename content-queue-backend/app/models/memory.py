"""
Models for persistent user memory.

user_memory_events — episodic memory: specific reading/writing events
user_profiles      — semantic + procedural memory: extracted facts about the user
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class ReadingVelocity(str, enum.Enum):
    fast = "fast"
    deep = "deep"
    browsing = "browsing"


class UserMemoryEvent(Base):
    __tablename__ = "user_memory_events"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "event_type",
            "content_item_id",
            name="uq_memory_event_day",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(Text, nullable=False)
    content_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_ = Column("metadata", JSONB, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_focus = Column(Text, nullable=True)
    reading_velocity = Column(
        SAEnum(ReadingVelocity, name="reading_velocity_enum", create_constraint=True),
        nullable=True,
    )
    memory_text = Column(Text, nullable=True)  # free-form prose, LLM-managed
    persistent_gaps = Column(
        Text, nullable=True
    )  # recurring research gaps, LLM-managed
    last_consolidated = Column(DateTime(timezone=True), nullable=True)
