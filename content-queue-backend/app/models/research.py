"""
ResearchRun — persistent state record for a multi-agent research brief run.

One row per run. Status transitions:
  queued → planning → searching → synthesizing → verifying → done
                                                           ↘ partial (budget exhausted)
                                                           ↘ failed (unrecoverable error)
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question = Column(Text, nullable=False)
    mode = Column(Text, nullable=False, default="deep")  # "quick" | "deep"

    # Status: queued → planning → searching → synthesizing → verifying → done|partial|failed
    status = Column(Text, nullable=False, default="queued")

    # Planning step output
    plan = Column(Text, nullable=True)
    sub_questions = Column(JSONB, nullable=True)  # list[str]

    # Retrieval step outputs
    subagent_results = Column(JSONB, nullable=True)  # list[SubagentResult as dict]
    item_ids_retrieved = Column(JSONB, nullable=True)  # list[str] — deduped union
    searches_run = Column(JSONB, nullable=True)  # [{idempotency_key, subagent_id}]

    # Synthesis output — ResearchBrief as dict when status is done/partial
    result = Column(JSONB, nullable=True)

    # Metadata
    cost = Column(JSONB, nullable=True)  # {prompt_tokens, completion_tokens, usd}
    error = Column(JSONB, nullable=True)  # {code, message}
    iteration_count = Column(Integer, nullable=False, default=0)
    budget = Column(
        JSONB, nullable=False
    )  # {max_tokens, max_iterations, max_subagents, timeout_s}

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
