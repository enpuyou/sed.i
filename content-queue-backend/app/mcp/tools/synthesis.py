"""
MCP tools: synthesize_topic, assist_draft.

synthesize_topic — quick mode: single-pass synthesis across the user's library.
assist_draft     — draft a paragraph using the user's highlights in their writing voice.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.hybrid_search import hybrid_search
from app.core.llm_client import llm_client, TASK_SYNTHESIS
from app.models.memory import UserProfile
from app.models.user import User
from app.tasks.research import run_research_lead_task

logger = logging.getLogger(__name__)

_MAX_CONTEXT_TOKENS = 4000

_QUICK_SYNTHESIS_PROMPT = """\
You are synthesizing a user's personal reading library on a specific topic.
All sources below are from the user's own library — articles they chose to save.

{memory_context}

Topic: {topic}

Library sources:
{context}

Produce a structured synthesis. For each perspective or angle found in the sources,
note which articles support it (by item_id). Be specific about what the user's
library actually covers — do not generalize beyond the sources shown.
If the library has little on this topic, say so and set confidence to "low".
"""

_DRAFT_PROMPT = """\
You are helping a user draft content using their own reading as source material.

Writing style to match exactly:
{style_notes}

Instruction: {instruction}

Relevant articles and highlights from the user's library:
{context}

Draft exactly one paragraph. Use inline citations [Title] where you reference a source.
Match the writing style above. Only cite sources shown — never invent sources.
"""


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PerspectiveItem(BaseModel):
    stance: str
    summary: str
    source_ids: list[str]


class SourceCitation(BaseModel):
    item_id: str
    title: str
    quote: str | None = None


class SynthesisResponse(BaseModel):
    summary: str
    perspectives: list[PerspectiveItem]
    key_concepts: list[str]
    sources: list[SourceCitation]
    confidence: Literal["high", "medium", "low"]


class Citation(BaseModel):
    item_id: str
    title: str


class DraftAddition(BaseModel):
    content: str
    citations: list[Citation]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _count_tokens_approx(text: str) -> int:
    """Approximate token count: ~4 chars per token (tiktoken cl100k_base average)."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def _build_context(
    results: list[dict],
    db: Session,
    max_tokens: int = _MAX_CONTEXT_TOKENS,
) -> tuple[str, list[str]]:
    """
    Build a context string from search results, gated by token budget.

    Returns (context_str, included_item_ids).
    Drops whole blocks rather than truncating mid-way.
    Blocks are appended in descending relevance order (results already sorted).
    """
    from app.models.highlight import Highlight

    blocks: list[tuple[str, str]] = []  # (item_id, block_text)

    for r in results:
        item_id = str(r.get("id", r.get("item_id", "")))
        title = r.get("title") or "Untitled"
        description = r.get("description") or ""
        # Try to get a top highlight for richer context
        top_highlight = ""
        if item_id:
            try:
                import uuid

                h = (
                    db.query(Highlight)
                    .filter(
                        Highlight.content_item_id == uuid.UUID(item_id),
                        Highlight.user_id == r.get("user_id"),
                    )
                    .order_by(Highlight.created_at.desc())
                    .first()
                )
                if h:
                    top_highlight = f'\n  Highlight: "{h.text[:200]}"'
            except Exception:
                pass

        block = f"[{item_id}] {title}\n{description}{top_highlight}"
        blocks.append((item_id, block))

    included_ids: list[str] = []
    parts: list[str] = []
    running_tokens = 0

    for item_id, block in blocks:
        tokens = _count_tokens_approx(block)
        if running_tokens + tokens > max_tokens:
            break
        parts.append(block)
        included_ids.append(item_id)
        running_tokens += tokens

    return "\n\n".join(parts), included_ids


def _load_profile(user: User, db: Session) -> UserProfile | None:
    return db.query(UserProfile).filter(UserProfile.user_id == user.id).first()


# ---------------------------------------------------------------------------
# Core logic (directly callable in tests via __wrapped__ or direct import)
# ---------------------------------------------------------------------------


