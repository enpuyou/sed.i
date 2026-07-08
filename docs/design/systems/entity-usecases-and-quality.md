---
type: design
status: active
date: 2026-07-02
---

# Entity & Relation Work: Use Cases First

Backward from what we want to do → to what the entity graph must look like to do it.

---

## The five features and what they actually need

### Feature A — `explore_concept` MCP tool
**What the user does**: Asks Claude "what does my library know about attention mechanisms?"

**What it does today**: Nothing. `search_content("attention mechanisms")` returns articles that use those words. You get a list of titles, no synthesis.

**What we want**: Claude calls `explore_concept("attention mechanism")` and gets back:
- Which articles discuss it (with a sentence of context from each)
- What it connects to: "5 articles also discuss transformers, 3 discuss BERT"
- Any people/orgs associated: "Vaswani et al. introduced it, LangChain uses it"

**What the entity graph must provide**:
- Entity node for "attention mechanism" with stable name (not "attention", "self-attention", "multi-head attention" as separate nodes — or at minimum those must be deduped/linked)
- `entity_mentions` rows linking it to articles, with `context_text` (the sentence from the article where it appears)
- `entity_relations` rows showing what it connects to, with correct types

**Where we are now**: We have entity nodes but they're fragmented (AI / A.I. / Artificial Intelligence as 3 nodes). The `context_text` field exists but isn't always populated. Relations exist but many are noise (co-mention relations, not real connections).

---

### Feature B — PPR Connections Panel
**What the user does**: Highlights a passage in an article about transformer architecture. Clicks "Connections." Expects to see other articles about related ideas — not just articles that also say "transformer" but articles about attention, BERT, GPT, even articles about cognitive science if they discuss related computational concepts.

**What it does today**: Point-to-point cosine similarity between highlight embeddings. Works for vocabulary overlap. Misses conceptual adjacency: an article on "gradient descent" and one on "backpropagation" may not share many words but are closely connected in the graph.

**What we want**: PPR seeds from the entities mentioned in the highlighted article, propagates to entities in other articles via the graph edges, surfaces articles connected through shared conceptual neighborhood.

**What the entity graph must provide**:
- Clean entity nodes with stable IDs that persist across articles (so "gradient descent" in Article A and "gradient descent" in Article B map to the SAME entity node — this is the cross-article bridge)
- Edges between those nodes (relations) so PPR has paths to traverse
- Enough articles with entities to make the graph connected (currently zero cross-article bridges — every entity is an island)

**Where we are now**: Zero cross-article entity bridges at 62 articles. The PPR graph would have 1,100+ disconnected nodes. PPR seeded from a handful of nodes would immediately drain — it can't propagate anywhere.

**The entity dedup prerequisite**: "AI" / "A.I." / "Artificial Intelligence" being three nodes means that two articles both about AI don't share any entity nodes — they look disconnected. Dedup + prompt canonicalization is the prerequisite for cross-article bridges to form.

---

### Feature C — Entity-Augmented Hybrid Search
**What the user does**: Searches "LLM alignment research."

**What it does today**: Vector + keyword search over article embeddings and chunks. Finds articles that use those words. The Anthropic Institute article surfaces only if its text mentions "alignment" prominently.

**What we want**: Search also finds entity nodes matching "LLM alignment" → "Anthropic", "scalable oversight", "RLHF" → finds all articles mentioning those entities, even if those articles don't say "alignment."

**What the entity graph must provide**:
- Embedded entity nodes (entity embeddings exist — this part works)
- Entity-to-article links via `entity_mentions`
- Clean enough entity names that semantic search on entity embeddings returns the right nodes

