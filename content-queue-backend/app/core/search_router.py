"""
Query intent classifier for hybrid search.

Routes search queries to the most efficient search path using
regex heuristics and user data matching. No LLM call — under 1ms.

Returns:
    tuple[str, dict]: (search_type, metadata)
    search_type: "filter" | "keyword" | "semantic" | "hybrid"
    metadata: dict with inferred filter values (e.g. {"author": "Paul Graham"})

Priority order:
    operators > exact phrase > domain > known author > known tag > short keyword > question > hybrid
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.user import User

# ---------------------------------------------------------------------------
# Operator patterns
# ---------------------------------------------------------------------------

_OPERATOR_PATTERNS: dict[str, re.Pattern[str]] = {
    # author: captures quoted value OR everything until the next operator / end-of-string
    "author": re.compile(r'author:(?:"([^"]+)"|([^:]+?)(?=\s+\w+:|$))', re.IGNORECASE),
    "tag": re.compile(r'tag:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
    "site": re.compile(r'site:(?:"([^"]+)"|(\S+))', re.IGNORECASE),
    "is": re.compile(r"is:(read|unread|archived)", re.IGNORECASE),
    "before": re.compile(r"before:(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    "after": re.compile(r"after:(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
}

_QUESTION_STARTS = re.compile(
    r"^(how|what|why|which|when|where|who|explain|describe|tell me|find me|show me)\b",
    re.IGNORECASE,
)

_DOMAIN_PATTERN = re.compile(
    r"^[\w.-]+\.(com|org|net|io|co|dev|substack|medium|edu|gov)$",
    re.IGNORECASE,
)

_EXACT_PHRASE_PATTERN = re.compile(r'"[^"]+"')


def _extract_operators(query: str) -> dict:
    """Extract all key:value operators from a query string."""
    meta: dict[str, str] = {}
    for key, pattern in _OPERATOR_PATTERNS.items():
        m = pattern.search(query)
        if m:
            # Group 1 is the quoted value, group 2 is the unquoted value
            # For `is`, `before`, `after` there's only one capture group
            groups = [g for g in m.groups() if g is not None]
            if groups:
                meta[key] = groups[0]
    return meta


def classify_query(
    query: str,
    *,
    user_authors: set[str] | None = None,
    user_tags: set[str] | None = None,
) -> tuple[str, dict]:
    """
    Classify a search query into a search type and extract filter metadata.

    Args:
        query: The raw search string from the user.
        user_authors: Lowercased set of known author names for this user.
        user_tags: Lowercased set of known tags for this user.

    Returns:
        (search_type, metadata) where search_type is one of:
        "filter", "keyword", "semantic", "hybrid"
    """
    q = query.strip()

    # 1. OPERATORS — explicit power-user syntax always wins
    meta = _extract_operators(q)
    if meta:
        return "filter", meta

    # 2. EXACT PHRASE — quoted text → keyword (tsvector phrase search)
    if _EXACT_PHRASE_PATTERN.search(q):
        return "keyword", {}

    q_lower = q.lower()

    # 3. DOMAIN — looks like a URL domain → site filter
    if _DOMAIN_PATTERN.match(q_lower):
        return "filter", {"site": q_lower}

    # 4. KNOWN AUTHOR — matches user's library data → filter by author
    if user_authors and q_lower in user_authors:
        return "filter", {"author": q}

    # 5. KNOWN TAG — matches user's existing tags → filter by tag
    # Only trigger when the query is already lowercase. Uppercase/mixed-case queries
    # (e.g. "RLHF", "GPT-4") look like acronyms or proper nouns the user wants to
    # search by content, not narrow to tag-filtered results.
    if user_tags and q_lower in user_tags and q == q_lower:
        return "filter", {"tag": q}

    # 6. QUESTION — interrogative start or trailing ? (checked before short-keyword
    #    so "anything about stoicism?" doesn't get misrouted as keyword)
    if q.endswith("?") or _QUESTION_STARTS.match(q):
        return "semantic", {}

    words = q.split()

    # 7. SHORT KEYWORD — 1-3 words, not a question → full-text keyword search
    #    Falls back to semantic in hybrid_search() if keyword returns 0 results.
    #    4+ word queries are assumed to be conceptual phrases → hybrid (step 8).
    _question_words = {
        "how",
        "what",
        "why",
        "which",
        "when",
        "where",
        "who",
        "explain",
        "describe",
        "find",
        "show",
    }
    if len(words) <= 3 and not any(w.lower() in _question_words for w in words):
        return "keyword", {}

    # 8. DEFAULT — run both keyword + semantic and fuse
    return "hybrid", {}


# ---------------------------------------------------------------------------
# Filter query executor
# ---------------------------------------------------------------------------


def parse_filter_query(
    *,
    meta: dict,
    user: User,
    db: Session,
    limit: int = 50,
) -> list[dict]:
    """
    Execute a structured filter query against content_items.

    Builds a SQLAlchemy query from the metadata dict produced by classify_query,
    applies all filters with AND logic, and returns formatted results.

    Args:
        meta: Dict of filter key/value pairs (author, tag, site, is, before, after).
        user: The authenticated user — all results are scoped to their library.
        db: Database session.
        limit: Maximum number of results to return.

    Returns:
        List of dicts in the same format as _format_item from mcp/tools/content.py.
    """
    from app.mcp.tools.content import _format_item

    q = db.query(ContentItem).filter(
        ContentItem.user_id == user.id,
        ContentItem.deleted_at.is_(None),
        ContentItem.title.isnot(None),
        ContentItem.title != "",
    )

    if "author" in meta:
        q = q.filter(ContentItem.author.ilike(f"%{meta['author']}%"))

    if "tag" in meta:
        # Case-insensitive partial match: any element of the tags array contains the value
        q = q.filter(
            text(
                "EXISTS (SELECT 1 FROM unnest(tags) t WHERE t ILIKE :tag_pat)"
            ).bindparams(tag_pat=f"%{meta['tag']}%")
        )

    if "site" in meta:
        q = q.filter(ContentItem.original_url.ilike(f"%{meta['site']}%"))

    if "is" in meta:
        status = meta["is"].lower()
        if status == "unread":
            q = q.filter(ContentItem.is_read == False)  # noqa: E712
        elif status == "read":
            q = q.filter(ContentItem.is_read == True)  # noqa: E712
        elif status == "archived":
            q = q.filter(ContentItem.is_archived == True)  # noqa: E712

    if "before" in meta:
        try:
            dt = datetime.strptime(meta["before"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            q = q.filter(ContentItem.created_at < dt)
        except ValueError:
            pass

    if "after" in meta:
        try:
            dt = datetime.strptime(meta["after"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            q = q.filter(ContentItem.created_at >= dt)
        except ValueError:
            pass

    items = q.order_by(ContentItem.created_at.desc()).limit(limit).all()
    return [_format_item(item, include_full_text=False) for item in items]
