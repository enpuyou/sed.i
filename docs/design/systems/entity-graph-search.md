# Entity Graph Search — Implementation, Workflow, and Eval

Date: 2026-07-02
Status: Implemented, in production

---

## What this document covers

How entity extraction and entity-augmented search work in sed.i, including the full
retrieval eval results showing what the entity graph solves, what it made worse,
and what remains unsolved.

---

## 1. What is implemented

### 1.1 Single-pass article analysis

Every article runs through `analyze_article_with_llm()` in
`app/tasks/article_analysis.py`. One gpt-4o call extracts:

- **Domain tags** (1-2): field or area, e.g. `"AI safety research"`
- **Concept tags** (3-4): specific ideas, e.g. `"scalable oversight"`, `"reward hacking"`
- **Entities** (3-8): named people, organizations, tools, concepts, papers that play
  an active role in the article's argument
- **Relations** (0-4): free-text predicates connecting entity pairs, with verbatim
  evidence and strength 1-5

**Entity rules enforced in the prompt:**
- Skip names appearing only in bylines, footnotes, or citation references
- Skip generic background terms (`"AI"`, `"technology"`) unless the specific subject
- Full canonical names only (`"New York Times"` not `"NYT"`)
- Extract `mention_context`: one verbatim sentence per entity

**Relation rules enforced in the prompt:**
- Predicate: 3-8 words completing `"<source> ___ <target>"`
- Intellectual influence relations are explicitly flagged as highest value
- Zero relations is correct when no clear connection is stated in the text
- Only strength ≥ 3 unless edge matters for graph traversal

**Good predicate examples the prompt provides:**
```
"founded and transformed"            → Ted Turner / Atlanta Braves
"directly shaped the development of" → McLuhan / Californian Ideology
"was developed at"                   → Claude / Anthropic
"outperforms on benchmarks"          → SkillOpt / Trace2Skill
```

**Bad predicate examples the prompt rejects:**
```
"said about"                  → quote attribution, not a relation
"addressed correspondence to" → administrative contact, not a relation
"is mentioned alongside"      → co-mention, not a relation
```

### 1.2 Storage schema

| Table | What it stores |
|---|---|
| `entities` | Canonical entity nodes per user: name, type, description, embedding |
| `entity_mentions` | Article → entity links with mention_context text |
| `entity_relations` | Directed edges: source → predicate → target, weight 0.2–1.0, anchored to article |

Entity embeddings are embeddings of the **entity name string** (e.g. `"Donald Trump"`,
`"ChatGPT"`), not of descriptions or context sentences. This matters — see Section 4.

Backfill result: 62 articles → 379 entities, 97 grounded relations (schema-level
validator drops relations whose source/target names are not in the extracted entity
list; pre-validator runs returned 3,386 hallucinated relations).

### 1.3 Entity-augmented search lane

`_entity_search()` in `app/core/hybrid_search.py`:

```
1. Embed query
2. Find top-8 entity nodes by cosine similarity to query embedding
3. Gate: if top entity sim < 0.55 → return [] (no entity contribution)
4. Split anchors into two tiers:
   - Strong (sim ≥ 0.60, article_count ≤ 4): eligible for 1-hop graph expansion
   - Weak (sim ≥ 0.55 but below 0.60, or hub entities): direct articles only
5. Score articles: Σ(anchor_sim / log2(2 + entity_article_count))
   — IDF-like dampening so hub entities (Anthropic ×10, Claude ×6) contribute less
6. 1-hop expansion: for strong anchors, fetch neighbor entities via entity_relations;
   their articles get neighbor_sim = 0.5 × min_anchor_sim
```

**Hub entity handling:** Without IDF dampening, entities like Anthropic (×10) and
Claude (×6) flooded results for unrelated queries. Three mechanisms suppress this:
- IDF dampening: score divided by `log2(2 + mention_count)`
- Hub expansion cap: entities with >4 articles never get 1-hop expansion
- Half-weight RRF: entity lane uses k=120 vs k=60 for keyword/semantic lanes

### 1.4 Hybrid search fusion

