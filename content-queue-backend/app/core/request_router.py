"""
Two-tier request classifier for MCP tool routing.

Tier 1 (free): catches obvious filter/lookup queries without an LLM call.
Tier 2 (gpt-4o-mini): everything else — structured output with few-shot examples.

Usage:
    from app.core.request_router import classify_request
    route, skill = classify_request("what did I save this week?")
    # → ("skill", "weekly-digest")
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.core.llm_client import llm_client, TASK_ROUTING

# Filter operators that unambiguously mean "direct lookup"
_DIRECT_OPERATORS = ("after:", "before:", "tag:", "author:", "site:")

# Verb prefixes that indicate a single-item lookup with no synthesis signal
_DIRECT_VERBS = ("find ", "search ", "show me ", "get ", "list ", "how many ")

# Signals that indicate synthesis intent even when a direct verb is present
_SYNTHESIS_SIGNALS = (
    "competing views",
    "synthesize",
    "themes",
    "what do i know",
    "overview of",
    "summarize my",
    "across my",
)

_ROUTING_PROMPT = """\
Classify the user's question into one of three routes for a personal reading assistant:

- "direct": a simple lookup query (find an article, show recent saves, filter by tag/date/author)
- "skill": a task matching one of these named skills:
    "weekly-digest"        — what the user saved/read this week, weekly summary
    "connect-new-save"     — how a newly saved article connects to existing library
    "draft-from-highlights" — draft or write using the user's highlights and reading
- "orchestrate": an open-ended research question requiring multi-round synthesis

Examples:
Q: "find articles about transformers" → direct
Q: "show me what I read last month" → direct
Q: "search tag:ml" → direct
Q: "what did I save this week?" → skill: weekly-digest
Q: "give me a summary of my past 7 days" → skill: weekly-digest
Q: "summarize what I've been reading recently" → skill: weekly-digest
Q: "how does this article connect to what I know?" → skill: connect-new-save
Q: "how is this related to my library?" → skill: connect-new-save
Q: "help me draft an intro using my reading" → skill: draft-from-highlights
Q: "write a paragraph from my highlights" → skill: draft-from-highlights
Q: "what are the competing views I've saved on AI alignment?" → orchestrate
Q: "synthesize everything I know about production ML systems" → orchestrate
Q: "what themes keep coming up across my ML reading?" → orchestrate
Q: "what do I know about RAG?" → orchestrate

Question: {question}
"""


class RouteDecision(BaseModel):
    route: Literal["direct", "skill", "orchestrate"]
    skill: (
        Literal["weekly-digest", "connect-new-save", "draft-from-highlights"] | None
    ) = None


def _has_synthesis_signal(q: str) -> bool:
    return any(s in q for s in _SYNTHESIS_SIGNALS)


def classify_request(question: str) -> tuple[str, str | None]:
    """
    Classify a user question into (route, skill_name | None).

    Tier 1 (free): obvious filter/lookup queries bypass the LLM.
    Tier 2 (gpt-4o-mini): structured output classification for everything else.

    Routes: 'direct' | 'skill' | 'orchestrate'
    """
    q = question.lower().strip()

    # Tier 1: filter operators unambiguously mean direct lookup
    if any(op in q for op in _DIRECT_OPERATORS):
        return ("direct", None)

    # Tier 1: direct-lookup verb prefix with no synthesis signal
    if any(q.startswith(v) for v in _DIRECT_VERBS) and not _has_synthesis_signal(q):
        return ("direct", None)

    # Tier 2: LLM classification
    decision: RouteDecision = llm_client.structured_chat(
        messages=[
            {
                "role": "user",
                "content": _ROUTING_PROMPT.format(question=question),
            }
        ],
        response_model=RouteDecision,
        task=TASK_ROUTING,
        max_tokens=64,
    )
    return (decision.route, decision.skill)
