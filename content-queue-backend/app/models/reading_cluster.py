import uuid
from sqlalchemy import Column, Text, DateTime, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class ReadingCluster(Base):
    __tablename__ = "reading_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(Text, nullable=False)
    article_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    tag_labels = Column(ARRAY(Text), nullable=False, default=list)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
