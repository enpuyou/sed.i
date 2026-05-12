"""ContentChunk model — stores per-article embedding chunks for multi-vector search."""

from sqlalchemy import Column, Integer, Text, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class ContentChunk(Base):
    __tablename__ = "content_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    content_item = relationship("ContentItem", back_populates="chunks")