`hybrid_search(mode="full")` runs four lanes in parallel:
1. Keyword (tsvector BM25-like)
2. Semantic (pgvector cosine on article + chunk embeddings)
3. Filter (date operators, tag filters)
4. Entity (described above)

Fused with Reciprocal Rank Fusion. Entity lane uses k=120 (half-weight).

---

## 2. Workflow: how a query flows through the system

### Example A: "ChatGPT and AI tools changing how people work"

```
Query embedding → compare to entity name embeddings
  ChatGPT (sim=0.668) → PASS threshold 0.55 ✓
  All others < 0.55

ChatGPT entity → article_count=3, sim=0.668 → strong anchor
  → eligible for 1-hop expansion

ChatGPT mentioned in:
  • "Management as AI superpower"           (direct)
  • "Our obsession with efficiency..."      (direct)
  • "The Year in Slop"                      (direct)

1-hop neighbors of ChatGPT entity: OpenAI, Claude, etc.
  → their articles added at neighbor_sim = 0.5 × 0.668 = 0.334

RRF fusion: entity lane adds all 3 target articles
  Semantic lane: only finds "Management as AI superpower" (R@10=0.67)
  Final hybrid: all 3 articles in top-10 (R@10=1.00)

Δ = +0.33 — entity-only win
```

### Example B: "CNN news network business coverage"

```
Query embedding → compare to entity name embeddings
  CNN (sim=0.601) → PASS ✓
  Ted Turner (sim=0.737 for "Ted Turner television empire" variant) → PASS ✓

CNN entity → article_count=2, sim=0.601 → strong anchor (≤4 articles)
  → eligible for 1-hop expansion

CNN mentioned in:
  • "How Ted Turner transformed the Atlanta Braves..." (sports media history)
  • "'A bone-headed move.' Trump's shocking battle with Powell..." (political economy)

These two articles share NO vocabulary. Semantic embeds them in
completely different regions of embedding space.

RRF fusion: entity lane surfaces both articles
  Semantic lane: finds Ted Turner article (R@10=0.50), misses Trump/Powell
  Final hybrid: both articles in top-10 (R@10=1.00)

Δ = +0.50 — cross-domain entity bridge
```

### Example C: "Ted Turner television empire" (1-hop expansion)

```
Ted Turner entity (sim=0.737) → PASS, strong anchor
  → mentioned in 1 article: Ted Turner/Braves

1-hop neighbors of Ted Turner: CNN (via entity_relations)
  CNN → mentioned in: Ted Turner/Braves + Trump/Powell

neighbor_sim = 0.5 × 0.737 = 0.369

Trump/Powell article gets score from Ted Turner's 1-hop CNN expansion.
Semantic: only finds Ted Turner article (R@10=0.50)
Hybrid: finds both (R@10=1.00)

Δ = +0.50 — two-hop cross-domain bridge
```

### Example D: "Donald Trump tariffs economy 2025" (entity fails)

```
Query embedding → compare to entity name embeddings
  Donald Trump (sim=0.331) → FAIL threshold 0.55 ✗
  Federal Reserve (sim=0.223) → FAIL ✗
  Jerome Powell (sim=0.257) → FAIL ✗

Entity lane returns [] immediately.

Root cause: entity name embeddings ("Donald Trump", "Federal Reserve")
are short proper noun strings. Descriptive queries about those entities
embed far from the entity name embeddings.

Semantic lane handles all 5 Trump articles fine (R@10=1.00).
Entity adds nothing.
```

---

## 3. Confirmed entity wins

All 4 discovered by systematic cross-article entity enumeration across the 62-article corpus.

| Query | sem@10 | ent@10 | hyb@10 | Δ | Anchor entity | Bridge type |
|---|---|---|---|---|---|---|
| "ChatGPT and AI tools changing how people work" | 0.67 | 1.00 | 1.00 | **+0.33** | ChatGPT (TOOL) | Direct: 3 topically unrelated articles share ChatGPT |
| "CNN news network business coverage" | 0.50 | 1.00 | 1.00 | **+0.50** | CNN (ORG) | Cross-domain: sports media ↔ political economy |
| "Ted Turner television empire" | 0.50 | 1.00 | 1.00 | **+0.50** | Ted Turner → CNN | 1-hop: Ted Turner → CNN relation → Trump/Powell article |
| "Claude Agent SDK" | 0.50 | 1.00 | 1.00 | **+0.50** | Claude Agent SDK (TOOL) | Direct: 2 agentic architecture articles |

