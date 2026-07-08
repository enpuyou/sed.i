---
type: research
status: reference
last_updated: 2026-07-03
consumer: backend, product
---

# Search Research: Full-Text Body Search and Entity Graph Value

Two questions answered with reference to real system behavior where available.

---

## Question 1: Full-text body search

### What real read-it-later apps actually do

Every major read-it-later app indexes article bodies. This is industry standard, not a premium edge case:

- **Readwise Reader** indexes the full text of all Library documents (titles, authors, and body). Their docs explicitly state "full-text offline search that will search the full text of all your Library documents." Feed items are excluded from the index; moving them to Library triggers indexing. As of 2025–2026, they also offer a server-side search toggle with faster indexing and better accuracy than the local index.
- **Instapaper** gates full-text body search behind Premium ($5.99/month). Free tier searches only titles and descriptions.
- **Pocket** (shut down July 2025) indexed article bodies.
- **Notion / Obsidian / Logseq** search the complete content of all stored documents, with no distinction between metadata and body. Body search is the baseline assumption in note-taking tools.

The product implication: users of these tools expect to type any word from an article and find it. Not offering this creates a qualitative gap against the reference products.

### PostgreSQL tsvector at sed.i's scale

sed.i has 500–5,000 articles. At that scale, tsvector full-text search is effectively free.

The best available benchmark (Daniela Baron, 2024): on 100,000 rows, a query against a persisted tsvector column with a GIN index returns in **~2.4ms**. Without a GIN index (on-the-fly tsvector), the same query takes **~283ms** because it does a sequential scan over all rows. The speedup is ~118×.

At 5,000 articles — 50× fewer rows than that benchmark — the unoptimized path would still return in under 15ms, and the indexed path in under 1ms. A second benchmark (VectorChord, 10M rows, 2024) shows ~877ms optimized vs ~41 seconds unoptimized. Scaling linearly back to 5,000 rows: indexed is sub-millisecond, unoptimized is under 20ms.

Cost is zero: tsvector and GIN indexes are included in PostgreSQL. No external service, no embedding tokens, no API latency.

Implementation is three lines: add a generated tsvector column over `body_text`, create a GIN index on it, query with `@@` and `to_tsquery`. The existing metadata tsvector column in sed.i already demonstrates the pattern.

### When full-text search wins over semantic search

| Scenario | Why full-text wins |
|----------|-------------------|
| User remembers an exact phrase from the article | Semantic search finds thematically related text; it does not guarantee exact substring retrieval. A rare term — a person's name, a product name, a specific URL — that appears once in a 3,000-word article may not dominate the chunk embedding enough to surface. |
| Error codes, URLs, technical identifiers | `"ECONNRESET"`, `"RFC 7231"`, `"CVE-2024-12345"` — these are zero-context tokens; semantic distance from them is meaningless. Only exact match finds them. |
| Short, specific queries | `"Thacker Pass"`, `"Balaji Srinivasan"` — names are high-signal; semantic search on a name query often retrieves articles about vaguely related people or topics instead. |
| Boolean must-include logic | Querying for articles that mention both Person A and Organization B explicitly. Semantic similarity does not model conjunction. |

| Scenario | Why semantic wins |
|----------|------------------|
| User remembers the gist but not the words | "something about why remote work hurts junior employees" finds articles using "distributed teams", "onboarding", "mentorship loss" without any of those words in the query. |
| Concept synonyms | `"carbon capture"` finds articles that only say `"CO2 removal"` or `"direct air capture"`. |
| Cross-language | Less relevant for sed.i but relevant in theory. |

**The practical conclusion:** at sed.i's scale, both run under 5ms. Running them in parallel and merging results is cheaper than choosing. The current hybrid search already does semantic + keyword-on-metadata; adding keyword-on-body makes the keyword leg more complete.

---

## Question 2: Entity and relation types that add value beyond full-text and semantic

### What full-text + semantic already cover

After adding body text to tsvector search, the system can find:
- Any article mentioning a specific name, term, or phrase (full-text)
- Any article semantically related to a concept (whole-article embedding)
- Any article containing a passage semantically related to part of a query (chunk embeddings)

What neither covers: **structural relationships between named things across articles that were never in the same document.**

### The gap: multi-hop and cross-article relational queries

Consider a user who saved 500 articles over two years. The query "show me everything about how OpenAI's safety team influenced policy" is not answered by:
- Full-text search on "OpenAI safety policy" — finds articles mentioning all three words but misses articles that discuss the influence through different vocabulary
- Semantic search — retrieves articles thematically similar to the query but cannot reason about the directed relationship "influenced → policy"

What an entity graph answers that neither can: **traverse a chain.** `OpenAI` → `employs` → `[Person]` → `testified before` → `Senate committee` → `resulted in` → `EU AI Act`. No single article contains that chain. Each hop lives in a different saved document. Full-text and semantic find nodes; the graph finds paths between them.

