---
type: design
status: active
last_updated: 2026-07-08
---

# Entity Graph Search

How entity extraction, storage, and entity-augmented retrieval work in sed.i.
Includes the retrieval eval results showing what the entity graph solves, what it
doesn't, and what's known to be missing.

---

## 1. What is implemented

### 1.1 Article analysis (single-pass extraction)

Every article runs `analyze_article_with_llm()` in `app/tasks/article_analysis.py`.
One gpt-4o call returns tags + entities + relations in a structured response:

- **Domain tags** (1-2): field or area, e.g. `"AI safety research"`
- **Concept tags** (3-4): specific ideas, e.g. `"scalable oversight"`, `"reward hacking"`
- **Entities** (3-8): named people, organizations, tools, concepts, papers that play
  an active role in the article's argument
- **Relations** (0-4): directed edges between entity pairs with a free-text predicate,
  evidence sentence, and strength 1-5

Entity extraction rules enforced in the prompt:

- CONCEPT entities are highest priority: named ideas, frameworks, phenomena — not
  generic terms like "AI" or "technology"
- Concept tags are also promoted to entity nodes (every concept tag becomes a CONCEPT
  entity even if extraction missed it)
- Full canonical names only (`"New York Times"` not `"NYT"`)
- Skip names appearing only in bylines, footnotes, or citations
- Extract `context_text`: one verbatim sentence from the article per entity

Relation rules:
- Predicate: 3-8 words completing `"<source> ___ <target>"`
- Intellectual influence relations flagged as highest value
- Zero relations is correct when no clear connection is stated in the text
- Only strength ≥ 3 unless edge matters for graph traversal

Good predicates: `"founded and transformed"`, `"directly shaped the development of"`,
`"was developed at"`, `"outperforms on benchmarks"`

Bad predicates (prompt rejects): `"said about"`, `"addressed correspondence to"`,
`"is mentioned alongside"` (co-mention, not a relation)

### 1.2 Storage schema

| Table | What it stores |
|---|---|
| `entities` | Canonical node per user: name, type, description, embedding of name string |
| `entity_mentions` | Article → entity link with `context_text` (verbatim sentence) |
| `entity_relations` | Directed edge: source → predicate → target, weight 0.2–1.0, anchored to article |

Entity embeddings are embeddings of the **entity name string** (e.g. `"Donald Trump"`,
`"ChatGPT"`), not of descriptions or context sentences. This is the root cause of the
descriptive-query limitation described in Section 4.

Entities are upserted by `(user_id, lower(name))` — case-insensitive dedup across
articles within a user's library.

Backfill result (62-article corpus): 379 entities, 97 grounded relations. Pre-validator
runs returned 3,386 hallucinated relations; schema-level validation (source/target must
be in the extracted entity list) drops spurious ones.

### 1.3 Entity search lane (`_entity_search`)

Located in `app/core/hybrid_search.py`. Called as one of four lanes in `hybrid_search()`.

```
1. Embed query (or use pre-provided query_embedding if passed directly)
2. Find top-8 entity nodes by cosine similarity to query embedding
3. Gate: if top entity sim < 0.55 → return [] immediately (no entity contribution)
4. Split anchors into two tiers:
   - Strong (sim ≥ 0.60, article_count ≤ 4): eligible for 1-hop graph expansion
   - Weak (sim ≥ 0.55 but below 0.60, or hub entities): direct articles only
5. Score articles: Σ(anchor_sim / log2(2 + entity_article_count))
   IDF-like dampening — hub entities (Anthropic ×10, Claude ×6) contribute less
6. 1-hop expansion: for strong anchors, fetch neighbor entities via entity_relations;
   their articles get neighbor_sim = 0.5 × min_anchor_sim
7. Return results with match_type="entity" and matched_via list
```

Hub entity handling — three mechanisms suppress hub flooding without a binary cap:
- IDF dampening: score divided by `log2(2 + article_count)`
- Hub expansion cap: entities with >4 articles never trigger 1-hop expansion
- Half-weight RRF: entity lane uses k=120 vs k=60 for keyword/semantic lanes

### 1.4 Hybrid search fusion

`hybrid_search(mode="full")` runs four lanes in parallel: keyword, semantic, filter, entity.
Fused with Reciprocal Rank Fusion (k=60 for keyword/semantic/filter, k=120 for entity).
See [search.md](search.md) for the full search architecture.

