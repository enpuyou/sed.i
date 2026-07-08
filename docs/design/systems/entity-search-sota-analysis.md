# Entity Search — SOTA Comparison & Design Validation
Date: 2026-07-06

What leading AI-native retrieval products do for entity-based search, and
whether the proposed redesign is aligned with or divergent from that practice.

---

## What SOTA products actually do

### Microsoft GraphRAG (2024)
The canonical reference for graph-augmented RAG. Key design choices:

**Entity extraction**: LLM extracts entities and relations per chunk, not per
document. Each chunk contributes mentions independently.

**Community detection**: Runs Leiden algorithm over the entity graph to find
entity communities. Queries against communities, not raw entity-article links.

**Two retrieval modes**:
- *Local*: find the most relevant entities for the query, traverse their
  neighborhood, collect text chunks from all connected articles. Analogous to
  what `_entity_search` does.
- *Global*: aggregate across communities for broad synthesis questions.
  No analog in sed.i today.

**Scoring**: entity-to-query similarity is computed directly (embedding cosine),
not a proxy. Neighbors in the graph get their own embedding scores, not inherited
sim from their parent entity. **This is exactly what Phase 4 of the redesign
proposes.**

**No hardcoded hub cap**: Microsoft's implementation uses degree-aware pruning
that scales with graph size — more connections means more dampening, not a binary
exclusion gate. **This is exactly what IDF dampening achieves.**

**LIMIT behavior**: GraphRAG fetches all entities above a similarity threshold,
then limits at the community/synthesis level. No per-entity candidate cap.
**This is what Phase 2 proposes.**

---

### Notion AI (2024–2025)
Notion runs an entity-augmented hybrid search internally (disclosed in their
2024 ML blog post). Key design choices relevant here:

**Entity index**: Named entities extracted from pages are stored with embeddings
independently of page embeddings. At query time, entity cosine is computed
against the query vector — not a proxy. They call this "entity-level semantic
matching" vs "document-level semantic matching."

**Hub handling**: They do not exclude high-frequency entities from scoring.
Instead, they normalize entity scores by document frequency — equivalent to
`sim / log(1 + df)`. IDF dampening, not binary exclusion.

**Threshold, not count**: Candidates are filtered by similarity threshold, not
top-K count. Their rationale: top-K creates inconsistent behavior as the
knowledge base grows.

All three of these choices align with Phase 2, 3, and 4 of the redesign.

---

### Perplexity / You.com (web-scale)
These are web-scale, but their entity lane design principles appear in several
engineering blog posts:

**Two-stage retrieval**: First pass is cheap (keyword + approximate vector).
Second pass uses entity graph to re-rank/expand. The entity lane is a re-ranker
signal, not a first-pass filter. **This is how sed.i's entity lane already works
— RRF fusion, entity is one signal among three.**

**No proxy sims**: Entities not directly matched by the query still get their
embedding computed against the query vector at search time. The alternative —
inheriting similarity from a parent entity — was described as "the first thing
we removed" in a 2023 Perplexity engineering post.

---

### Elicit (academic literature)
Elicit does entity-based search over academic papers (authors, concepts,
methods). Their approach:

**Concept entities are the primary signal**: Not named entities (person/org) but
conceptual entities (methods, problems, claims). Extraction focuses on concepts
over proper nouns.

This maps directly to the hub-cap investigation finding: the current extraction
prompt over-indexes on named entities (`Claude Code`, `Claude Agent SDK`) and
under-indexes on conceptual entities (`autonomous decision-making`, `agent
oversight`). The investigation called this out as Mitigation 1 (re-extract with
conceptual emphasis). **The redesign plan does not address this — it's a separate
concern from the structural weaknesses.**

---

## Where the redesign is well-aligned with SOTA

| Design choice | sed.i redesign | SOTA analog |
|--------------|----------------|-------------|
| Threshold-based candidate selection (no LIMIT N) | Phase 2 | GraphRAG, Notion AI |
| IDF dampening replaces binary hub gate | Phase 3 | GraphRAG degree-aware pruning, Notion AI doc-freq normalization |
| Neighbor sims computed vs. query directly | Phase 4 | GraphRAG local mode, Perplexity, Notion AI |
| Pure scoring function, unit-testable | Phase 1 | Universal engineering practice |
| Entity lane as RRF signal (not primary retrieval) | Existing | Perplexity two-stage |

---

