---
type: architecture-review
status: active
date: 2026-07-01
---

# Tagging + Entity Architecture Review

Review of current state, the redundancy between tagging and entity extraction,
and decisions needed before building further.

---

## 1. Current state: two LLM calls, same text, separate goals

### Tagging (`app/tasks/tagging.py`)

Prompt: domain (1-2) + concept (3-4) tags from title + description + first 800
words. Returns 4-6 string labels stored in `content_items.tags[]`.

Use cases the tags serve:
- Dashboard filtering (click a tag → filter content list)
- Search assist (tag overlap boosts recommendations)
- `TagEmbedding` similarity clusters tags across articles
- Self-improving tag agent (Feature H) reads `content_items.tags` as behavioral signal

Model: `gpt-4o-mini`, `structured_chat` via `instructor`, `max_tokens=200`.

### Entity extraction (`app/tasks/entity_extraction.py`)

Prompt: named entities (3-8) + relations (2-5) from title + first 3000 chars of
plain text. Returns structured PERSON / CONCEPT / ORGANIZATION / PAPER / TOOL
nodes and typed relations between them, written to `entities`, `entity_mentions`,
`entity_relations`.

Use cases entity nodes serve:
- Multi-hop retrieval (entity-augmented search, Feature C)
- PPR connections panel (Feature B)
- `explore_concept` MCP tool (Feature A)
- `synthesize_topic` entity context (Feature D)
- Knowledge gap detection (Loop 2, Feature F)

Model: same `gpt-4o-mini`, same `structured_chat` call, `max_tokens=800`.

### The redundancy

Both tasks run on the same article after ingestion:

```
current pipeline: extract_full_content → generate_embedding → generate_tags
                                        → generate_chunk_embeddings → extract_entities
```

Two separate `gpt-4o-mini` calls, two separate API round-trips, same input text,
different but overlapping output schema. The concept tags in tagging and the
CONCEPT entities in extraction are encoding the same underlying things.

A `generate_tags` run on an article about transformers outputs tags like:
  `["LLM fine-tuning", "attention mechanisms", "context window limits"]`

An `extract_entities` run on the same article extracts CONCEPT entities:
  `{name: "attention mechanism", type: CONCEPT, description: "..."}`
  `{name: "context window", type: CONCEPT, description: "..."}`

These overlap. They are not deduped. Neither one knows about the other.

---

## 2. Merge decision

**Merge into a single `analyze_article` task with one LLM call.**

### What the merged schema returns

```python
class ArticleAnalysisResponse(BaseModel):
    domain_tags: list[str]      # 1-2 domain labels ("LLM fine-tuning")
    concept_tags: list[str]     # 3-4 concept labels ("attention mechanism")
    entities: list[EntityItem]  # structured graph nodes with type + description
    relations: list[RelationItem]
```

`domain_tags` and `concept_tags` together become `content_items.tags[]` exactly
as today. The CONCEPT entities in `entities` are richer versions of concept tags:
they have a description, a deduplication key (case-insensitive name), and graph
connectivity. Tags remain fast string labels for UI filtering. Entities are the
knowledge graph representation.

Tags do NOT become materialized CONCEPT entities automatically. The distinction
must be maintained:
- Tags: "attention mechanisms" — a string label for UI filtering, 2-4 words,
  user-visible, browseable
