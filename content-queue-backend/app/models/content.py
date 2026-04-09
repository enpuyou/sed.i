from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    Integer,
    Text,
    ForeignKey,
    Float,
    ARRAY,
)
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
import uuid
from app.core.database import Base


class ContentItem(Base):
    __tablename__ = "content_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Original submission
    original_url = Column(Text, nullable=False)
    submitted_via = Column(String(50))  # 'web', 'email', 'api', 'extension'

    # Extracted metadata
    title = Column(Text)
    description = Column(Text)
    summary = Column(Text)  # AI-generated summary
    thumbnail_url = Column(Text)
    content_type = Column(
        String(50), index=True
    )  # 'article', 'video', 'pdf', 'tweet', 'unknown'
    author = Column(String(255))

    # Flexible Vertical Metadata Structure
    content_vertical = Column(
        String(50), default="general", server_default="general", index=True
    )
    vertical_metadata = Column(JSONB, default=dict, server_default="{}")

    tags = Column(ARRAY(String(100)), default=list)  # User-confirmed tags
    auto_tags = Column(ARRAY(String(100)), default=list)  # AI suggestions

    published_date = Column(DateTime(timezone=True))

    # Full content (extracted in background)
    full_text = Column(Text)
    word_count = Column(Integer)
    reading_time_minutes = Column(Integer)

    # Reading progress
    read_position = Column(Float, default=0.0, nullable=True)  # 0.0 to 1.0 (0% to 100%)

    # Full-text search vector (generated, stored) — weighted: title/author = A, description/tags = B
    # Full-text search vector — populated/updated by the tsvector_update trigger.
    # Weighted: title/author = A (high), description/tags = B.
    search_vector = Column(TSVECTOR, nullable=True)

    # ML embeddings (1536 dimensions for OpenAI text-embedding-3-small)
    embedding = Column(Vector(1536))

    # User interaction
    is_read = Column(Boolean, default=False, index=True)
    is_archived = Column(Boolean, default=False, index=True)
    is_public = Column(Boolean, default=False, index=True)
    read_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True), index=True)  # Soft delete

    # Processing status
    processing_status = Column(String(50), default="pending", index=True)
    processing_error = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="content_items")
    highlights = relationship(
        "Highlight", back_populates="content_item", cascade="all, delete-orphan"
    )