Microsoft's GraphRAG paper (2024) quantified this specifically: on "global" questions about an entire corpus ("What are the main themes?", "How do tech leaders view privacy laws?"), GraphRAG outperformed vector RAG substantially on comprehensiveness and diversity. The structural reason: community summaries aggregate entity clusters across documents that never co-appear in a single passage.

### Specific entity + relation combinations that create irreplaceable value

**1. Person → [relation] → Organization, cross-article**

Query intent: "Which researchers moved from academia to safety labs, and what changed in their public positions?"

Full-text finds articles mentioning a researcher's name. Semantic finds topically related articles. Neither links `[Person] worked at [University]` (article A, 2021) to `[Person] now at [Lab]` (article B, 2024) to `[Person] published [paper]` (article C, 2024). The entity graph holds all three as typed edges on the same node and can answer "show me the arc of this person's career."

Precondition: entity resolution must be reliable — `"Sam Altman"` and `"Altman"` must resolve to the same node. sed.i currently stores full canonical names, which partially addresses this.

**2. Organization → [relation] → Concept, as a stance tracker**

Query intent: "Which organizations have reversed positions on AI regulation?"

Full-text matches articles containing both an org name and regulation terms. It cannot answer whether the org's position changed direction. A relation like `[Meta] publicly opposed → [AI regulation]` (article A, 2022) and `[Meta] endorsed → [EU AI Act]` (article B, 2024) expresses a contradiction. The graph surfaces it; keyword and semantic do not.

This is the highest-value entity type for a read-it-later app: **institutional stances over time.** Users accumulate news articles over years; the graph turns that into a timeline of positions.

**3. Concept → [influenced] → Concept, for intellectual lineage**

Query intent: "What prior ideas does effective altruism draw from?"

Semantic search on "effective altruism" retrieves EA articles. It does not surface that those articles cite utilitarian philosophy, longtermism arguments, and Peter Singer's work as foundations — unless the user already knows to search for those terms. The entity graph, where sed.i's existing relation extraction already targets intellectual influence as highest-value, creates the edges explicitly. A graph traversal from `effective altruism` → `influenced by` surfaces the conceptual parents.

This is the one case where sed.i's current entity graph is already implemented and differentiated. The eval results in `entity-graph-search.md` should indicate whether the retrieval quality is demonstrated.

**4. Paper / Work → [cites or extends] → Paper / Work**

For users who save academic papers and technical posts: citation and extension relations create a lineage graph that neither full-text nor semantic can reconstruct. `"paper X extends paper Y by adding Z"` requires extracting that predicate from the text — semantic search retrieves papers that are similar in topic, not papers that are explicitly in a progression.

### What is proven vs. speculative in the PKM tool literature

**Proven, observable in deployed products:**

- Readwise Reader's full-text search across saved article bodies is demonstrably shipped and used at scale. It is confirmed baseline functionality, not a research prototype.
- Obsidian and Logseq's graph views show note-to-note links based on explicit `[[wikilinks]]` created by the user. The graphs are structurally real but only as rich as the user's manual linking effort. Auto-generated entity graphs from article text are not native to these tools; they require plugins (e.g., Breadcrumbs for typed relations).
- InfraNodus (a third-party tool that wraps Obsidian/Logseq graphs) claims to surface "structural gaps" — pairs of topics that are frequently co-discussed elsewhere but have no link in the user's notes. This is a real and shipped feature, not speculative.

**Speculative or undemonstrated at personal PKM scale:**

- Microsoft GraphRAG's community summaries are benchmarked on large corpora (news datasets, books). Performance on 500 personal articles, where entities are sparse and relations thin, is extrapolation. Thin graphs have few multi-hop paths; the value degrades toward zero if the average article yields only 3–4 entities and 1–2 relations.
- "Serendipitous discovery" claims in PKM literature are largely anecdotal. No published controlled study demonstrates that auto-generated entity graphs in personal note corpora produce discoveries that users act on at a measurable rate. The Roam/Obsidian communities report individual positive examples, not population statistics.
- Entity resolution at personal scale is unsolved without a reference database. `"the Fed"`, `"Federal Reserve"`, `"FOMC"` must resolve to one node. sed.i's current extraction uses full canonical names but does no cross-article normalization pass. Without it, multi-hop traversal is broken by entity fragmentation.

### Practical recommendation for sed.i

Given existing capabilities (chunk-level semantic, whole-article semantic, metadata keyword), the additions that create real incremental value in priority order:

1. **Body tsvector search** — zero new infrastructure, sub-millisecond latency, closes the exact-recall gap, matches industry standard. Implement first.
2. **Cross-article entity timeline** (Person/Organization → stance/role over time) — requires entity resolution, which is the hard part. High value if solved; low value if `"OpenAI"` appears as three different nodes due to capitalization or abbreviation variance.
3. **Intellectual influence traversal** — already partially served by the current relation extraction. The missing piece is a UI that lets users traverse the graph, not just see entities per article. The extraction is done; the traversal is not exposed.
4. **Community-level theme summarization (GraphRAG-style)** — premature at 500 articles. Revisit when the library contains 2,000+ articles with well-resolved entities.