**Pattern:** All 4 wins occur when:
1. The query names or closely matches an entity name (query embedding sim ≥ 0.55 to entity name embedding)
2. That entity appears in ≥ 2 articles from semantically distant domains

**What wins does NOT require:** The entity lane does not need to understand the query's
intent. It only needs the query to embed close enough to an entity name.

---

## 4. What the entity graph does NOT solve (and why)

### 4.1 Descriptive queries about hub entities (root cause: entity name embedding)

Queries that describe an entity without naming it directly fail the similarity gate.

| Query | Entity | sim to entity name | Threshold | Result |
|---|---|---|---|---|
| "Donald Trump tariffs economy 2025" | Donald Trump | 0.331 | 0.55 | FAIL |
| "Trump administration political decisions" | Donald Trump | 0.346 | 0.55 | FAIL |
| "Federal Reserve monetary policy independence" | Federal Reserve | 0.536 | 0.55 | FAIL |
| "how people are using AI in daily lives" | ChatGPT | 0.412 | 0.55 | FAIL |

Donald Trump appears in 5 articles but entity search cannot find them unless the query
says "Donald Trump". The entity name embedding is just the embedding of the string
`"Donald Trump"` — it knows nothing about tariffs, economy, or policy.

**Fix path:** Embed entity description + mention_context sentences instead of (or in
addition to) the bare name. This would let "tariffs economy" → "announced sweeping
tariff policy" → Trump entity.

### 4.2 Cross-cluster concept gaps (root cause: no CONCEPT nodes bridging clusters)

The corpus has hard vocabulary splits between topically related clusters:

| Query | Semantic miss | Reason |
|---|---|---|
| "how technology platforms undermine human wellbeing" | "LLMs are slot-machines" | Article uses "dopamine loop", "variable reward" — not "wellbeing" or "platform" |
| "decline of content quality due to AI slop" | "LLMs are slot-machines", "banality of recommendation" | Different critical vocabularies; no shared surface terms |
| "AI recommendation and agent systems affect culture" | All culture-criticism articles | "agent systems" pulls technical cluster; culture cluster uses "curation", "dopamine" |

These require CONCEPT entity nodes like `"platform_harm"`, `"content_degradation"`,
`"algorithmic_recommendation"` that bridge article clusters. These concepts don't
currently exist as extracted entities because no single article names them explicitly —
they would need to be synthesized from multiple articles.

### 4.3 Hub entity noise (partially solved, one known gap)

Before hub dampening, Anthropic (×10) and Claude (×6) flooded results. Three
mechanisms suppressed this. One query (`fde_ai_economy`) still shows 0.80 vs 1.00
because FDE articles extract Palantir/Anduril/customer-software as entities, and those
entities inject noise that displaces the expected FDE articles.

### 4.4 Entity search is slower on entity losses

When entity search returns results for a query that semantics already handles at R=1.00,
the entity lane adds noise via half-weight RRF. 16 of 32 queries show entity loss
(though hybrid score remains unaffected because the entity lane's half-weight k=120
rarely displaces semantic results from top-10).

---

## 5. Full retrieval eval — 32 queries (2026-07-02)

Measured on 62-article corpus (enpu@example.com). `sem` = semantic-only lane,
`ent` = entity-only lane, `hyb` = full hybrid (all lanes fused).

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

**Summary:**
- Entity wins: 4/32 queries (queries 3, 14, 15, 16)
- Entity ties: 12/32
- Entity losses: 16/32 (entity lane returns worse results, but hybrid is unaffected for 15 of them)
- Hybrid R@10 = 1.00: 30/32
- Hybrid R@10 < 1.00: 2 queries (rows 2 and 10)

### Queries still below R@10 = 1.00 in hybrid

**Query 2: "AI alignment and safety research"** — R@10 = 0.75

