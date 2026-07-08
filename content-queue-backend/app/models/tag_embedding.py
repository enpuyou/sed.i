"""TagEmbedding — global label→vector lookup table for semantic tag similarity."""

import uuid
from sqlalchemy import Column, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.core.database import Base


class TagEmbedding(Base):
    __tablename__ = "tag_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label = Column(Text, nullable=False, unique=True)
    embedding = Column(Vector(1536), nullable=False)
    # 'domain' | 'concept' | None (legacy rows before the split was introduced)
    tag_type = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