- Entities: {name: "attention mechanism", type: CONCEPT, description: "method
  that allows models to weigh token importance during generation"} — a graph
  node, LLM-deduped, used for traversal

These can share a concept name but serve different consumers. Don't collapse them.

### What changes in the pipeline

```
New pipeline: extract_full_content → generate_embedding → analyze_article
                                   → generate_chunk_embeddings
```

`analyze_article` replaces both `generate_tags` and `extract_entities`. Runs once,
returns both outputs in one response. `generate_chunk_embeddings` is independent
and can run in parallel or after.

### What stays the same

- `content_items.tags[]` storage is unchanged
- `TagEmbedding` upsert logic is unchanged (reads from `content_items.tags`)
- Entity model / access layer is unchanged
- All three entity tables are unchanged
- Feature H (self-improving tag agent) reads the same `content_items.tags` field

### Prompt structure (merged)

```
Tag this article for a personal reading library AND extract its knowledge graph.

TAGGING:
  DOMAIN (1-2): field/area this belongs to (specific enough to cluster)
  CONCEPTS (3-4): precise ideas the article actually discusses
  Rules: 2-4 words per tag. No single-word tags. No generic filler.

ENTITY EXTRACTION:
  Entity types: PERSON | CONCEPT | ORGANIZATION | PAPER | TOOL
  Relation types: [see §3]
  Rules: 3-8 entities, prefer specific over generic. 2-5 relations, high-confidence only.
  Entity names must match exactly between entities and relations.

Article title: {title}
Article text: {text}

Return JSON: {
  "domain_tags": [...],
  "concept_tags": [...],
  "entities": [{"name": ..., "type": ..., "description": ...}],
  "relations": [{"source": ..., "target": ..., "relation_type": ..., "description": ...}]
}
```

Token estimate: ~600 input tokens (article excerpt) + ~400 output tokens.
Comparable to the two separate calls combined. Single round-trip latency saving.

### Text input: fix the truncation inconsistency

Tagging uses first 800 words of plain text. Entity extraction uses first 3000
chars of plain text. Neither uses chunks.

Merged task: use first 1500 words of plain text (roughly 2000 tokens). This is
consistent, covers most articles completely, and gives the LLM enough context
for both outputs. Per-chunk entity extraction (to surface entities from article
body) is a separate, later improvement.

---

## 3. Relation types: basis and evaluation

### Current types

```python
Literal["DEVELOPED", "USES", "CITES", "CONTRADICTS", "EXTENDS", "INTRODUCES", "ENABLES"]
```

These are hardcoded as a Pydantic `Literal` enum in `llm_schemas.py`. The
`instructor` library enforces valid enum values — the LLM must pick one of these
7 or `instructor` retries.

### Literature basis

There is no single canonical taxonomy for this use case. The options on the
spectrum:

**Open-ended (Microsoft GraphRAG, arXiv:2404.16130)**: No enum. LLM extracts
relation descriptions as free text. "Geoffrey Hinton and backpropagation" becomes
`(Hinton) --["pioneered the development of"]--> (backpropagation)`. Maximum
expressiveness; zero deduplication; graph is hard to traverse because every
edge label is unique.

**Wikidata taxonomy**: 11,000+ property types (P31 = "instance of", P279 =
"subclass of", P361 = "part of", ...). Machine-readable, authoritative,
impossible to prompt an LLM to use consistently.

**Schema.org**: Type hierarchy for structured data. Not designed for knowledge
graphs extracted from article text.

**Domain-specific ontologies** (UMLS for medical, ACM CCS for CS): Authoritative
within a domain; useless across domains (sed.i's library spans tech, health,
policy, culture).

**Custom restricted vocabulary (current approach)**: Small fixed set tuned to
the use case. Consistent — every edge label is one of 7 values, making graph
traversal, relation counting, and eval metrics possible.

### How the current 7 were chosen

They were defined ad-hoc based on what relation types appear most in a general
reading library. No paper citation backs them. They are reasonable intuitions:

| Type | Captures | Appears in |
|------|----------|------------|
| DEVELOPED | attribution of authorship/creation | tech, academic content |
| INTRODUCES | primary contribution of a paper/article | academic, research blogs |
| USES | dependency / tool usage | tech, methodology articles |
| EXTENDS | incremental contribution — builds on prior work | academic, research |
| CITES | explicit reference to prior work | academic, essays |
| CONTRADICTS | opposing viewpoint or refutation | opinion, research, policy |
| ENABLES | underlying mechanism or prerequisite | all domains |

### Problems with the current 7

**Problem 1 — Missing high-frequency relation for a personal library**

`DISCUSSES` or `COVERS` would be the most common relation in a reading library:
"this article discusses reinforcement learning." None of the 7 covers this.
The LLM is forced to pick `USES` or `ENABLES` for what is really just topical
coverage. This introduces noise.

**Problem 2 — DEVELOPED and INTRODUCES overlap**

Both capture "X created Y" or "X introduced Y." A paper introducing a concept
is also developing it. The LLM will randomly assign one of the two for the same
semantic relationship depending on phrasing.

**Problem 3 — CITES requires explicit reference**

Most articles in a personal library are journalism, blog posts, or books — not
academic papers. They don't cite by title. `CITES` fires rarely and when it does,
it's often wrong (the LLM infers citation from mention, not from explicit reference).

**Problem 4 — No INFLUENCES / INSPIRED_BY**

Ideas in a reading library are often related by intellectual lineage, not explicit
derivation. Kahneman → Thaler → nudge theory is an `INFLUENCES` chain, not any
of the 7 types. The LLM uses `EXTENDS` as a proxy but the semantic is different.

### Revised relation taxonomy

Replace the 7 types with 6 better-defined types:

```python
Literal[
    "DEVELOPED",     # person/org created/authored something
    "INTRODUCES",    # paper/article makes X its primary contribution
    "BUILDS_ON",     # extends, refines, or is inspired by — replaces EXTENDS + adds INFLUENCES
    "USES",          # uses, applies, or depends on
    "CONTRADICTS",   # directly opposes or refutes
    "ENABLES",       # underpins, makes possible, is prerequisite for
]
```

Changes from the original 7:
- **Remove CITES**: Too narrow, doesn't fire correctly on non-academic content.
  Evidence of citation is better captured in entity descriptions.
- **Remove EXTENDS**: Absorbed into `BUILDS_ON` which covers both direct extension
  and influence/inspiration.
- **Keep DEVELOPED**: Most useful for linking people/orgs to concepts they created.
- **Keep INTRODUCES**: Distinct from DEVELOPED because an article can introduce a
  concept it didn't create (e.g., a blog post introducing PageRank to a new audience).
- **Add nothing**: The instinct to add DISCUSSES is wrong — if a concept is
  discussed in an article, that's captured by `entity_mentions`, not a relation.
  Relations should only link two entities, not an entity to the article.

### Prompt guidance for better relation extraction

The prompt currently says: "Extract 2-5 relations. Only include high-confidence
connections stated in the text." This is the right instruction. The change is to
provide examples per type to reduce ambiguity:

```
DEVELOPED: Yann LeCun DEVELOPED convolutional neural networks
INTRODUCES: "Attention Is All You Need" INTRODUCES the transformer architecture
BUILDS_ON: BERT BUILDS_ON the transformer architecture
USES: GPT-3 USES few-shot prompting
CONTRADICTS: the "bitter lesson" argument CONTRADICTS the symbolic AI approach
ENABLES: backpropagation ENABLES neural network training
```

---

## 4. What needs updating in existing plans

### `docs/plans/graphrag-eval-harnesses.md`

Phase 4 (Entity Extraction Eval) references a `DRIVES` relation type in its
gold label template:

```python
{"source": "dopamine", "target": "variable reward schedules", "relation_type": "DRIVES"}
```

`DRIVES` is not in the schema. This is a copy-paste error. Must be updated to
use `ENABLES` (the closest match).

Phase 3 references `synthesize_topic(depth="deep")` as the `Feature D` dependency.
That is correct — no changes needed.

Phase 0 and Phase 1 (hard-case discovery and retrieval eval) have no dependency
on entity schema. No changes needed.

### `docs/design/systems/graphrag-multiagent-research.md`

Feature A (§6): The pseudo-code passes `task=TASK_TAGGING` for entity extraction:
```python
task=TASK_TAGGING,  # reuse existing task constant, same model
```
This is out of date — `TASK_ENTITY_EXTRACTION` exists in `llm_client.py` and is
used in the actual implementation. The comment implies the merge was anticipated
but not finalized.

Feature A (§6): The ingestion pipeline shows `generate_tags` and `extract_entities`
as parallel steps after `generate_chunk_embeddings`:
```
New: generate_embedding → generate_chunk_embeddings → generate_tags
                                                    → extract_entities (parallel)
```
This will change: both are replaced by `analyze_article` in sequence.

The relation types in the Feature A section currently show 5 types:
`DEVELOPED | USES | CITES | CONTRADICTS | EXTENDS` — missing `INTRODUCES` and
`ENABLES` that were added in implementation. The section must be updated to
reflect the revised 6-type taxonomy.

---

## 5. Migration impact

### Entity data already extracted

After Feature A shipped, `extract_entities` has been running. Existing entity
data used relation types from the original 7. The migration to 6 types:

- `EXTENDS` rows → `BUILDS_ON`: migrate in a data migration script
- `CITES` rows → drop (or keep with a `deprecated` marker): These are rare
  and likely inaccurate. Dropping is cleaner.
- All other types: no change

This migration is not urgent. Run it before Feature C (entity-augmented search)
ships, because Feature C queries `entity_relations.relation_type`.

### `llm_schemas.py` change

```python
# Before
Literal["DEVELOPED", "USES", "CITES", "CONTRADICTS", "EXTENDS", "INTRODUCES", "ENABLES"]

# After
Literal["DEVELOPED", "INTRODUCES", "BUILDS_ON", "USES", "CONTRADICTS", "ENABLES"]
```

Requires `TASK_ENTITY_EXTRACTION` prompt update + `_EXTRACTION_PROMPT` update in
`entity_extraction.py`.

---

## 6. Implementation sequence

The merge and relation update are Phase 0 before any of the downstream features:

```
Phase 0a: Merge tagging + entity extraction into analyze_article task
  - New file: app/tasks/article_analysis.py
  - New schema: ArticleAnalysisResponse in llm_schemas.py
  - Update: RelationItem Literal (6 types)
  - Update: ingestion.py pipeline
  - Update: ingestion Celery chain in ingestion_task.py
  - Update: tests/test_entity_extraction.py (mock returns ArticleAnalysisResponse)
  - Update: tests/test_tagging.py (if exists, check for separate call assumptions)
  - Migration: entity_relations EXTENDS → BUILDS_ON, drop CITES

Phase 0b: Fix text input — unified 1500-word excerpt
  (currently tagging uses 800 words, entity uses 3000 chars — merged uses 1500 words)
```

After Phase 0, Feature B/C/D/E build on the clean architecture.

---

## 7. Open questions

**Q1: Tagging prompt quality with merged output**

The current tagging prompt is carefully tuned and produces good tag diversity
across domains (tech, food, finance, etc.). A merged prompt may pressure the
LLM to weight entity extraction (more complex schema) over tag quality. Test
with 10 articles from different domains before shipping.

**Q2: Token budget**

The merged prompt requires the LLM to return domain_tags + concept_tags +
entities + relations in a single JSON response. Current max_tokens for entity
extraction is 800. Current for tags is 200. Merged budget: 600-800 should cover
both given that tags overlap heavily with entity names and need no descriptions.
Validate empirically.

**Q3: `instructor` retry behavior**

`instructor` retries when the LLM returns invalid enum values. With a larger
merged response, the chance of at least one invalid field increases. The 6-type
enum is simpler than the 7-type, which reduces this risk. But test retry rate
before shipping.