---

## 2. Query flow examples

### Example A: "ChatGPT and AI tools changing how people work"

```
Query embedding → entity similarity
  ChatGPT (sim=0.668) → PASS 0.55 threshold ✓

ChatGPT: article_count=3, sim=0.668 → strong anchor (≤4 articles)
  → eligible for 1-hop expansion

Direct articles: Management as AI superpower, Our obsession with efficiency, The Year in Slop
1-hop neighbors: OpenAI, Claude → their articles at neighbor_sim = 0.5 × 0.668 = 0.334

Semantic lane: only finds "Management as AI superpower" (R@10=0.67)
Entity lane + hybrid: all 3 articles in top-10 (R@10=1.00) — Δ = +0.33
```

### Example B: "CNN news network business coverage"

```
CNN (sim=0.601) → PASS ✓

CNN mentioned in:
  • "How Ted Turner transformed the Atlanta Braves…" (sports media history)
  • "Trump's shocking battle with Powell…" (political economy)

These two articles share NO vocabulary — semantic places them in different embedding regions.
Entity lane bridges both: R@10=1.00 vs semantic R@10=0.50 — Δ = +0.50
```

### Example C: "Ted Turner television empire" (1-hop expansion)

```
Ted Turner (sim=0.737) → strong anchor → mentioned in 1 article (Ted Turner/Braves)

1-hop neighbors: CNN (via entity_relations)
CNN → mentioned in: Ted Turner/Braves + Trump/Powell

Trump/Powell article gets score from Ted Turner's 1-hop CNN expansion.
Hybrid: both articles found (R@10=1.00) vs semantic R@10=0.50 — Δ = +0.50
```

### Example D: "Donald Trump tariffs economy 2025" (entity fails)

```
Donald Trump (sim=0.331) → FAIL 0.55 threshold ✗
Federal Reserve (sim=0.223) → FAIL ✗
Jerome Powell (sim=0.257) → FAIL ✗

Entity lane returns [] immediately.

Root cause: entity name embeddings are embeddings of short proper noun strings.
Descriptive queries about those entities embed far from the entity name strings.
Semantic lane handles all 5 Trump articles fine (R@10=1.00). Entity adds nothing.
```

---

## 3. Retrieval eval results (32 queries, 62-article corpus, 2026-07-02)

`sem` = semantic lane only. `ent` = entity lane only. `hyb` = full hybrid (all lanes).

| # | Query | sem@10 | ent@10 | hyb@10 | Δ(e-s) | verdict |
|---|---|---|---|---|---|---|
| 1 | What is Claude and how does it compare to other AI assistants? | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 2 | AI alignment and safety research | 0.75 | 0.25 | 0.75 | -0.50 | entity loss |
| 3 | Claude Agent SDK | 0.50 | 1.00 | 1.00 | **+0.50** | **entity WIN** |
| 4 | context engineering for AI agents | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 5 | harness design for long-running agent applications | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 6 | SkillOpt agent skill evolution | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 7 | forward deployed engineer AI economy | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 8 | MLOps tools and practices | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 9 | Apple and Google AI features | 1.00 | 0.50 | 1.00 | -0.50 | entity loss |
| 10 | Anthropic products and research | 1.00 | 0.75 | **0.75** | -0.25 | entity loss |
| 11 | AI tools for managers and productivity | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 12 | efficiency obsession and losing our humanity | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 13 | AI generated content slop internet | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 14 | ChatGPT and AI tools changing how people work | 0.67 | 1.00 | 1.00 | **+0.33** | **entity WIN** |
| 15 | CNN news network business coverage | 0.50 | 1.00 | 1.00 | **+0.50** | **entity WIN** |
| 16 | Ted Turner television empire | 0.50 | 1.00 | 1.00 | **+0.50** | **entity WIN** |
| 17 | Californian Ideology Silicon Valley | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 18 | capitalism co-opting mindfulness and wellness | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 19 | Cory Doctorow LLMs criticism | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 20 | online recommendation algorithms culture | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 21 | Trump Jerome Powell Federal Reserve | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 22 | Trump Iran military options | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 23 | Elizabeth Warren Democrats 2026 midterms | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 24 | US inflation tariffs economic impact | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 25 | Ozempic GLP-1 addiction treatment | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 26 | simple software tools TextEdit | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 27 | why quit Spotify music streaming | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 28 | Alzheimer's blood test early detection | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 29 | Jay-Z Reasonable Doubt album anniversary | 1.00 | 1.00 | 1.00 | +0.00 | tie |
| 30 | natural language autoencoders | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 31 | AI labor market China technology | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |
| 32 | fertility rate decline causes | 1.00 | 0.00 | 1.00 | -1.00 | entity loss |