Expected articles: `automated_alignment`, `anthropic_institute_focus`,
`trustworthy_agents`, `what_is_claude`.

Semantic gets 3/4. Missing: `natural_language_autoencoders` (the 4th expected article).
That article covers latent-space interpolation and autoencoder geometry — not "alignment"
or "safety" terminology. It is relevant as an interpretability technique but uses
entirely different vocabulary. Entity search actually hurts: it returns articles anchored
on Anthropic (hub entity) which displace the 4th article from rank 10.

**Root cause:** vocab_frame_mismatch. The natural language autoencoder paper would need
a CONCEPT:interpretability entity bridging it to the alignment cluster.

**Query 10: "Anthropic products and research"** — R@10 = 0.75

Expected: `what_is_claude`, `anthropic_institute_focus`, `automated_alignment`,
`anthropic_economic_index`.

Semantic gets all 4. Entity gets 3/4 but its Anthropic hub (×10 articles) floods slots
5-8 with off-cluster articles, displacing `anthropic_economic_index` from top-10 in the
entity lane. Since hybrid fuses entity + semantic, the off-cluster articles from entity
push `anthropic_economic_index` below rank 10 in the final fusion.

**Root cause:** hub entity noise still leaking through for the Anthropic entity
specifically (×10 articles — highest count in corpus). The IDF dampening reduces it but
does not eliminate it when the query word "Anthropic" makes Anthropic entity sim=1.0.

---

## 6. What improved vs. what's still missing

### What the entity graph improved

**Cross-domain bridging for named entities.** When a user names or closely paraphrases
an entity that appears in multiple thematically unrelated articles, entity search finds
all of them. Semantic search cannot do this — it embeds articles in domain-specific
regions and two articles from different domains (sports media / political economy) share
no semantic neighborhood.

**Confirmed wins in this corpus:**
- ChatGPT bridges 3 articles on AI productivity, burnout, and internet slop
- CNN bridges Ted Turner sports history ↔ Trump/Powell political economy
- Ted Turner entity → CNN relation → reaches Trump/Powell via 1-hop expansion
- Claude Agent SDK bridges 2 agentic engineering articles

**What prevented more wins:**
- Entity name embeddings don't match descriptive queries (threshold gate fails for
  "Donald Trump tariffs" → "Donald Trump" at sim=0.33)
- 62-article corpus is small; most entities appear in only 1 article and can't bridge

### What the entity graph did NOT improve and why

**Single-article entities (16 entity losses):** Queries 5, 7, 11, 12, 13, 20, 22, 24,
27, 28, 30, 31, 32 all get entity R@10 = 0.00. These are queries where the relevant
entities only appear in 1 article each, so entity search can't bridge anywhere. The
entity lane either returns empty (gate fails) or returns the same single article
semantics already found.

**Cross-cluster concept gaps (unresolved):** The corpus has hard vocabulary splits
between culture-criticism articles (use "dopamine", "slot machine", "curation") and
technical/economic articles (use "AI tools", "labor market", "efficiency"). No currently
extracted entity bridges these clusters because no single article names the abstract
concepts. Requires synthesized CONCEPT nodes that would need to be injected rather than
extracted.

**Hub entity noise persists for extreme hubs:** Anthropic (×10) still causes R@10
regression on query 10. The IDF dampening + hub expansion cap handles moderate hubs
(Claude ×6, Google ×4) but the top entity by frequency still injects noise when the
query directly names it.

### What's genuinely missing (data gap, not algorithm gap)

- **CONCEPT bridging nodes**: concepts like `"platform_harm"`, `"content_degradation"`,
  `"agent_reliability"` that would bridge articles using different vocabulary for the same
  idea. These must be synthesized across multiple articles — not extractable from any
  single one.
- **Richer entity embeddings**: embedding entity descriptions or mention_context sentences
  would allow descriptive queries ("tariffs and economy" → Trump entity) to pass the
  similarity gate. Currently only name embeddings are stored.
- **Larger corpus**: with 62 articles, most entities appear in exactly 1 article. The
  entity graph's bridging value scales with corpus size. Expected win rate would increase
  significantly at 500+ articles.