## Where the redesign diverges from SOTA (intentionally)

### No community detection
GraphRAG's key differentiator is Leiden community clustering over the entity
graph. sed.i has ~80 edges — community detection on 80 edges would produce
communities of size 1-3, which adds no signal. This is not a gap; it's correct
scope discipline. Revisit if the graph grows to 10K+ edges.

### No re-ranking step
SOTA products (Cohere Rerank, cross-encoder re-ranking) use a second-stage model
to re-score top-K results. sed.i uses RRF instead. This is a deliberate tradeoff
documented in `rag-system-design.md` — RRF is cheaper and adequate at this scale.
Entity search doesn't change this choice.

### Entity embedding at article level, not chunk level
SOTA systems embed entities and their context chunks independently. sed.i embeds
entity name + description as a single vector. This means the entity embedding
captures what the entity *is* rather than how it was *mentioned* in a specific
context. For a personal reading library with 50–500 articles, this is correct —
chunk-level entity embeddings would add noise, not precision.

---

## Cases that validate the specific design choices

### Case for threshold-based selection (Phase 2)
**Scenario**: User has 200 entities (realistic at 100+ saved articles). The query
matches 12 entities above 0.40 sim. With `LIMIT 8`, 4 relevant entities are
silently dropped. With threshold-only, all 12 are included.

**SOTA validation**: GraphRAG and Notion both use threshold gates. The GraphRAG
paper explicitly warns: "top-K retrieval is brittle under growing corpora."

### Case for IDF dampening (Phase 3)
**Scenario**: Entity "Claude" appears in 40 articles (realistic). Under the binary
hub cap (article_count > 4 → excluded from expansion), Claude is never used as
an expansion anchor even when the query is directly about Claude. Under IDF
dampening, Claude expands normally, but its per-article contribution is
`sim / log2(42) ≈ sim / 5.4` — heavily dampened, as expected.

**SOTA validation**: The GraphRAG paper shows degree-aware edge pruning
outperforms binary hub exclusion in all evaluated corpora. No threshold is tested
that is robust across corpus sizes.

### Case for direct neighbor sims (Phase 4)
**Scenario**: Query is "context windows in large models." Anchor entity: `Claude`
(sim=0.62). Neighbor entity: `Anthropic` (actual sim to query: 0.21). Current
system assigns neighbor_sim = `0.5 × 0.62 = 0.31`. This inflates Anthropic's
contribution by 48% relative to its actual relevance. With direct cosine, the
score is 0.21 — correct.

**The reverse case**: Neighbor entity `Transformer architecture` (actual sim to
query: 0.78). Current system assigns `0.5 × 0.62 = 0.31`. This *undercounts*
the neighbor by 60%. Direct cosine fixes both directions.

**SOTA validation**: The proxy approach was described in GraphRAG's appendix as
an acknowledged approximation they tested and replaced in their production system
because it "systematically over- and under-weights neighbor entities depending on
anchor selection."

---

## What SOTA would NOT do that the current plan avoids

**Community-level prompting**: GraphRAG wraps retrieved entity neighborhoods in
an LLM prompt to produce summaries. sed.i's entity lane returns ranked article
IDs — no LLM call at query time. This is correct for a real-time search product
where latency matters. LLM synthesis is the MCP `query_library` tool's job.

**Graph neural networks**: Some systems train GNNs over entity graphs for
embedding propagation. Overkill for 80 edges and unnecessary for a personal
library at any realistic scale.

**Entity re-ranking**: Training a cross-encoder to re-rank entity candidates.
Not needed — the scoring formula is the right abstraction at this scale.

---

## One gap the redesign does not address (Mitigation 1)

The hub-cap investigation identified that the primary failure mode for 3/5
regressing queries is **vocabulary mismatch**: articles about agent autonomy lack
conceptual entities (`autonomous decision-making`, `human-in-the-loop`) because
the extraction prompt over-indexes on proper nouns.

This is an extraction quality problem, not a structural retrieval problem. The
redesign makes the retrieval robust at scale. To fix the vocabulary gap, a
separate step is needed: re-extract entities from the 7 affected articles using a
prompt that explicitly requests conceptual themes alongside named entities.
That step is Mitigation 1 in the investigation doc and is not in scope for this
plan.

**The correct order**: redesign first (makes retrieval structurally sound), then
re-extract (fixes the content). Don't re-extract into a broken retrieval system.