**Summary:** Entity wins 4/32, ties 12/32, losses 16/32. Hybrid R@10 = 1.00 on 30/32.
Entity losses do not degrade hybrid in 15 of 16 cases (k=120 half-weight absorbs them).

### Two queries below R@10=1.00 in hybrid

**Query 2: "AI alignment and safety research"** (hyb=0.75)

Semantic gets 3/4 expected articles. The 4th (`natural_language_autoencoders`) covers
latent-space interpolation using entirely different vocabulary — no "alignment" or
"safety" terms. Entity search hurts here: Anthropic hub entity floods slots 5-8 and
displaces the 4th article. Fix requires a synthesized CONCEPT node for "interpretability"
bridging the two clusters.

**Query 10: "Anthropic products and research"** (hyb=0.75)

Semantic gets all 4. Entity gets 3/4 but Anthropic (×10 articles, highest frequency)
floods the entity lane with off-cluster articles even after IDF dampening — because the
query word "Anthropic" makes Anthropic entity sim=1.0, which overcomes the dampening.
Entity+hybrid fusion pushes `anthropic_economic_index` below rank 10.

---

## 4. What the entity graph does and does not solve

### What it solves

**Cross-domain bridging for named entities.** When a query names or closely paraphrases
an entity that appears in multiple thematically unrelated articles, entity search finds
all of them. Semantic search cannot — two articles from different domains embed in
different regions and have no semantic neighborhood overlap.

Pattern for all 4 wins: query embedding similarity ≥ 0.55 to entity name + entity
appears in ≥2 articles from semantically distant domains.

### What it does not solve

**Descriptive queries about hub entities** (root cause: name-only embeddings)

Entity embeddings are computed from the name string alone. Queries describing an entity
without naming it directly fail the similarity gate.

| Query | Entity | sim | Gate |
|---|---|---|---|
| "Donald Trump tariffs economy 2025" | Donald Trump | 0.331 | FAIL |
| "Federal Reserve monetary policy independence" | Federal Reserve | 0.536 | FAIL |
| "how people are using AI in daily lives" | ChatGPT | 0.412 | FAIL |

Fix path: embed entity description or `context_text` sentences alongside the name.
"Announced sweeping tariff policy" → Trump entity would then be reachable from
descriptive queries.

**Cross-cluster concept gaps** (root cause: no synthesized CONCEPT nodes)

The corpus has hard vocabulary splits between thematically related clusters. Articles on
"platform harm" use "dopamine loop", "variable reward"; other articles use "AI tools",
"efficiency". No extracted entity bridges these because no single article names the
abstract concept. Requires synthesized CONCEPT nodes across multiple articles.

**Extreme hub flooding** (partially solved)

Anthropic (×10) causes R@10 regression on query 10. IDF dampening + hub expansion cap
handle moderate hubs (Claude ×6, Google ×4) but the top-frequency entity can still
inject noise when its name appears literally in the query (sim=1.0 overcomes dampening).

### Small corpus scale

At 62 articles, 379 entities: only 19 entities appear in ≥2 articles. Entity bridging
requires an entity to appear in multiple articles. Value scales significantly with corpus
size — expected win rate increases substantially at 500+ articles.

---

## 5. Known gaps (not yet implemented)

- **Richer entity embeddings**: embed `description + context_text` instead of name
  only — lets descriptive queries reach entities they describe but don't name
- **Synthesized CONCEPT nodes**: cross-article concept bridging for vocabulary-split
  clusters; would need to be generated by comparing articles, not extracted from any one
- **Tag-to-entity promotion**: concept tags appearing in ≥2 articles promoted to CONCEPT
  entity nodes with entity_mention rows per article carrying the tag — bridges articles
  on the same topic when named entity overlap is low
- **Entity deduplication**: "AI", "A.I.", "Artificial Intelligence" currently create
  separate nodes; a dedup pass on aliases would consolidate them
