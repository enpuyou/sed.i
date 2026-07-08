---
type: research
status: active
last_updated: 2026-07-01
consumer: human
---

# GraphRAG & Multi-Agent Architecture for sed.i

> Covers: current stack limitations, SOTA (2026) GraphRAG and agentic patterns,
> feature proposals with design sketches, eval framework, and build sequencing.

---

## Table of contents

1. [Where the current stack breaks](#1-where-the-current-stack-breaks)
2. [GraphRAG: what it actually is](#2-graphrag-what-it-actually-is)
3. [SOTA GraphRAG variants (2024–2026)](#3-sota-graphrag-variants-2024-2026)
4. [2026 agentic patterns in production](#4-2026-agentic-patterns-in-production)
5. [Agent loops — when they're justified](#5-agent-loops--when-theyre-justified)
6. [Concrete feature proposals](#6-concrete-feature-proposals)
7. [Eval framework](#7-eval-framework)
8. [Implementation sequencing](#8-implementation-sequencing)
9. [Job signal map](#9-job-signal-map)
10. [Decision record hooks](#10-decision-record-hooks)
11. [Appendix A: Papers](#appendix-a-papers-to-read-in-order)
12. [Appendix B: Stack additions](#appendix-b-technology-stack-additions)

---

## 1. Where the current stack breaks

### What sed.i already does well

- **Item-level embedding**: 1536-dim vector per article, truncated to 8k tokens.
- **Chunk-level embedding**: structure-aware ~350-token chunks with contextual
  prefixes (Anthropic contextual retrieval). MAX-similarity-across-chunks scores
  each article.
- **Hybrid search**: tsvector keyword + pgvector semantic, fused with RRF.
- **Highlight embeddings**: per-highlight vectors powering the connections panel.
- **Tag embeddings**: per-label vectors for tag similarity clustering.
- **Connections**: cosine similarity across highlights, with AI-generated one-sentence insights.

### Limitations

#### Break 1 — No multi-hop retrieval

Flat cosine similarity can only find articles with overlapping vocabulary. It
cannot traverse chains: sleep → circadian rhythm → cortisol → stress → focus
→ productivity. A query like "what connects sleep science to productivity" only
works if those terms co-occur in the same article's embedding.

#### Break 2 — Redundant retrieval

Twenty articles on "LLM context windows" → every search for "context" surfaces
near-identical results. No distinction between canonical sources and supporting
evidence.

#### Break 3 — Recommendation has no knowledge adjacency model

The recommendation engine scores: embedding similarity to recent reads +
recency + tag overlap + reading time match. There is no model of conceptual
adjacency — it does not know that five articles on "gradient descent" makes
"backpropagation" a natural next step.

Separately: `reading_patterns["preferred_tags"]` never shrinks (bug documented
in data-flows.md §7.4), so old interests are weighted equally to current ones.

#### Break 4 — MCP `query_library` does not synthesize across articles

Handles structured queries ("ML articles from last month") but cannot answer
"what are the competing views I've saved on AI alignment?" — it generates SQL,
it does not reason over article content.

#### Break 5 — No entity deduplication

Fifty articles mentioning "Geoffrey Hinton" each encode him as a statistical
signal in their embedding. There is no entity node aggregating his appearances,
the contexts, or the relationships across those articles.

---

## 2. GraphRAG: what it actually is

In GraphRAG, the retrieval unit is a subgraph rather than a chunk. Indexing
extracts entities and relations; queries traverse the graph rather than ranking
vectors by cosine distance.

### Flat vector RAG (what sed.i has)

```
Document → chunk → embed → store vector
Query → embed → cosine search → top-k chunks → LLM synthesizes
```

Relevance: point-to-point cosine. Context: one chunk. No structural information.

### GraphRAG (Microsoft, 2024)

```
Document → extract entities + relations → build graph
         → community detection → summarize communities
Query → classify: local (entity) or global (community)
      → traverse relevant subgraph → structured context
      → LLM synthesizes
```

Relevance: graph traversal + vector similarity. Context: entity neighborhoods
and community summaries across articles.

### What gets stored in a graph index

```
Entities: {id, name, type, description, embedding}
  Person("Geoffrey Hinton"), Concept("attention mechanism"), Org("DeepMind")

Relations: {source_entity, target_entity, relation_type, description, weight}
  (Hinton) --[DEVELOPED]--> (backpropagation)
  (Attention Is All You Need) --[INTRODUCES]--> (transformer architecture)

Community reports: {community_id, title, summary, entity_ids, level}
  "Transformer Architecture" — 15 entities across 8 articles
```

Entity extraction is the expensive step: O(n_chunks × LLM_calls) at index
time. At gpt-4o-mini pricing (~$0.15/1M tokens), an 800-word article costs
~$0.0002. Negligible per-article, runs asynchronously in the Celery chain.

---

## 3. SOTA GraphRAG variants (2024–2026)

### Microsoft GraphRAG (2024, arXiv:2404.16130)

Two contributions: (1) community-level indexing with Leiden clustering,
(2) global search via map-reduce over community summaries.

**Limitation for sed.i**: Designed for 100k+ document corpora. Global search
requires pre-summarizing every community — cost that doesn't amortize at
single-user / 200-article scale.

**What transfers**: Entity extraction + local search. Subgraph → synthesize
handles multi-hop queries better than chunks → synthesize.

### LightRAG (2024, arXiv:2410.05779 — HKUST)

Builds a dual-level retrieval index: entities/relations as a graph AND as
vectors for lookup. Query types: naive (vector only), local (entity
neighborhood), global (community summaries), hybrid (all three, fused).

**For sed.i**: No Leiden community detection required. Hybrid mode parallels
sed.i's existing RRF fusion. Entity graph fits in Postgres at this scale.

**Benchmark**: Outperforms Microsoft GraphRAG and NaiveRAG on NarrativeQA and
HotpotQA, 3–5× faster at query time due to lighter global indexing.

### HippoRAG (2024, arXiv:2405.14831 — Ohio State)

Inspired by hippocampal memory retrieval: builds a Personalized PageRank graph
where nodes are named entities and edges are co-occurrence within passages.
At query time, seeds PPR from query-relevant entities and propagates to find
topically adjacent passages — not just semantically similar ones.

**For sed.i**: "Sleep cycles" → "cognitive load" → "working memory" surfaces
cross-article connections based on entity co-occurrence rather than vocabulary
similarity. PPR is computable in Python (networkx) or SQL (recursive CTEs) at
this graph size.

### RAPTOR (2024, arXiv:2401.18059 — Stanford)

Tree-based, not graph-based: cluster similar chunks → summarize each cluster →
build a tree of summaries → query at any level.

**For sed.i**: The existing `cluster_user_tags_task` does flat clustering.
RAPTOR-style tree summarization adds a per-cluster summary embedding as a
recommendation signal. Unlike the current `preferred_tags` list, it decays
naturally as clusters evolve.

### PathRAG (2025, arXiv:2502.14902)

Models retrieval as graph path-finding. Finds shortest paths connecting
query-relevant entities. Returns paths, not just nodes. The LLM reasons about
why each path makes sense.

**For sed.i**: Returns path chains as explicit context, e.g. Kahneman →
[cognitive bias] → [reward hacking] → [RLHF] → alignment research.

---

## 4. 2026 agentic patterns in production

### Dreaming — scheduled background memory consolidation

Anthropic shipped **Dreaming** on May 6, 2026 (Code with Claude): a scheduled
process that runs between agent sessions, reviews activity, extracts patterns,
merges duplicates, and writes new memory entries. Harvey (legal AI) reported
~6× task completion improvement in internal testing — agents were repeatedly
failing on the same tool quirks and filetype workarounds between sessions;
Dreaming consolidated those into persistent memory.

**For sed.i**: A nightly Celery beat task reviews the last 7 days of user
activity (reads, highlights, saves), extracts structured insights via
`llm_client.structured_chat()`, and writes to a `user_memory` table.
Recommendations, synthesis, and the proactive agent all read from this table.

### Four-tier agent memory

Four memory types are now the standard taxonomy in agent memory systems. Zep
scores 63.8% vs Mem0's 49% on LongMemEval, attributed to its temporal
knowledge graph architecture.

| Memory type | What it stores | sed.i today | Gap |
| --- | --- | --- | --- |
| **Working** | Current session context | Claude conversation window | N/A — LLM handles |
| **Episodic** | Specific past events ("on June 3 user highlighted X") | Highlights + read history — not queryable as memory | `user_memory` table needed |
| **Semantic** | Extracted facts ("user prefers long-form, dislikes clickbait") | `reading_patterns` JSONB — 3 shallow fields | Needs richer `UserProfile` |
| **Procedural** | How to do things ("user's writing style is concise") | Not implemented | Can be extracted from drafts |

sed.i has working memory (LLM handles it) and a shallow form of semantic
(`reading_patterns` JSONB). Episodic and procedural are not implemented.
`user_memory` + `UserProfile` (Feature M) adds both.

### MCP Skills — bundling domain knowledge with tools

The 2026 MCP roadmap added **Skills**: operational guidance bundled alongside
tool definitions — instructions for sequencing and combining tools, not just
their signatures.

**For sed.i**: A Skills block in the MCP server guides calling agents (Claude
Desktop, Claude.ai) on how to sequence tools optimally, rather than leaving
them to discover the right order by trial and error:

```python
SEDI_SKILLS = """
When researching a topic in the user's library:
1. Start with search_content(topic) — broad pass
2. If results < 3, try find_similar() on the best result
3. For multi-part questions, search_content() separately per concept
4. get_highlights() surfaces the user's own annotations — always check this
5. summarize_list() is cached — prefer it over reading full articles
6. explore_concept(entity_name) traverses the knowledge graph — use for
   questions about a specific person, concept, or organization
"""
```

Additive to the existing MCP server registration — no new endpoints.

### ReAct loops

**ReAct (Reason + Act + Observe)** is the dominant loop pattern in production
agents (Claude Code, Codex, Cursor). The practical constraint: the verifier
must be machine-checkable and cheap to run. "Make the tests pass" works because
the verifier is binary and free. "Make the essay better" doesn't work because
the verifier is subjective.

---

## 5. Agent loops — when they're justified

### Criteria for a justified loop

1. **Success is machine-verifiable** — the verifier runs in code, not human
   judgment.
2. **Success is binary or countable** — pass/fail or "found ≥ N items", not
   "looks good."
3. **The loop has a hard exit condition** — max iterations capped in code, not
   in prompt.
4. **The loop is cheaper than the alternative** — if a better prompt solves it,
   use that.

### Two loops justified in sed.i

**Loop 1 — Iterative search refinement (inside `synthesize_topic`)**

One-shot retrieval may return too few relevant articles. The loop reformulates
the query based on what was found and retries, up to a cap.

```
Goal: find ≥ 5 relevant articles for synthesis

Iteration 1: hybrid_search(original_query)
  → 2 results. Verifier: len(unique_results) < 5. Continue.

Iteration 2: LLM reflects on what's missing → generates reformulation
  hybrid_search(reformulated_query)
  → 4 results (2 new). Verifier: still < 5. Continue.

Iteration 3: entity_search(top concept from iteration 2)
  → 3 results (1 new). Total unique = 5. Verifier: PASS. Stop.
```

Verifier: `len(unique_results) >= target_count` — binary, free, instant.
Hard cap: 3 iterations.

```python
def iterative_search(topic, user, db, target=5, max_iter=3):
    seen, results, query = set(), [], topic

    for _ in range(max_iter):
        batch = hybrid_search(query, user, db, limit=10, mode="full")
        new = [r for r in batch if r["id"] not in seen]
        seen.update(r["id"] for r in new)
        results.extend(new)

        if len(results) >= target:
            break  # verifier passed

        # Reflection step — only runs if verifier fails
        query = llm_client.chat(
            messages=[{"role": "user", "content": REFORMULATION_PROMPT.format(
                original=topic, tried=query,
                found=[r["title"] for r in results],
            )}],
            task=TASK_SEARCH_REFLECT,
        )
    return results
```

**Loop 2 — Knowledge gap detection (inside `consolidate_memory`)**

Gap detection is iterative: finding one gap reveals adjacent gaps in the entity
graph.

```
Goal: find all meaningful gaps in reading cluster X
  (gap = concept strongly implied by existing reading, not yet covered)

Iteration 1: load cluster "distributed systems" → 12 articles
  reflect: "covers Raft and Paxos but nothing on Viewstamped Replication"
  → gap found: "Viewstamped Replication"
  Verifier: unexplored 1-hop entity neighbors? YES → continue.

Iteration 2: check entity graph neighbors of "Raft", "Paxos"
  → "Byzantine fault tolerance" in 0 articles but 3 relations
  → gap found: "Byzantine fault tolerance"
  Verifier: unexplored neighbors at depth ≤ 2? NO → STOP.
```

Verifier: SQL — entity nodes adjacent to this cluster's entities with zero
article mentions. Binary, cheap.

### What NOT to loop in sed.i

- **"Improve this draft"** — subjective verifier, loop never legitimately stops
- **"Find the best articles on X"** — "best" undefined, no termination signal
- **Tag quality** — covered by behavioral feedback (Feature G), not a loop
- **Highlight connection discovery** — PPR graph traversal is already
  deterministic; a loop adds cost with no benefit

---

## 6. Concrete feature proposals

Nine features, grouped by the layer of the stack they improve. Each is
self-contained and independently deployable.

---

### Layer 0: Memory foundation (prerequisite for everything else)

---

#### Feature M: User Memory Table + `consolidate_memory` Task (Dreaming pattern)

**Problem it solves**: sed.i has no episodic or procedural memory. Every
feature starts cold. Recommendations don't know what the user is working on
right now. Synthesis doesn't know the user's current focus. The proactive agent
has no foundation to stand on.

**What it does**: A nightly Celery beat task that reviews user activity from
the last 7 days, extracts structured insights, and writes to a persistent
`user_memory` table. All downstream features read from this table first.

**New tables**:

```sql
-- Episodic memory: specific events
CREATE TABLE user_memory_events (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    event_type TEXT,  -- 'deep_read', 'highlight_burst', 'abandoned', 'cluster_focus'
    content_item_id UUID REFERENCES content_items(id),
    metadata JSONB,   -- {"duration_minutes": 45, "highlight_count": 7}
    occurred_at TIMESTAMPTZ
);

-- Semantic memory: extracted facts (replaces reading_patterns JSONB)
CREATE TABLE user_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    current_focus TEXT,         -- "attention mechanisms in transformers"
    reading_velocity TEXT,      -- 'fast' | 'deep' | 'browsing'
    preferred_depth_words INT,  -- avg word count of completed reads
    writing_style_notes TEXT,   -- extracted from drafts
    active_knowledge_gaps JSONB, -- [{concept, cluster_id, confidence}]
    last_consolidated TIMESTAMPTZ
);
```

**The task**:

```python
# app/tasks/memory.py
@celery_app.task
def consolidate_memory(user_id: str):
    """Dreaming pattern: review last 7 days, extract insights, update memory."""
    # 1. Load recent activity
    recent_reads = _load_recent_reads(user_id, days=7, db)
    recent_highlights = _load_recent_highlights(user_id, days=7, db)
    recent_saves = _load_recent_saves(user_id, days=7, db)

    if not any([recent_reads, recent_highlights, recent_saves]):
        return  # nothing to consolidate

    # 2. Extract structured insights (the LLM step)
    activity_summary = _format_activity(recent_reads, recent_highlights)
    insights = llm_client.structured_chat(
        messages=[{"role": "user", "content": CONSOLIDATION_PROMPT.format(
            activity=activity_summary,
            current_profile=_load_profile(user_id, db),
        )}],
        response_model=ConsolidationResult(
            current_focus=str,
            reading_velocity=Literal["fast", "deep", "browsing"],
            knowledge_gaps=list[KnowledgeGap],
            episodic_events=list[MemoryEvent],
        ),
        task=TASK_MEMORY_CONSOLIDATION,
    )

    # 3. Write to DB
    _upsert_profile(user_id, insights, db)
    _insert_episodic_events(user_id, insights.episodic_events, db)
    db.commit()

# Beat schedule: nightly at 3am per user
# cluster_all_users_task pattern: dispatch per-user tasks
```

**Who reads from this**:

- Recommendation engine: `current_focus` + `active_knowledge_gaps` seed scoring
- `synthesize_topic`: `current_focus` seeds the planning prompt
- Proactive Research Agent: reads `active_knowledge_gaps` to know what to surface
- MCP tools: `get_reading_stats` can return profile summary

**This is Feature M because it's the prerequisite**: build this first, even
before the entity graph. It has zero dependencies and immediately improves
the recommendation engine.

---

### Layer 1: Knowledge graph (GraphRAG foundation)

---

#### Feature A: Knowledge Graph Entity Index

**Problem it solves**: Break 1 (multi-hop reasoning), Break 5 (entity
deduplication). Foundation for Features B, C, D, F, and Loop 2.

**New tables**:

```sql
CREATE TABLE entities (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    name TEXT NOT NULL,
    entity_type TEXT,  -- 'PERSON', 'CONCEPT', 'ORGANIZATION', 'PAPER', 'TOOL'
    description TEXT,
    article_count INT DEFAULT 0,
    embedding VECTOR(1536),
    created_at TIMESTAMPTZ,
    UNIQUE(user_id, lower(name))
);

CREATE TABLE entity_mentions (
    id UUID PRIMARY KEY,
    entity_id UUID REFERENCES entities(id),
    content_item_id UUID REFERENCES content_items(id),
    user_id UUID REFERENCES users(id),
    context_text TEXT,  -- the sentence containing the mention
    weight FLOAT DEFAULT 1.0
);

CREATE TABLE entity_relations (
    id UUID PRIMARY KEY,
    source_entity_id UUID REFERENCES entities(id),
    target_entity_id UUID REFERENCES entities(id),
    relation_type TEXT,  -- 'DEVELOPED', 'INTRODUCES', 'BUILDS_ON', 'USES', 'CONTRADICTS', 'ENABLES'
    description TEXT,
    weight FLOAT DEFAULT 1.0,
    content_item_id UUID REFERENCES content_items(id)
);
```

**New Celery task** (runs after `generate_chunk_embeddings`):

```python
@celery_app.task
def extract_entities(content_item_id: str):
    item = db.get(ContentItem, content_item_id)
    text = f"{item.title}\n\n{html_to_plain(item.full_text)[:3000]}"

    result = llm_client.structured_chat(
        messages=[{"role": "user", "content": ANALYSIS_PROMPT.format(text=text)}],
        response_model=ArticleAnalysisResponse,
        task=TASK_ARTICLE_ANALYSIS,
    )

    # Upsert entities (case-insensitive dedup across articles)
    for e in result.entities:
        entity = _upsert_entity(user_id=item.user_id, name=e.name,
                                 entity_type=e.type, description=e.description, db)
        _insert_mention(entity.id, content_item_id, db)
        entity.article_count += 1

    # Insert relations
    for r in result.relations:
        source = _get_entity_by_name(item.user_id, r.source, db)
        target = _get_entity_by_name(item.user_id, r.target, db)
        if source and target:
            _insert_relation(source.id, target.id, r.relation_type, r.description, db)

    db.commit()
    # Embed new entities (batch call)
    embed_new_entities.delay(item.user_id)
```

**Ingestion pipeline change** (replaces two tasks with one):

```
Before: generate_embedding → generate_tags → generate_chunk_embeddings → extract_entities
After:  generate_embedding → analyze_article → generate_chunk_embeddings

analyze_article returns domain_tags + concept_tags + entities + relations in a
single gpt-4o-mini call. Replaces separate generate_tags and extract_entities.
See docs/design/systems/tagging-entity-architecture-review.md for full design.
```

**New MCP tool**: `explore_concept(concept_name)` — returns entity neighborhood:
what it connects to, in how many articles, with representative quotes. The
"what does my library know about X" tool.

---

### Layer 2: Retrieval upgrades (uses entity graph)

---

#### Feature B: HippoRAG Connections via Personalized PageRank

**Problem it solves**: Break 1 (multi-hop). Upgrades the existing connections
panel from point-to-point cosine to graph traversal.

**What changes**: The `/search/connections/{highlight_id}` backend query gets
a second retrieval path — PPR over the entity graph — merged with existing
cosine similarity via weighted combination.

```python
# app/api/search.py — augments _connections_for_highlight()

def _ppr_connections(highlight, user_id, db):
    # 1. Load user's entity graph into networkx (Redis-cached, 1hr TTL)
    G = _load_entity_graph(user_id, db)  # nodes=entities, edges=relations
    if G.number_of_nodes() < 10:
        return []  # graph too sparse, skip PPR

    # 2. Seed from entities mentioned in the highlight's article
    seed_entities = _get_article_entities(highlight.content_item_id, db)
    if not seed_entities:
        return []

    # 3. Personalized PageRank from seed nodes
    personalization = {e_id: 1.0 / len(seed_entities) for e_id in seed_entities}
    scores = nx.pagerank(G, personalization=personalization, alpha=0.85, max_iter=100)

    # 4. Top PPR-scoring entities → articles that mention them
    top_entities = sorted(scores, key=scores.get, reverse=True)[:10]
    ppr_articles = _articles_for_entities(top_entities, user_id,
                                           exclude=highlight.content_item_id, db)
    return ppr_articles  # [(article_id, ppr_score)]

# Combined score in _connections_for_highlight():
# final_score = 0.4 * cosine_sim + 0.6 * ppr_score
# (PPR weighted higher — finds non-obvious thematic connections)
```

**Frontend**: ConnectionsPanel shows connection type — "Semantic" (cosine) vs.
"Conceptual" (PPR). Toggle is a small UI change. Zero breaking changes.

---

#### Feature C: Entity-Augmented Hybrid Search

**Problem it solves**: Break 2 (redundant retrieval). Improves search quality
transparently — no UI changes needed.

**What changes**: After standard hybrid search, re-rank results using entity
graph centrality. Articles that are central in the user's knowledge graph
(many entity connections) get a bounded boost.

```python
# app/core/hybrid_search.py — new entity_augmented_search()

def entity_augmented_search(query, user_id, db, limit=10):
    hybrid_results = hybrid_search(query, user_id, db, limit * 2, mode="full")

    # Find entity nodes matching the query
    query_emb = get_or_create_query_embedding(query)
    entity_matches = db.execute("""
        SELECT e.id, 1 - (e.embedding <=> CAST(:q AS vector)) AS sim
        FROM entities e
        WHERE e.user_id = :uid AND e.embedding IS NOT NULL
        ORDER BY sim DESC LIMIT 5
    """, {"q": str(query_emb), "uid": user_id}).fetchall()

    if not entity_matches:
        return hybrid_results  # graceful fallback — graph not yet built

    # Article-level entity centrality scores
    entity_ids = [r.id for r in entity_matches]
    centrality = db.execute("""
        SELECT em.content_item_id, COUNT(*) * AVG(em.weight) AS score
        FROM entity_mentions em
        WHERE em.entity_id = ANY(:eids) AND em.user_id = :uid
        GROUP BY em.content_item_id
    """, {"eids": entity_ids, "uid": user_id}).fetchall()

    boost = {r.content_item_id: r.score for r in centrality}

    # Re-rank: RRF base + bounded entity boost
    reranked = sorted(
        enumerate(hybrid_results, 1),
        key=lambda x: 1/(60 + x[0]) + boost.get(x[1]["id"], 0) * 0.2,
        reverse=True
    )
    return [r[1] for r in reranked[:limit]]
```

Deploy behind `ENTITY_SEARCH_ENABLED` env flag. Run Eval A before and after
to confirm NDCG@5 improves.

---

### Layer 3: Agent features (uses memory + entity graph)

---

#### Feature D: Multi-Agent Synthesis MCP Tool (`synthesize_topic`)

**Problem it solves**: Break 4 (MCP reasoning). Turns sed.i from a search
tool into a research assistant.

**Architecture**: Two modes. `quick` = one-shot synthesis (2 LLM calls).
`deep` = plan → iterative search loop → synthesize (4–6 LLM calls, capped).

```python
# app/mcp/tools/synthesis.py

@mcp.tool()
async def synthesize_topic(
    topic: str,
    depth: Literal["quick", "deep"] = "quick"
) -> SynthesisResponse:
    """Research a topic across your library. Returns structured synthesis
    with perspectives, key concepts, and source citations."""
    user = _current_user()
    db = get_db()

    # Seed from user memory (Dreaming pattern)
    profile = _load_user_profile(user.id, db)
    memory_context = f"User is currently focused on: {profile.current_focus}" if profile else ""

    if depth == "quick":
        results = hybrid_search(topic, user, db, limit=10, mode="full")
        context = _build_context(results, topic, db, max_tokens=4000)
        return llm_client.structured_chat(
            messages=[{"role": "user", "content": QUICK_SYNTHESIS_PROMPT.format(
                topic=topic, context=context, memory=memory_context,
            )}],
            response_model=SynthesisResponse,
            task=TASK_SYNTHESIS,
        )

    # depth == "deep": iterative search loop + entity graph
    # Phase 1: iterative search (Loop 1 — verifier: found ≥ 5 articles)
    results = iterative_search(topic, user, db, target=5, max_iter=3)

    # Phase 2: entity graph context
    entity_context = _entity_neighborhood_context(topic, user.id, db)

    # Phase 3: planning LLM identifies sub-queries and perspectives
    plan = llm_client.structured_chat(
        messages=[{"role": "user", "content": PLANNING_PROMPT.format(
            topic=topic,
            summaries=_brief_summaries(results),
            entity_context=entity_context,
            memory=memory_context,
        )}],
        response_model=SynthesisPlan(
            sub_queries=list[str],
            perspectives=list[str],
        ),
        task=TASK_SYNTHESIS,
    )

    # Phase 4: run sub-queries (capped at 3)
    all_results = list(results)
    for sub_query in plan.sub_queries[:3]:
        sub = hybrid_search(sub_query, user, db, limit=5, mode="full")
        all_results.extend(sub)

    # Phase 5: deduplicate + synthesize
    unique = {r["id"]: r for r in all_results}
    context = _build_context(list(unique.values()), topic, db, max_tokens=6000)

    return llm_client.structured_chat(
        messages=[{"role": "user", "content": DEEP_SYNTHESIS_PROMPT.format(
            topic=topic, perspectives=plan.perspectives, context=context,
        )}],
        response_model=SynthesisResponse,
        task=TASK_SYNTHESIS,
    )


class SynthesisResponse(BaseModel):
    summary: str                           # 2-3 sentence overview
    perspectives: list[PerspectiveItem]   # competing viewpoints
    key_concepts: list[str]               # entity names surfaced
    sources: list[SourceCitation]         # article IDs + quotes
    confidence: Literal["high", "medium", "low"]
```

---

#### Feature E: RAPTOR Cluster Summaries

**Problem it solves**: Break 3 (cold-start), fixes `preferred_tags` decay bug.

**What changes**: Extends existing `cluster_user_tags_task` to generate LLM
summaries and embeddings per cluster. Replaces tag-overlap scoring in the
recommendation engine with cluster-embedding similarity.

```python
# tasks/clustering.py — after computing clusters

def generate_cluster_summary(cluster, db):
    snippets = _gather_snippets(cluster.article_ids, db)  # top 5 titles + descriptions
    summary = llm_client.chat(
        messages=[{"role": "user", "content": CLUSTER_SUMMARY_PROMPT.format(
            label=cluster.label, articles=snippets,
        )}],
        task=TASK_CLUSTER_SUMMARY,
    )
    cluster.summary = summary
    cluster.summary_embedding = llm_client.embed([summary])[0]
    db.commit()

# New columns: reading_clusters.summary TEXT,
#              reading_clusters.summary_embedding VECTOR(1536)
```

**Recommendation engine fix** (replaces uncapped tag-overlap Factor 3):

```python
# content.py get_recommended_content() — new Factor 3
cluster_embeddings = _load_cluster_embeddings(user.id, db)
if cluster_embeddings and item.embedding:
    cluster_sims = [cosine_similarity(item.embedding, ce) for ce in cluster_embeddings]
    score += max(cluster_sims) * 20  # capped, decays as clusters change
```

Also feeds Feature D (`synthesize_topic` uses cluster summaries as context
seed) and Feature F (Proactive Research Agent identifies gaps per cluster).

---

#### Feature F: Proactive Research Agent

**Problem it solves**: Every sed.i feature is reactive. This is the first
autonomous feature — runs without user action.

**What it does**: A scheduled agent (nightly Celery beat, runs after
`consolidate_memory`) that detects knowledge gaps from `user_memory`, generates
targeted research angles, and writes a structured daily brief to a special
"Daily Brief" list via `update_draft()`.

```python
# app/tasks/proactive_agent.py

@celery_app.task
def run_proactive_agent(user_id: str):
    db = _get_db()
    profile = _load_user_profile(user_id, db)
    if not profile or not profile.active_knowledge_gaps:
        return  # no gaps identified yet

    # Loop 2: knowledge gap detection over entity graph
    gaps = _detect_gaps_via_ppr(user_id, profile, db)  # see §5

    # For each gap (capped at 3): find relevant articles already in library
    surfaced = []
    for gap in gaps[:3]:
        results = hybrid_search(gap.concept, user_id, db, limit=3, mode="full")
        if results:
            surfaced.append({"gap": gap.concept, "articles": results})

    if not surfaced:
        return  # nothing worth surfacing

    # Generate the daily brief via LLM
    brief = llm_client.structured_chat(
        messages=[{"role": "user", "content": BRIEF_PROMPT.format(
            focus=profile.current_focus,
            gaps=gaps,
            surfaced=surfaced,
        )}],
        response_model=DailyBrief(
            headline=str,
            sections=list[BriefSection],
            orphaned_highlights=list[str],  # highlights with 0 connections
        ),
        task=TASK_SYNTHESIS,
    )

    # Write to "Daily Brief" list via the same update_draft() used by MCP
    brief_list = _get_or_create_brief_list(user_id, db)
    _write_brief_as_draft(brief_list.id, brief, db)
```

**Eval**: track whether articles surfaced in the brief get read (PostHog:
`article_opened` with `source=daily_brief`). 0% click-through = noise.
The verifier is behavioral and honest.

---

#### Feature G: Writing Agent with Memory (`assist_draft`)

**Problem it solves**: Draft workspace surfaces relevant articles passively.
User still manually extracts quotes and weaves them into writing. This agent
does the threading.

**What it does**: A new MCP tool `assist_draft(list_id, instruction)` that
reads the current draft, searches the library for relevant material, extracts
the strongest quotes from the user's own highlights, drafts a paragraph with
inline citations, and writes it back via `update_draft()`. Bounded write scope:
only calls `update_draft` — cannot touch the library.

```python
# app/mcp/tools/synthesis.py

@mcp.tool()
async def assist_draft(
    list_id: str,
    instruction: str,  # e.g. "write the intro using my reading as sources"
) -> DraftAssistResult:
    """Assist writing a draft using your library as source material."""
    user = _current_user()
    db = get_db()

    # 1. Load current draft state
    draft = _get_draft(list_id, user.id, db)

    # 2. Search library for material relevant to the instruction
    results = iterative_search(instruction, user, db, target=5, max_iter=3)

    # 3. Pull the user's own highlights from those articles
    #    (user's own words are the best source for their writing voice)
    article_ids = [r["id"] for r in results]
    highlights = _get_highlights_for_articles(article_ids, user.id, db)

    # 4. Draft a paragraph with inline citations
    draft_addition = llm_client.structured_chat(
        messages=[{"role": "user", "content": DRAFT_ASSIST_PROMPT.format(
            instruction=instruction,
            current_draft=draft.content[:2000] if draft else "",
            sources=_format_sources(results, highlights),
        )}],
        response_model=DraftAddition(
            content=str,   # markdown paragraph with [Author, Title] citations
            citations=list[Citation],
        ),
        task=TASK_SYNTHESIS,
    )

    # 5. Write back — the only write operation, explicitly bounded
    updated = _append_to_draft(list_id, draft_addition.content, user.id, db)

    return DraftAssistResult(
        added=draft_addition.content,
        citations=draft_addition.citations,
        source_count=len(results),
    )
```

**Eval**: user edit distance after `assist_draft` runs. Low edit distance =
output was usable. High edit distance = noise. Faithfulness score (are the
citations real?) runs as a Braintrust eval.

---

#### Feature H: Self-Improving Tag Agent

**Problem it solves**: Tagging is static. User behavior (deleting tags, reading
tagged articles) is a free reward signal that's never used.

**What it does**: A weekly Celery task that collects tag behavioral signals and
uses them to personalize the tagging prompt with few-shot examples drawn from
the user's own kept tags.

```python
# app/tasks/tagging.py — extends generate_tags

@celery_app.task
def build_personalized_tag_examples(user_id: str):
    """Collect behavioral signal → update few-shot examples in tagging prompt."""
    db = _get_db()

    # Implicit reward signal: tags on articles user completed reading
    kept_tags = db.execute("""
        SELECT DISTINCT unnest(tags) AS tag
        FROM content_items
        WHERE user_id = :uid
          AND is_read = TRUE
          AND array_length(tags, 1) > 0
          AND deleted_at IS NULL
        ORDER BY tag
        LIMIT 20
    """, {"uid": user_id}).fetchall()

    # Implicit negative signal: tags on articles user never opened after saving
    # (proxy for "these tags didn't make the article sound interesting")
    # More complex — defer to v2 of this feature

    if len(kept_tags) < 5:
        return  # not enough signal yet

    # Write personalized examples to user_profiles.writing_style_notes
    # The generate_tags task reads this and prepends to the prompt
    examples = _format_tag_examples(kept_tags, db)
    _update_profile_tag_examples(user_id, examples, db)

# In generate_tags task:
# user_examples = _load_profile_tag_examples(item.user_id, db)
# prompt = (user_examples + GENERIC_TAG_EXAMPLES) if user_examples else GENERIC_TAG_EXAMPLES
```

**Eval**: tag retention rate (% of AI-generated tags user keeps) before vs.
after personalization. If it goes up → personalization is working.

---

#### Feature I: MCP Skills Layer

**Problem it solves**: Calling agents (Claude Desktop, Claude.ai) stumble
through the sed.i tool surface without guidance. Skills tell them how to
sequence tools optimally.

**What it does**: Add a Skills metadata block to the MCP server that guides
tool sequencing. Low implementation cost; high leverage.

```python
# app/mcp/server.py — add alongside tool registration

SEDI_SKILLS = """
## sed.i Research Skills

### Researching a topic
1. search_content(topic) — broad first pass
2. If results < 3: find_similar() on the best result, OR reformulate the query
3. For multi-part questions: search_content() per concept separately
4. explore_concept(name) — use when asking about a specific person, org, or concept
5. get_highlights() — always check; surfaces the user's own thinking on a topic
6. summarize_list() — cached; prefer over reading full articles for overview

### Writing assistance
1. get_draft(list_id) — read current state first
2. get_highlights(list_id) — user's own annotations are the best source material
3. search_content() — for additional library sources
4. assist_draft(list_id, instruction) — when user wants AI to draft from sources

### Understanding reading patterns
1. get_reading_stats() — high-level numbers
2. synthesize_topic(topic, depth='quick') — thematic overview of a topic
3. list_lists() → summarize_list() — per-list summaries
"""
```

No new endpoints. Purely additive to the existing MCP server registration.

---

## 7. Eval framework

Evals run at two layers: offline (labeled dataset, tracked metrics per build)
and online (PostHog behavioral events). Offline metrics use RAGAS for
faithfulness and standard IR metrics (MRR, NDCG@k, Recall@k) for retrieval.
Online metrics measure whether users actually engaged with what was surfaced.

Baselines must be established before any feature work. Without a pre-feature
baseline there is no way to attribute a metric change to a specific change.

### Eval 0: Baselines (run first, before any feature work)

```python
# tests/evals/baseline.py
# Establish these numbers before touching a single line of feature code.
# Every subsequent eval compares against these.

BASELINE_METRICS = {
    "retrieval_mrr": None,          # fill in from Eval A run 1
    "retrieval_ndcg_at_5": None,    # fill in from Eval A run 1
    "tag_retention_rate": None,     # fill in from PostHog query
    "connection_ctr": None,         # % connections leading to reads
    "search_first_click_rank": None # avg rank of first clicked result
}
```

### Eval A: Retrieval quality (MRR, NDCG@5, Recall@10)

**What**: Does the search system surface the right articles?

**Dataset**: 30+ (query, library, relevant_ids) cases seeded into a test
database. Cover: factual recall, multi-hop questions, author/tag filters, cold
queries on sparse libraries.

```python
RETRIEVAL_EVAL_CASES = [
    {
        "query": "how does attention mechanism work in transformers",
        "relevant_ids": ["attention_paper_id", "bert_paper_id", "gpt3_paper_id"],
        "irrelevant_ids": ["cooking_pasta_id", "piano_lessons_id"],
        "expected_rank_1": "attention_paper_id",
    },
    # ...
]
```

**Metrics**:
```python
def mrr(ranked, relevant):
    for i, id in enumerate(ranked, 1):
        if id in relevant: return 1 / i
    return 0

def ndcg_at_k(ranked, relevant, k=5):
    dcg = sum(1/math.log2(i+1) for i,id in enumerate(ranked[:k],1) if id in relevant)
    ideal = sum(1/math.log2(i+1) for i in range(1, len(relevant)+1))
    return dcg/ideal if ideal else 0
```

**Run against**:

1. Current hybrid search (baseline — run before any feature work)
2. Entity-augmented search (Feature C)
3. Entity graph local search (Feature A)

**Location**: `content-queue-backend/tests/evals/test_retrieval_quality.py`

---

### Eval B: Entity extraction quality (F1)

**What**: Does entity extraction produce accurate entities and relations?

**Dataset**: 10 manually labeled articles with gold-standard entity lists.

```python
def entity_f1(extracted, expected):
    extracted_norm = {n.lower().strip() for n in extracted}
    expected_norm = {n.lower().strip() for n in expected}
    if not extracted_norm: return 0.0
    p = len(extracted_norm & expected_norm) / len(extracted_norm)
    r = len(extracted_norm & expected_norm) / len(expected_norm)
    return 2*p*r/(p+r) if (p+r) else 0
```

**Gate**: entity F1 ≥ 0.70 before enabling Feature A in production.

---

### Eval C: Synthesis quality (RAGAS via Braintrust)

**What**: Does `synthesize_topic` produce accurate, grounded answers?

RAGAS metrics (arXiv:2309.15217):

| Metric | Definition |
|--------|-----------|
| **Faithfulness** | Every claim supported by a retrieved source |
| **Answer Relevance** | Answer addresses the question |
| **Context Precision** | Retrieved chunks were useful |
| **Context Recall** | Retrieved all relevant information |

```python
# tests/evals/test_synthesis_quality.py
import braintrust

experiment = braintrust.Experiment(project="sedi-synthesis-eval")

for case in SYNTHESIS_EVAL_CASES:
    answer = synthesize_topic(case["topic"], depth="deep")
    experiment.log(
        input=case["topic"],
        output=answer.summary,
        expected=case["expected_key_points"],
        scores={
            "faithfulness": faithfulness_score(answer.summary, answer.source_texts),
            "coverage": key_point_coverage(answer.summary, case["expected_key_points"]),
            "source_count": len(answer.sources),
        }
    )
```

Braintrust is already wired (`BRAINTRUST_API_KEY`). This just needs the eval
scripts to exist.

---

### Eval D: Agent loop effectiveness

**What**: Does the iterative search loop (Feature D) actually find more
relevant articles than one-shot retrieval?

```python
LOOP_EVAL_CASES = [
    {
        "topic": "the relationship between dopamine and habit formation",
        "min_relevant_articles": 3,
        "relevant_ids": ["dopamine_article", "habit_loop_article", "reward_circuit_article"],
    },
]

def loop_vs_oneshot(case):
    oneshot = hybrid_search(case["topic"], limit=10, mode="full")
    loop_result = iterative_search(case["topic"], target=5, max_iter=3)

    oneshot_recall = recall_at_k([r["id"] for r in oneshot], case["relevant_ids"], k=10)
    loop_recall = recall_at_k([r["id"] for r in loop_result], case["relevant_ids"], k=10)
    return {"oneshot": oneshot_recall, "loop": loop_recall, "delta": loop_recall - oneshot_recall}
```

If delta is consistently ≤ 0 across cases, the loop adds cost with no benefit
→ remove it. The loop only stays if it measurably improves recall.

---

### Eval E: Multi-hop reasoning accuracy

**What**: Can the system answer questions requiring information from multiple
articles, not just one?

```python
MULTI_HOP_CASES = [
    {
        "query": "What does my reading say about how sleep affects learning?",
        "requires_articles": ["sleep_study", "memory_consolidation_paper"],
        "single_article_answerability": False,  # cannot be answered from one article
    },
]
```

Baseline: run through `query_library` (text-to-SQL). Test: run through
`synthesize_topic(depth="deep")`. Deep synthesis should score significantly
higher on multi-hop questions.

---

### Eval F: Online behavioral metrics (PostHog)

Add these events — PostHog is already instrumented:

```python
# Tag behavior (feeds Feature H eval)
posthog.capture(user_id, "tag_deleted", {"tag": tag, "article_age_days": int})
posthog.capture(user_id, "tag_kept_on_read", {"tag": tag})

# Connection engagement (feeds Feature B eval)
posthog.capture(user_id, "connection_clicked", {
    "type": "semantic" | "conceptual",  # cosine vs PPR
    "led_to_read": bool,
})

# Search click position (feeds Eval A online)
posthog.capture(user_id, "search_result_clicked", {
    "rank": int,
    "entity_boost": bool,  # was Feature C active?
})

# Daily brief engagement (feeds Feature F eval)
posthog.capture(user_id, "article_opened", {
    "source": "daily_brief" | "search" | "recommendation" | ...,
})

# Synthesis usage
posthog.capture(user_id, "synthesis_completed", {
    "depth": "quick" | "deep",
    "loop_iterations": int,  # how many search iterations were needed
    "source_count": int,
})
```

**Key derived metrics**:
- **MRR@online**: avg reciprocal rank of first clicked search result
- **Connection CTR**: % of shown connections → article read
- **Brief CTR**: % of daily brief articles → article opened
- **Tag retention rate**: kept / (kept + deleted) per week

---

## 8. Implementation sequencing

Ordered by: dependency order first, then risk, then signal value. Each sprint
delivers something independently usable — no "big bang" launches.

```
╔══════════════════════════════════════════════════════════════╗
║  BEFORE EVERYTHING: run Eval 0 baselines                     ║
║  Measure: MRR, NDCG@5, tag retention rate, connection CTR   ║
║  Lock these numbers. They are your before-state.            ║
╚══════════════════════════════════════════════════════════════╝

Sprint 1 — Memory foundation + first measurement (1–2 weeks)
├── Feature M: user_memory + UserProfile tables + consolidate_memory task
│     - Zero dependencies, zero risk to existing features
│     - Immediately improves recommendation engine (current_focus seed)
│     - Foundation for everything in Sprint 3+
│
└── Eval A run 1: establish retrieval baseline
      - 30 labeled cases, measure MRR + NDCG@5
      - These numbers lock the baseline before any search changes

Sprint 2 — Entity graph (2–3 weeks)
├── Feature A: entity extraction + entity/mentions/relations tables
│     - New Celery task after chunk embedding
│     - Alembic migration (new tables only, non-breaking)
│     - Backfill: run extract_entities on existing articles
│
├── Feature E: RAPTOR cluster summaries
│     - Extends existing clustering task (already runs weekly)
│     - Fixes preferred_tags decay bug in recommendation engine
│     - New columns on reading_clusters (non-breaking migration)
│
└── Eval B: entity extraction F1
      - Gate: F1 ≥ 0.70 before enabling entity features in search
      - Run on 10 manually labeled articles

Sprint 3 — Search quality (2 weeks)
├── Feature C: entity-augmented hybrid search
│     - Reads entity graph from Sprint 2
│     - Feature-flagged: ENTITY_SEARCH_ENABLED env var
│     - Graceful fallback if entity graph not yet built
│
└── Eval A run 2: compare vs baseline
      - MRR delta, NDCG@5 delta
      - If either metric regresses → revert Feature C
      - If both improve → ship to production, update Eval 0 baseline

Sprint 4 — Connections upgrade (2 weeks)
├── Feature B: HippoRAG PPR connections
│     - Reads entity graph from Sprint 2
│     - Adds networkx to requirements.txt
│     - Augments existing /search/connections endpoint (additive)
│
└── Eval F: connection CTR (PostHog)
      - Measure: semantic vs conceptual connection click-through
      - If PPR connections are clicked less → tune weighting, don't revert

Sprint 5 — Agent features (3–4 weeks)
├── Feature D: synthesize_topic MCP tool
│     - Includes iterative search loop (Loop 1)
│     - New MCP tool, no UI changes
│     - Reads from Feature M (user memory) and Feature A (entity graph)
│
├── Feature G: Writing agent (assist_draft MCP tool)
│     - Bounded write access: only update_draft()
│     - Depends on Feature D's iterative_search function
│
└── Eval C: synthesis quality (Braintrust)
      Eval D: loop effectiveness (loop vs one-shot recall)
      Eval E: multi-hop accuracy (synthesize vs query_library)

Sprint 6 — Autonomous + self-improving (2–3 weeks)
├── Feature F: Proactive Research Agent (daily brief)
│     - Scheduled beat task, runs after consolidate_memory
│     - Reads Feature M + Feature A (PPR gap detection)
│     - Writes to "Daily Brief" list via update_draft()
│
├── Feature H: Self-improving tag agent
│     - Weekly task, builds personalized few-shot examples
│     - Reads PostHog behavioral signal (tag retention)
│
└── Feature I: MCP Skills layer
      - Additive metadata on existing MCP server
      - Zero risk, implement any time

Sprint 7 — Measurement and calibration (ongoing)
└── Eval F: full behavioral dashboard (PostHog)
      - MRR@online trend over time
      - Brief CTR vs recommendation CTR vs search CTR
      - Tag retention rate before/after Feature H
      - This is what you show in interviews and put in your portfolio
```

---

## 9. Job signal map

| What you built | Technique name | Company it signals for |
| --- | --- | --- |
| Entity graph + extraction | Named entity recognition, knowledge graph construction | Glean, Notion, Perplexity, any KM/search startup |
| HippoRAG PPR connections | Graph algorithms, implementing papers | AI labs, research eng, search companies |
| Iterative search loop | ReAct, loop engineering, verifier design | Any company shipping agents in production |
| Dreaming / memory consolidation | Episodic memory, agent lifecycle management | Anthropic, Letta, any company with persistent agents |
| RAGAS + Braintrust evals | RAG evaluation, LLM-as-judge, eval design | Every AI product company — this is the rarest skill |
| Four-tier memory architecture | Agent memory systems, MemGPT/Letta pattern | Anthropic, AI platform companies |
| Self-improving tags | Behavioral RLHF, implicit feedback loops | AI personalization companies |
| MCP Skills | MCP protocol, tool design, agent-tool interface | Any company building on MCP |
| Online behavioral metrics | Connecting model quality to product metrics | Growth eng, PM-adjacent ML roles |

The most useful portfolio artifact is a before/after metric comparison:
NDCG@5 before and after entity-augmented search, plus PostHog data on click
depth. Eval design is the least common skill in AI product roles — having
Braintrust experiment logs with faithfulness scores and PostHog behavioral
data covers both offline and online layers.

**Interview answer for "what multi-agent system have you built"**:
> "A synthesis tool with an iterative search loop — runs until it finds 5
> relevant articles or hits 3 iterations. The verifier is a count check, not
> LLM judgment. I measured it against one-shot retrieval using recall@10:
> ~23% coverage improvement on multi-hop queries, ~40% latency increase."

---

## 10. Decision record hooks

| Feature | ADR needed | Key decision |
|---------|-----------|--------------|
| Feature M | ADR-0008-user-memory | Schema design for episodic vs. semantic memory. Beat schedule vs. event-triggered consolidation. |
| Feature A | ADR-0009-entity-graph | Postgres + networkx vs. Neo4j/Weaviate. Recommendation: Postgres at <50k entities. Migration trigger: >50k OR graph queries >100ms p95. |
| Feature B | ADR-0010-ppr-connections | PPR damping factor (0.85), max iterations (100), seed weighting. When to switch from networkx to Redis adjacency list. |
| Feature D | ADR-0011-synthesis-agent | Sub-query cap (3), max tokens per synthesis (6000), latency budget (<10s for deep). How to handle loop failures gracefully. |
| Feature H | ADR-0012-tag-personalization | What counts as a positive signal (kept tag on completed read) vs. negative (deleted tag). Minimum signal threshold before enabling. |
| Eval infra | ADR-0013-eval-framework | Braintrust vs. LangSmith vs. custom. Recommendation: Braintrust (already wired). Who runs evals, what's the gate for shipping. |

---

## Appendix A: Papers to read (in order)

1. **Microsoft GraphRAG**: arXiv:2404.16130 — "From Local to Global: A Graph RAG Approach to Query-Focused Summarization" (Edge et al., 2024)
2. **LightRAG**: arXiv:2410.05779 — "LightRAG: Simple and Fast Retrieval-Augmented Generation" (Guo et al., 2024, HKUST)
3. **HippoRAG**: arXiv:2405.14831 — "HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models" (Gutiérrez et al., 2024)
4. **RAPTOR**: arXiv:2401.18059 — "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval" (Sarthi et al., Stanford, 2024)
5. **RAGAS**: arXiv:2309.15217 — "RAGAS: Automated Evaluation of Retrieval Augmented Generation" (Es et al., 2023)
6. **MemGPT**: arXiv:2310.08560 — "MemGPT: Towards LLMs as Operating Systems" (Packer et al., UC Berkeley, 2023)
7. **PathRAG**: arXiv:2502.14902 — "PathRAG: Pruning Graph-based Retrieval Augmented Generation with Relational Paths" (Chen et al., 2025)
8. **Contextual Retrieval**: Anthropic blog, Sept 2024 — already partially implemented in sed.i's chunk prefixes
9. **Anthropic Dreaming**: announced May 6, 2026 at Code with Claude — the pattern behind Feature M

---

## Appendix B: Technology stack additions

| Component | Tech | Why | Where |
|-----------|------|-----|-------|
| Graph algorithms | `networkx` | PPR traversal (Feature B). In-process, no new service | `requirements.txt` |
| Entity extraction schema | `instructor` (already in stack) | Structured entity/relation output | Uses existing `llm_client.structured_chat()` |
| Eval tracking | Braintrust (already in stack) | Already wired, just needs eval scripts | New `tests/evals/` files |
| Agent memory tables | Alembic migration | Standard established pattern | New migration |
| Entity graph tables | Alembic migration | Non-breaking (new tables only) | New migration |

All features use existing infrastructure: Postgres, Redis, Celery, the LLM
client, Braintrust, and PostHog. No new services or infrastructure proposed.
