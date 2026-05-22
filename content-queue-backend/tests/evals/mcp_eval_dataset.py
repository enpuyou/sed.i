"""
MCP behavioral eval dataset.

Each example describes a user message and the expected MCP tool call(s)
that should result. Used to verify that the MCP server routes user intent
to the correct tools with sane parameters.

These are behavioral (tool-routing) tests — they don't require LLM inference,
just checking that the tool layer accepts the right inputs and returns
expected shape.
"""

# MCP tool invocation examples — each maps user intent to expected tool + args
MCP_TOOL_EXAMPLES = [
    # ── Content retrieval ──
    {
        "key": "search_articles",
        "intent": "User wants to find articles about a topic",
        "tool": "search_content",
        "args": {"query": "reinforcement learning from human feedback"},
        "expected_keys": ["item", "similarity_score"],  # keys per result entry
        "description": "search_content returns list of {item, similarity_score}",
    },
    {
        "key": "get_article",
        "intent": "User wants to read a specific article by URL",
        "tool": "get_content_item",
        "args": None,  # requires real item ID — tested with fixture
        "expected_keys": ["id", "title", "url"],
        "description": "get_content_item returns item metadata",
    },
    # ── List operations ──
    {
        "key": "list_lists",
        "intent": "User wants to see their reading lists",
        "tool": "list_lists",
        "args": {},
        "expected_keys": ["lists"],
        "description": "list_lists returns user's lists",
    },
    {
        "key": "summarize_list_overview",
        "intent": "User wants a summary of what's in a list",
        "tool": "summarize_list",
        "args": {"style": "overview"},
        "expected_keys": ["summary", "style", "item_count"],
        "description": "summarize_list overview returns summary + metadata",
    },
    {
        "key": "summarize_list_gaps",
        "intent": "User wants to know what's missing from their research",
        "tool": "summarize_list",
        "args": {"style": "gaps"},
        "expected_keys": ["summary", "style", "item_count"],
        "description": "summarize_list gaps returns gap analysis",
    },
    # ── Highlights ──
    {
        "key": "get_highlights",
        "intent": "User wants to see their highlights from an article",
        "tool": "get_highlights",
        "args": None,  # requires real item ID
        "expected_keys": ["highlights"],
        "description": "get_highlights returns list of highlights",
    },
    # ── Writing ──
    {
        "key": "get_draft",
        "intent": "User wants to see their draft for a list",
        "tool": "get_draft",
        "args": None,  # requires real list ID
        "expected_keys": ["content", "list_id"],
        "description": "get_draft returns draft content",
    },
    # ── Stats ──
    {
        "key": "reading_stats",
        "intent": "User wants to see their reading stats",
        "tool": "get_reading_stats",
        "args": {},
        "expected_keys": ["total_items", "read_count"],
        "description": "get_reading_stats returns reading statistics",
    },
]
