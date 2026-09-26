"""
Agent Skills for sed.i — Anthropic Agent Skills format (March 2026).

Each Skill is a named instruction block that tells a calling agent how to sequence
sed.i's MCP tools for a specific task. Registered as an MCP resource so only the
relevant Skill is loaded per request, not all simultaneously.
"""

WEEKLY_DIGEST_SKILL = """\
## weekly-digest

Goal: summarize what the user saved this week against what they already know, \
surfacing what's new and what connects to prior interests.

Steps:
1. get_reading_stats() — get counts and recency
2. list_lists() — identify active lists from this week
3. For each active list: summarize_list(list_id) — cached, prefer over raw content
4. Load user memory: current_focus + past_synthesis_topics (available in context)
5. search_content(current_focus) — find this week's saves that connect to focus
6. Synthesize: what's new this week, what connects to prior work, what's a new thread

Output: 3-5 sentences per theme. Surface connections to prior reading explicitly \
("this connects to your earlier reading on X"). Do not list every article — synthesize across them.
"""

CONNECT_NEW_SAVE_SKILL = """\
## connect-new-save

⚠️ NOT FULLY IMPLEMENTED — this skill is exploratory and not yet a real product feature.
It produces narrative only in a Claude MCP conversation; connections are not surfaced
automatically in the app UI. Overlaps heavily with existing highlight connections
(find_similar + ConnectionsPanel) and does not provide distinct value yet.

Goal: given a newly saved article, find what in the existing library it relates to \
and surface those connections explicitly.

Steps:
1. get_content_item(item_id) — load title, tags, entities
2. find_similar(item_id) — semantic neighbors
3. get_highlights() for the top 3 similar articles — user's own annotations
4. Load user memory: current_focus, active_knowledge_gaps (available in context)
5. Check: does this article address any active_knowledge_gaps?
6. explore_concept(entity_name) for each key entity — entity graph traversal
7. Synthesize: "This connects to [X] because [Y]. It also relates to [Z] which \
you highlighted [quote]. It fills a gap you had on [concept]."

Surface the connection in 2-4 sentences. Be specific — cite titles and quotes, \
not vague similarity scores.
"""

DRAFT_FROM_HIGHLIGHTS_SKILL = """\
## draft-from-highlights

Goal: draft a paragraph using the user's own highlights and library sources, \
in the user's own writing voice.

Steps:
1. get_draft(list_id) — read current draft state first
2. search_content(instruction) — find articles relevant to the draft instruction
3. get_highlights(content_item_ids=[...]) — pull user's own annotations from those articles
4. Draft one paragraph with inline citations [Author, Title]
5. update_draft(list_id, appended_content) — write back

Constraints:
- Only cite articles from the retrieved set — never fabricate sources
- Write one paragraph per call; ask before adding more
- Only call update_draft — do not modify the library
"""

SEDI_SKILLS: dict[str, str] = {
    "weekly-digest": WEEKLY_DIGEST_SKILL,
    "connect-new-save": CONNECT_NEW_SAVE_SKILL,
    "draft-from-highlights": DRAFT_FROM_HIGHLIGHTS_SKILL,
}
