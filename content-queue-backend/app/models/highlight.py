from sqlalchemy import Column, String, Integer, Text, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
import uuid

from app.core.database import Base


class Highlight(Base):
    __tablename__ = "highlights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    text = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    start_offset = Column(Integer, nullable=False)
    end_offset = Column(Integer, nullable=False)
    color = Column(String(20), default="yellow", nullable=False)
    embedding = Column(
        Vector(1536), nullable=True
    )  # AI embedding for connection search
    search_vector = Column(TSVECTOR, nullable=True)  # Full-text search over text + note
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    # Relationships
    content_item = relationship("ContentItem", back_populates="highlights")
    user = relationship("User", back_populates="highlights")
