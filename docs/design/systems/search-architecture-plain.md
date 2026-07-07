# Search Architecture — Plain English

Date: 2026-07-03

---

## The three separate data structures

There are three separate things stored per article, and they do not link to each other
except through `content_items.id`. This is the root of the confusion.

```
content_items
  .id
  .title
  .full_text
  .tags          ← array of strings, e.g. ["context engineering", "AI agents"]
  .search_vector ← PostgreSQL tsvector (auto-maintained by DB trigger)
  .embedding     ← 1536-dimensional vector of the whole article

tag_embeddings (global table, no user_id, no article link)
  .label         ← the tag string, e.g. "context engineering"
  .embedding     ← 1536-d vector of that string
  .tag_type      ← "domain" or "concept"

entities (per user)
  .name          ← e.g. "Anthropic", "ChatGPT", "Ted Turner"
  .entity_type   ← PERSON | ORGANIZATION | TOOL | CONCEPT | PAPER
  .embedding     ← 1536-d vector of the entity name string

entity_mentions (links entity → article)
  .entity_id
  .content_item_id
  .context_text  ← verbatim sentence from article where entity appears

entity_relations (links entity → entity, anchored to an article)
  .source_entity_id
  .target_entity_id
  .relation_type ← free-text predicate, e.g. "was developed by"
  .weight        ← 0.2–1.0 (strength of the stated relation)
  .content_item_id ← which article stated this relation
```

Tags and entities are **completely separate**. A tag is a string label on an article.
An entity is a named node in a graph that has edges to articles (mentions) and to other
entities (relations). Right now there is no connection between the two systems.

---

## What happens when you save a new article

1. **Extraction:** Full text is sent to gpt-4o. One call returns:
   - 2 domain tags (e.g. "AI safety research")
   - 3-4 concept tags (e.g. "scalable oversight", "reward hacking")
   - 3-8 entities (e.g. `{name: "Anthropic", type: "ORGANIZATION"}`)
   - 0-4 relations between those entities
     (e.g. `{source: "Claude", predicate: "was developed by", target: "Anthropic"}`)

2. **Tags are written to `content_items.tags`** as a plain string array.
   The DB trigger immediately rebuilds `search_vector` to include the tag words.
   The tag strings are also upserted into `tag_embeddings` (just the label + its vector,
   no link back to which article it came from).