def _synthesize_deep(*, topic: str, user: User, db: Session) -> dict:
    """
    Enqueue a deep research run and return {run_id, status_url} immediately.

    Rate limit: max 3 active (non-terminal) runs per user.
    """
    from app.models.research import ResearchRun

    _TERMINAL = ("done", "failed", "partial")
    _MAX_ACTIVE = 3

    active_count = (
        db.query(ResearchRun)
        .filter(
            ResearchRun.user_id == user.id,
            ResearchRun.status.notin_(_TERMINAL),
        )
        .count()
    )
    if active_count >= _MAX_ACTIVE:
        return {"error": "run_limit_exceeded"}

    run = ResearchRun(
        user_id=user.id,
        question=topic,
        mode="deep",
        status="queued",
        budget={
            "max_tokens": 50000,
            "max_iterations": 3,
            "max_subagents": 6,
            "timeout_s": 300,
            "target_count": 8,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    run_research_lead_task.delay(str(run.id))

    return {
        "run_id": str(run.id),
        "status_url": f"/research/{run.id}",
    }


def synthesize_topic(
    *,
    topic: str,
    depth: Literal["quick", "deep"] = "quick",
    user: User,
    db: Session,
) -> dict:
    """
    Synthesize a topic from the user's library.

    quick: single-pass, ~5s, synchronous. Returns SynthesisResponse dict.
    deep:  returns {run_id, status_url} immediately (Phase 2, not yet implemented).
    """
    if depth == "deep":
        return _synthesize_deep(topic=topic, user=user, db=db)

    profile = _load_profile(user, db)
    memory_context = ""
    if profile:
        parts = []
        if profile.current_focus:
            parts.append(f"Current focus: {profile.current_focus}")
        if profile.memory_text:
            parts.append(profile.memory_text)
        memory_context = "\n".join(parts)

    results = hybrid_search(query=topic, user=user, db=db, limit=10, mode="full")
    context, included_ids = _build_context(results, db)

    if not context:
        return SynthesisResponse(
            summary=f"Your library has no articles on '{topic}' yet.",
            perspectives=[],
            key_concepts=[],
            sources=[],
            confidence="low",
        ).model_dump()

    response: SynthesisResponse = llm_client.structured_chat(
        messages=[
            {
                "role": "user",
                "content": _QUICK_SYNTHESIS_PROMPT.format(
                    topic=topic,
                    context=context,
                    memory_context=memory_context,
                ),
            }
        ],
        response_model=SynthesisResponse,
        task=TASK_SYNTHESIS,
        max_tokens=1024,
    )

    # Filter sources to only those actually in context (grounding enforcement)
    grounded_sources = [s for s in response.sources if s.item_id in included_ids]
    response.sources = grounded_sources

    return response.model_dump()


def assist_draft(
    *,
    list_id: str,
    instruction: str,
    user: User,
    db: Session,
) -> dict:
    """
    Draft a paragraph using the user's highlights and library sources.

    Bounded write scope: only calls update_draft. Does not add content,
    create lists, or modify any library items.
    """
    from app.models.list import List
    from app.models.draft import Draft
    from app.models.highlight import Highlight
    from app.mcp.tools.write import update_draft as _update_draft

    import uuid as _uuid

    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    style_notes = "clear, concise prose"

    results = hybrid_search(query=instruction, user=user, db=db, limit=8, mode="full")

    # Build context with highlights for found articles
    retrieved_ids = [str(r.get("id", r.get("item_id", ""))) for r in results]
    context_parts: list[str] = []
    for r in results:
        item_id_str = str(r.get("id", r.get("item_id", "")))
        title = r.get("title") or "Untitled"
        try:
            item_uuid = _uuid.UUID(item_id_str)
            highlights = (
                db.query(Highlight)
                .filter(
                    Highlight.content_item_id == item_uuid,
                    Highlight.user_id == user.id,
                )
                .order_by(Highlight.created_at.desc())
                .limit(3)
                .all()
            )
            hl_text = "\n".join(f'  - "{h.text}"' for h in highlights)
            block = f"[{item_id_str}] {title}"
            if hl_text:
                block += f"\n  Your highlights:\n{hl_text}"
            context_parts.append(block)
        except Exception:
            context_parts.append(f"[{item_id_str}] {title}")

    context = "\n\n".join(context_parts) or "(no relevant articles found)"

    addition: DraftAddition = llm_client.structured_chat(
        messages=[
            {
                "role": "user",
                "content": _DRAFT_PROMPT.format(
                    style_notes=style_notes,
                    instruction=instruction,
                    context=context,
                ),
            }
        ],
        response_model=DraftAddition,
        task=TASK_SYNTHESIS,
        max_tokens=512,
    )

    # Verify all citations reference retrieved articles
    grounded_citations = [c for c in addition.citations if c.item_id in retrieved_ids]

    # Append to existing draft (bounded write: only update_draft)
    existing = (
        db.query(Draft)
        .filter(Draft.list_id == list_id, Draft.user_id == user.id)
        .first()
    )
    existing_content = existing.content if existing else ""
    separator = "\n\n" if existing_content else ""
    new_content = existing_content + separator + addition.content

    _update_draft(list_id=list_id, content=new_content, user=user, db=db)

    return {
        "added": addition.content,
        "citations": [c.model_dump() for c in grounded_citations],
        "source_count": len(retrieved_ids),
    }