**Where we are now**: This is the closest to working. The entity lane in hybrid search exists. The 0.55 similarity gate is working. The main bottleneck is entity quality (fragmented names mean the entity vectors are scattered) and graph sparsity (entity lane returns 0 articles if the entity didn't happen to be mentioned in an article).

---

### Feature D — `synthesize_topic` entity context
**What the user does**: Asks "synthesize what my library says about AI and labor markets."

**What we want**: Before synthesizing, the system pulls the entity neighborhood for "AI", "labor markets", "automation" — sees that these connect to "China", "Meituan", "Hu Anyan", "forward deployed engineers" — and uses that subgraph as additional context to seed the synthesis. The synthesis can then say "your library connects this to gig economy labor in China specifically, via [article X]."

**What the entity graph must provide**:
- Entity neighborhood traversal: given a concept, find 2-hop neighbors
- Enough cross-article coverage that a concept like "AI labor impact" bridges more than one article

**Where we are now**: The traversal logic doesn't exist yet. This is Sprint 5 work. Prerequisite: entity quality (Sprints 1-2).

---

### Feature F — Knowledge Gap Detection
**What it does**: Nightly task. Looks at the entity graph for your reading cluster on "distributed systems" and finds: you have articles about Raft and Paxos but nothing on Byzantine fault tolerance, even though 3 articles mention it as a neighbor concept. Surfaces that as a reading recommendation.

**What the entity graph must provide**:
- Entities with `article_count = 0` but with edges to entities in articles you've read — these are the gaps
- Cross-article entity bridges (otherwise every entity has `article_count = 1` from its source article and there are no zero-count neighbors)
- Stable enough entity names that "Byzantine fault tolerance" mentioned in passing in two articles gets recognized as the same concept

**Where we are now**: Cannot be built yet. Requires cross-article bridges first.

---

## What's actually blocking all five features

All five features share the same prerequisite tree:

```
Entity quality (stable names, no fragmentation)
    ↓
Cross-article entity bridges form as library grows
    ↓
PPR graph becomes connected         Entity search improves
    ↓                                    ↓
Features B, F possible            Features A, C, D possible
```

**The single biggest blocker**: entity name fragmentation. An article about AI that extracts "AI", "A.I.", "Artificial Intelligence", and "AGI" as four nodes will never form a bridge to another article about AI that extracts "AI systems" and "machine learning" as two nodes. They share zero entity nodes. The graph remains disconnected regardless of how many articles you add.

---

## What the prompt test showed

Ran the updated prompt against 6 articles. Old extraction vs new:

| Article | Old relations | New relations | Change |
|---------|-------------|-------------|--------|
| Notes on AI, Labor, China | 37 | 3 | -34 |
| Ted Turner / Braves | 77 | 3 | -74 |
| Automated Alignment Researchers | 111 | 4 | -107 |
| Alzheimer's blood test | 86 | 2 | -84 |
| The Californian Ideology | 92 | 4 | -88 |
| SkillOpt | 49 | 4 | -45 |
| **TOTAL** | **452** | **20** | **-432** |

**95% relation reduction is correct.** The old 452 relations were almost entirely co-mention noise — the LLM extracted a relation for every pair of entities that appeared in the same article, picking whatever type fit least badly. The new 20 are a much higher bar.

**Remaining quality issues** (visible from the test run):

1. **Entity name slip-throughs**: "AI" still appearing as an entity in the labor/China article (the article is specifically about AI's impact — marginal call, but AI alone is still too generic for a named entity node)

2. **Relation target not in entity list**: 7 flags across 6 articles. Example: `ENABLES McLuhan --> "technological determinism"` where "technological determinism" is a concept tag but not an extracted entity. The LLM is generating relations to concepts that didn't make it into the entity list. Fix: the prompt already says "entity names in relations must match the entities list exactly" — but the LLM is creating new names not in the list. Stricter enforcement needed, or post-processing that drops relations with unmatched names (already handled in `article_analysis.py` via the entity lookup).

3. **DEVELOPED misfire on the Alzheimer's article**: `Nature Medicine DEVELOPED Alzheimer's disease` — Nature Medicine published research about Alzheimer's, it didn't develop the disease. The NO examples in the prompt didn't cover "journal DEVELOPED topic." The type for this is actually `INTRODUCES` (the journal article introduced findings about the disease), but even that's a stretch. The real answer: no relation should be extracted here. The LLM is still reaching for a relation when it shouldn't.

4. **DEVELOPED for co-authorship**: `Richard Barbrook DEVELOPED Andy Cameron` — they co-authored an article, not one developed the other. The correct extraction: both are authors of a PAPER entity representing the article itself.

---

## What still needs changing

### Prompt fix 1 — tighter DEVELOPED definition

Current: "person or org created, built, or authored the target"

Problem: "authored" is ambiguous — it covers both "wrote a paper" (legitimate) and "wrote about a topic in a news article" (not DEVELOPED).

Better:
```
DEVELOPED — person or org built, invented, or designed the target as a product/system/model
            YES: "Ted Turner DEVELOPED CNN"  (he built it as a media org)
            YES: "Anthropic DEVELOPED Claude"  (they built the model)
            NO:  "Nature Medicine DEVELOPED Alzheimer's disease"  (published research about it)
            NO:  "Heidi Ledford DEVELOPED the blood test"  (she reported on it)
            NO:  "Richard Barbrook DEVELOPED Andy Cameron"  (they co-authored a paper)
```

### Prompt fix 2 — skip-relation instruction for authorship chains

When two people co-wrote something, extract no PERSON→PERSON relation. Instead, extract both as PERSON entities and let the article itself be the implicit connection.

Add to rules: "Do not extract relations between two PERSON entities unless one explicitly mentored, funded, or built on the intellectual work of the other."

### Entity fix — context_text population

The `entity_mentions` table has a `context_text` column that `explore_concept` will need. Currently it's not being populated in `article_analysis.py`. This is a one-line fix.

---

## The right sequencing from here

1. **Prompt fixes** (this session): tighter DEVELOPED, co-author rule, generic entity skip — done in `_ANALYSIS_PROMPT`
2. **context_text population** (one-line fix): populate it in the `upsert_mention` call
3. **Re-run analysis on existing 62 articles**: backfill with the improved prompt — this replaces the 452 noisy relations with ~20 clean ones per article, and fixes entity fragmentation
4. **Watch for cross-article bridges**: after backfill, query `entities WHERE article_count > 1` — if the number is nonzero, the graph is starting to connect
5. **Build Features A and C**: these only need entity embeddings + mentions, not a connected graph. Buildable now.
6. **Build Features B and F**: need a connected graph. Revisit after ~100 articles with clean extraction.