3. **Entities are written to `entities`** (one row per unique entity name per user,
   upserted — if "Anthropic" already exists it's reused).
   An `entity_mentions` row links that entity to this article.
   Any relations are written to `entity_relations`.

4. **The article embedding** is computed separately (whole article text → 1536-d vector),
   stored in `content_items.embedding`.

After ingestion, the state for one article looks like this:

```
Article: "Building agents with the Claude Agent SDK"
  content_items.tags = ["AI agents", "agent architecture", "tool use", "context management"]
  content_items.embedding = [0.02, -0.01, ...] (1536 numbers)
  search_vector = 'agent':1A 'build':2A 'claude':3A 'sdk':4A 'ai':5B 'architect':6B ...

  entities linked via entity_mentions:
    Claude Agent SDK (TOOL)    ← mentioned in this article
    Anthropic (ORGANIZATION)   ← mentioned in this article
    Claude (TOOL)              ← mentioned in this article

  entity_relations:
    Claude Agent SDK --[was developed by]--> Anthropic   (from this article)
```

---

## What happens when you search

Every search runs up to four parallel lookups and fuses the results.

### Lane 1: Keyword (tsvector)

Postgres searches `search_vector` using BM25-like ranking. Tags are included in
`search_vector` at weight B (lower than title weight A). So if you search "context
engineering", it matches articles whose title or tags contain those words.

**Tags are searchable here**, but only as exact word matches. Searching "context
engineering" finds articles whose tags column contains that string. Searching "agent
reliability" does not find articles tagged "context engineering" even if they're about
the same thing.

### Lane 2: Semantic (pgvector)

Your query is embedded into a 1536-d vector. Postgres finds articles whose
`content_items.embedding` is closest in cosine distance. This works on meaning, not
exact words. "agent reliability" and "harness design" will score close if the article
text discusses both concepts.

Tags do not participate here. Neither do entity embeddings. Only the whole-article
embedding matters.

### Lane 3: Filter

Handles explicit operators: `after:2025-01-01`, `tag:context engineering`, `author:X`.
This is the only path that does a direct SQL `WHERE 'tag' = ANY(tags)` lookup.
If a user types exactly `tag:context engineering` they get all articles with that tag.
This is not what most users do — they just type natural language.

### Lane 4: Entity

Your query is embedded. Postgres finds entity nodes whose `entities.embedding` is
closest in cosine distance (threshold: sim ≥ 0.55). For each entity that passes, it
finds all articles linked via `entity_mentions`. Articles mentioned by related entities
(via `entity_relations`) are also included as 1-hop neighbors.

This lane returns articles that weren't found by text match because they share a named
entity with your query — not shared vocabulary.

### Fusion

The four lanes produce four ranked lists. They are merged with Reciprocal Rank Fusion:
each article gets a score of `1/(k + rank)` from each lane it appears in. Entity lane
uses k=120 (half weight) vs k=60 for the others. The final list is sorted by total
score.

---

## What "concepts" currently exist

Right now there are **no CONCEPT entity nodes** in the graph for abstract ideas.
The only CONCEPT-typed entities are ones the LLM happened to label as CONCEPT during
extraction. From the actual data:

```
entity_type = CONCEPT in entities table today:
  Context Engineering    (appears in 2 articles)
  Silicon Valley         (appears in 2 articles)
  Fulton County Stadium  (appears in 1 article — LLM mislabeled a place as concept)
  AI Agents              (appears in 1 article)
  The Good-Enough Life   (appears in 1 article)
```

Most concepts the LLM extracts are single-article entities that can't bridge anything.

---

## What relations currently exist

Relations connect **entity to entity**, not entity to article. An article is not an
endpoint of a relation — it's the source document that *stated* the relation. Examples
from the actual data:

```
Context Engineering  --[shapes behavior and improves performance of]-->  AI Agents
   stated in: "Why Context Engineering? – Nextra"

Claude Agent SDK  --[was developed by]-->  Anthropic
   stated in: "Building agents with the Claude Agent SDK"

Apple  --[partnered to use ChatGPT]-->  OpenAI
   stated in: "Apple teams up with Google Gemini for AI-powered Siri"

Immanuel Kant  --[inspires ideas in]-->  The Good-Enough Life
   stated in: "Why Is It So Hard to Be Ordinary?"
```

Relations are used in `_entity_search` for 1-hop expansion: if your query matches
entity A, and A has a relation to entity B, then articles mentioning B are also
included. This is how "Ted Turner television empire" reached the Trump/Powell article —
Ted Turner → CNN (via relation) → Trump/Powell article (mentions CNN).

---

## The tag gap — what's missing and what your question points at

Right now:

```
User searches: "context engineering"
  keyword lane: finds articles whose tags[] contains "context engineering" ✓
  semantic lane: finds articles whose whole-article text is similar ✓
  entity lane: finds articles mentioning entity named "Context Engineering"
               (only 2 articles — this barely helps)
  filter lane: only if user typed "tag:context engineering" explicitly
```

What's missing: **tags are not linked to the entity graph**. The tag string "context
engineering" exists in `tag_embeddings` with its own embedding vector, but that table
has no link to which articles carry that tag. The entity node "Context Engineering"
exists with 2 mentions, but most concept tags were never promoted to entity nodes.

Your proposal: promote tags directly into the entity graph. For each concept tag that
appears in ≥2 articles, create an entity node of type CONCEPT and create entity_mention
rows linking it to every article that carries the tag.

Result: searching "context engineering" via the entity lane would find all 3 articles
with that tag (Effective CE, Harness design, Why CE) — even if one of them uses
different vocabulary in its body text. The tag becomes the bridge.

The relation question ("what relation connects them?") — none is needed. The link is
just `entity_mentions`: article → CONCEPT node. No edge between the concept and the
article needs a named predicate. The entity_mentions table already handles this — it's
just "this entity appears in this article", no direction required. entity_relations
(the predicate edges) are between entity nodes only, not between articles and concepts.

---

## How the network grows as the library grows

Each new article adds:
- 2-6 new tags to `content_items.tags` → immediately searchable via keyword
- 3-8 entities to `entities` (or re-links to existing ones if the name matches)
- 0-4 entity_relations between those entities

The graph gets denser as more articles share entities. The first time "Anthropic" is
mentioned it creates a node and 1 edge. By the 10th article mentioning Anthropic it
has 10 edges. Any query that matches "Anthropic" now reaches 10 articles via a single
hop.

The critical threshold: an entity needs to appear in ≥2 articles to create a bridge.
With 62 articles and 372 entities, only 19 entities appear in ≥2 articles. At 500
articles, most entities would appear in multiple articles and the graph would be
substantially denser. The tag promotion proposal matters most at small library sizes —
tags are assigned per article by the LLM and will reliably bridge articles on the same
topic even when named entity overlap is low.
