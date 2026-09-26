"""Model for persistent per-sub-question research memory."""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.sql import func

from app.core.database import Base


class ResearchMemory(Base):
    __tablename__ = "research_memory"
    __table_args__ = (
        Index(
            "ix_research_memory_embedding",
            "topic_embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"topic_embedding": "vector_cosine_ops"},
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sub_question = Column(Text, nullable=False)
    topic_embedding = Column(Vector(1536), nullable=True)
    coverage = Column(Text, nullable=False)  # "full" | "partial" | "none"
    topic_summary = Column(
        Text, nullable=True
    )  # what the library said (full/partial only)
    gap_description = Column(Text, nullable=True)  # what was missing (none only)
    source_item_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
