from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(50), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True)
    is_public = Column(Boolean, default=False, index=True)
    is_queue_public = Column(Boolean, default=False)
    is_crates_public = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    reading_patterns = Column(
        JSONB, default=dict
    )  # Tracks: avg_reading_time, preferred_times, tag_preferences
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    highlights = relationship(
        "Highlight",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tokens = relationship(
        "VerificationToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    content_items = relationship(
        "ContentItem",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    vinyl_records = relationship(
        "VinylRecord",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
