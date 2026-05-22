"""
MCP tools: summarize_list, get_summary_job.

summarize_list calls OpenAI to produce a summary of a reading list.
Results are cached in-process (keyed on user+list+content_hash+style).
get_summary_job provides a polling endpoint for async fallback.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.llm_client import llm_client
from app.models.user import User
from app.models.list import List, content_list_membership
from app.models.content import ContentItem
from app.models.draft import Draft

VALID_STYLES = {"overview", "themes", "gaps", "timeline"}

# In-process cache: (user_id, list_id, content_hash, style) → result dict
_cache: dict[tuple, dict] = {}

# Job store: job_id → result dict (populated after completion)
_jobs: dict[str, dict] = {}


def _content_hash(articles: list[dict]) -> str:
    payload = json.dumps(
        [{"id": a["id"], "title": a["title"]} for a in articles], sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _build_prompt(
    articles: list[dict], style: str, draft_content: str | None
) -> list[dict]:
    article_lines = "\n".join(
        f"- {a['title']}: {a.get('summary') or a.get('description') or ''}"
        for a in articles
    )

    style_instructions = {
        "overview": "Provide a concise overview of the main topics and themes covered by these articles.",
        "themes": "Identify and explain the key recurring themes across these articles.",
        "gaps": "Identify what topics or perspectives are missing or underrepresented in this collection.",
        "timeline": "Describe how the topics in this collection have evolved or progressed over time.",
    }

    system = (
        "You are a helpful research assistant. "
        "Summarize the provided reading list based on the requested style. "
        "Be concise (2-4 sentences) and insightful."
    )

    user_content = f"{style_instructions[style]}\n\nArticles:\n{article_lines}"

    if style == "gaps" and draft_content:
        user_content += f"\n\nUser's current draft:\n{draft_content}\n\nConsider what the draft covers when identifying gaps."

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def summarize_list(
    *,
    list_id: str,
    user: User,
    db: Session,
    style: str = "overview",
    max_items: int = 20,
) -> dict:
    """
    Summarize a reading list using OpenAI.

    Args:
        list_id: UUID of the list.
        style: One of 'overview', 'themes', 'gaps', 'timeline'.
        max_items: Max articles to include in the prompt (default 20).

    Returns:
        {summary, style, item_count, cached, job_id (optional)}

    Raises:
        ValueError: If list not found, belongs to another user, or style is invalid.
    """
    if style not in VALID_STYLES:
        raise ValueError(
            f"Invalid style '{style}'. Must be one of: {', '.join(sorted(VALID_STYLES))}"
        )

    lst = db.query(List).filter(List.id == list_id, List.owner_id == user.id).first()
    if not lst:
        raise ValueError(f"List '{list_id}' not found")

    # Load articles
    memberships = (
        db.query(content_list_membership)
        .filter(content_list_membership.c.list_id == list_id)
        .all()
    )
    item_ids = [m.content_item_id for m in memberships]

    articles: list[dict] = []
    if item_ids:
        items = (
            db.query(ContentItem)
            .filter(ContentItem.id.in_(item_ids), ContentItem.user_id == user.id)
            .limit(max_items)
            .all()
        )
        articles = [
            {
                "id": str(item.id),
                "title": item.title or "",
                "summary": item.summary or "",
                "description": item.description or "",
            }
            for item in items
        ]

    if not articles:
        return {
            "summary": "This list has no articles to summarize.",
            "style": style,
            "item_count": 0,
            "cached": False,
        }

    content_hash = _content_hash(articles)
    cache_key = (str(user.id), list_id, content_hash, style)

    if cache_key in _cache:
        cached = dict(_cache[cache_key])
        cached["cached"] = True
        return cached

    # Fetch draft for gaps style
    draft_content: str | None = None
    if style == "gaps":
        draft = (
            db.query(Draft)
            .filter(Draft.list_id == list_id, Draft.user_id == user.id)
            .first()
        )
        if draft:
            draft_content = draft.content

    messages = _build_prompt(articles, style, draft_content)

    result = llm_client.chat(messages=messages, max_tokens=512, temperature=0.5)
    summary_text = result.content

    result = {
        "summary": summary_text,
        "style": style,
        "item_count": len(articles),
        "cached": False,
    }
    _cache[cache_key] = result
    return result


def get_summary_job(*, job_id: str, user: User, db: Session) -> dict:
    """
    Poll for an async summary job result.

    Returns:
        {status: 'done'|'pending'|'not_found', summary?, ...}
    """
    if job_id not in _jobs:
        return {"status": "not_found", "job_id": job_id}

    job = _jobs[job_id]
    return {"status": "done", **job}
