"""Entity graph models — three tables for Feature A (knowledge graph entity index)."""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(Text, nullable=False)
    entity_type = Column(
        Text, nullable=False
    )  # PERSON | CONCEPT | ORGANIZATION | PAPER | TOOL
    description = Column(Text, nullable=True)
    article_count = Column(Integer, default=0, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Functional unique index on (user_id, lower(name)) — enforces case-insensitive
    # dedup at the DB layer. upsert_entity() queries by lower(name) and uses a
    # SAVEPOINT to handle the rare concurrent-insert race without wiping the outer tx.
    __table_args__ = (
        Index(
            "uq_entity_user_lower_name",
            "user_id",
            func.lower(name),
            unique=True,
        ),
    )

    mentions = relationship(
        "EntityMention", back_populates="entity", cascade="all, delete-orphan"
    )
    source_relations = relationship(
        "EntityRelation",
        foreign_keys="EntityRelation.source_entity_id",
        cascade="all, delete-orphan",
    )
    target_relations = relationship(
        "EntityRelation",
        foreign_keys="EntityRelation.target_entity_id",
        cascade="all, delete-orphan",
    )


class EntityMention(Base):
    __tablename__ = "entity_mentions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    context_text = Column(Text, nullable=True)
    weight = Column(Float, default=1.0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # One mention per entity per article (re-running extract_entities is idempotent)
    __table_args__ = (
        UniqueConstraint(
            "entity_id", "content_item_id", name="uq_mention_entity_article"
        ),
    )

    entity = relationship("Entity", back_populates="mentions")
    content_item = relationship("ContentItem")


class EntityRelation(Base):
    __tablename__ = "entity_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "content_item_id",
            name="uq_entity_relation_source_target_type_article",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type = Column(
        Text, nullable=False
    )  # free-text predicate, e.g. "pioneered the concept of"
    description = Column(Text, nullable=True)
    weight = Column(Float, default=1.0, nullable=False)
    content_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    source_entity = relationship(
        "Entity", foreign_keys=[source_entity_id], back_populates="source_relations"
    )
    target_entity = relationship(
        "Entity", foreign_keys=[target_entity_id], back_populates="target_relations"
    )
