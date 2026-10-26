---
layout: layouts/chapter.njk
title: Entity graph and connections
part: 2
order: 4
summary: Two ways sed.i links articles that plain passage search misses, a graph of named ideas (built, measured, switched off) and highlight-to-highlight connections (live).
verified: 2026-10-25
permalink: /retrieval/entity-graph-and-connections/
---

## In one minute

This chapter covers two features that link articles by something other than their wording. The first is the entity graph. After an article is embedded, one language-model call returns its tags, the 3 to 8 named ideas it is built around (entities such as a concept, tool, person or paper), and up to 4 relations between those entities. Entities are stored per user, each with an embedding, and a search lane can score articles by how close their entities are to the query. That lane is measured and then switched off by default, because on the project's own evaluation it was slightly worse than passage search alone.

The second feature is highlight connections. When you open a highlight in the reader, the backend compares its embedding with every highlight you made in other articles, groups the matches by article, and returns the best two passages per article. A second request asks a small model for a one-sentence explanation of the link and caches it in Redis for seven days. This feature is live.

## The problem

**How can two articles be connected when they share no words, and how do you know whether the connection is worth showing?**

Passage search ([Chunking and embedding](/retrieval/chunking-and-embedding/), [Hybrid search](/retrieval/hybrid-search/)) finds text that resembles the query. It fails when the link between two articles is a shared subject that each article describes in its own vocabulary. An article on management and one on internet "slop" may both be about the same chatbot, while sharing almost no phrases. A query for that chatbot finds one of them by wording and misses the other.

The obvious simpler design is tags: give every article a few labels and link articles that share a label. sed.i does that too (the same model call writes tags), but tags are written per article, in free text, so two articles about the same idea often get different strings. Measured on the development library, none of the 816 highlight-to-article pairs described below shares a single tag. Tags alone link almost nothing.

An entity graph gives the idea a single node that both articles point to. The cost is an extra language-model call per article and a whole new failure mode: the quality of the links depends on how well the model picked and named the entities.

## Mental model

Think of the index at the back of a textbook. Each entry ("backpropagation, 41, 87, 130") lists the pages that discuss a thing. The entity graph is that index for your library: each entry is a named idea, and its page list is the articles that discuss it. Searching becomes "find the entries that match the query, then return their pages".

Highlight connections work like margin notes that cross-reference: you underlined a sentence, and the app looks for sentences you underlined elsewhere that say something similar.

| Term | Meaning here |
|---|---|
| Entity | A named thing an article is built around: type is one of PERSON, CONCEPT, ORGANIZATION, PAPER, TOOL. One row in `entities`, unique per user by lower-cased name. |
| Mention | A link from an entity to an article that discusses it, with one verbatim sentence as evidence. One row in `entity_mentions`. |
| Relation | A directed edge between two entities in one article, with a short free-text predicate ("inspired the architecture of"). One row in `entity_relations`. |
| Hub | An entity mentioned in many articles. It matches many queries, so it is less informative per match. |
| IDF | Inverse document frequency: the idea that a term found everywhere tells you less than a rare one. |
| 1-hop expansion | From an entity that matches, also consider the entities it has a relation with. |
| GraphRAG | Retrieval-augmented generation that uses a graph of entities and relations, not only passages, to find context. Microsoft's version builds the graph with a language model, groups it into communities and summarizes each. |
| Highlight connection | A highlight in another article whose embedding is close to this highlight's. |

sed.i's version is GraphRAG-lite: the same first step (a language model extracts entities and relations from each document) and a much smaller second step (look up entities by similarity, step one hop along relations, score articles). It has no community detection, no community summaries and no graph algorithm such as PageRank. The relation weights are stored "for PPR edge weighting", a comment in the code that points at a step that was not built.

{% fig "How the pieces connect" %}
```text
article saved
  |  embed article (tasks/embedding.py)
  v
analyze_article_task: ONE LLM call
  |-- tags ------------------> content_items.tags
  |-- entities --> entities  + entity_mentions (article <-> entity)
  '-- relations -> entity_relations (entity -> entity)
  |
  |  hourly: embed entity text "TYPE: name - description"
  |  weekly: merge near-duplicate entities (HNSW neighbours + LLM yes/no)
  v
query --> embed --> entities above 0.40 --> + 1-hop neighbours
      --> score articles (IDF-dampened) --> added to the RRF sum
                                            (only if the flag is on)

highlight --> compare with highlights in other articles (cosine)
          --> group by article --> top 2 passages --> insight (cached)
```
{% endfig %}

## Walkthrough: from a query to an entity-lane score

The example is a small made-up library, so the arithmetic is visible. The query is something like "agent harness design". Five entities exist in the user's graph; the table is invented for this guide (illustrative), but the function that scores it is the real one, run on this input (captured).

| Entity | Similarity to query | Articles that mention it |
|---|---|---|
| agent harness | 0.62 | 2 (A1, A2) |
| Acme Assistant (a tool) | 0.71 | 10 (A1 and B1 to B9) |
| tool use | 0.48 | 3 (A1, A3, A4) |
| prompt caching | 0.31 | 1 (A5) |
| eval loop | 0.36 | 2 (A3, A6) |

### Step 1: gate by similarity

The query is embedded and compared with every embedded entity of this user. Only entities with cosine similarity of 0.40 or more count (`_ENTITY_SIM_THRESHOLD`). An entity whose name equals the query, ignoring case, bypasses the gate with similarity 1.0. Here "prompt caching" (0.31) is dropped. "eval loop" (0.36) is also below the gate.

### Step 2: expand one hop

Entities at 0.45 or more (`_ENTITY_EXPAND_THRESHOLD`) are anchors. The code asks `entity_relations` for every entity connected to an anchor by one relation, in either direction, and measures each neighbour's own similarity to the query. A neighbour is kept if that similarity is above zero, even when it is below the 0.40 gate. In the example "eval loop" is a neighbour of "agent harness", so it re-enters the set with its 0.36.

These are the constants and the scoring weights:

{% fig "core/hybrid_search.py: thresholds and secondary weight" %}
<!--src content-queue-backend/app/core/hybrid_search.py#380-390-->
```python
# Gate: minimum cosine similarity for an entity to contribute to results.
# Entities below this threshold are ignored even if they match the query.
_ENTITY_SIM_THRESHOLD = 0.40

# Gate: minimum cosine similarity for an anchor entity to trigger 1-hop expansion.
# Higher than _ENTITY_SIM_THRESHOLD — only high-confidence anchors expand.
_ENTITY_EXPAND_THRESHOLD = 0.45

# Weight for secondary entity contributions on the same article.
# Score = best_contribution + _SECONDARY_WEIGHT * sum(rest).
_ENTITY_SECONDARY_WEIGHT = 0.3
```
{% endfig %}

### Step 3: damp the hubs

An entity's contribution to an article is its similarity divided by `log2(2 + n)`, where `n` is how many articles mention the entity. This is an IDF-style penalty: the more articles an entity touches, the less one match says.

{% fig "Contribution of each entity that passed (captured)" %}
```text
gate 0.4 expand 0.45
passes gate: agent harness 0.62, Acme Assistant 0.71, tool use 0.48
  agent harness   sim 0.62  articles  2  1/log2(2+2)  = 0.500  -> 0.310
  Acme Assistant  sim 0.71  articles 10  1/log2(2+10) = 0.279  -> 0.198
  tool use        sim 0.48  articles  3  1/log2(2+3)  = 0.431  -> 0.207
```
{% endfig %}

The tool with the highest similarity (0.71) contributes the least, because ten articles mention it. The niche concept (0.62, two articles) contributes the most.

### Step 4: combine per article

For each article, sort its contributions and take the largest in full plus 0.3 times the sum of the rest. The pure function that does this takes rows of (article, entity, entity article count) and a similarity map:

{% fig "core/hybrid_search.py: _score_entity_articles, the scoring core" %}
<!--src content-queue-backend/app/core/hybrid_search.py#419-435-->
```python
article_contributions: dict[str, list[float]] = {}
article_entity_sims: dict[str, dict[str, float]] = {}  # aid → {eid: sim}
for mr in mention_rows:
    aid = str(mr.article_id)
    eid = str(mr.entity_id)
    sim = sim_map.get(eid)
    if sim is None:
        continue
    count = int(mr.entity_article_count)
    article_contributions.setdefault(aid, []).append(sim / _math.log2(2 + count))
    article_entity_sims.setdefault(aid, {})[eid] = sim

scores: dict[str, float] = {}
matched_via: dict[str, list[dict]] = {}
for aid, contribs in article_contributions.items():
    contribs.sort(reverse=True)
    scores[aid] = contribs[0] + secondary_weight * sum(contribs[1:])
```
{% endfig %}

Running it on the table above:

{% fig "Output of _score_entity_articles on the made-up table (captured)" %}
```text
A1  score 0.431  via [('Acme Assistant', 0.71), ('agent harness', 0.62), ('tool use', 0.48)]
A2  score 0.310  via [('agent harness', 0.62)]
A3  score 0.261  via [('tool use', 0.48), ('eval loop', 0.36)]
A4  score 0.207  via [('tool use', 0.48)]
B1  score 0.198  via [('Acme Assistant', 0.71)]
B2  score 0.198  via [('Acme Assistant', 0.71)]
B3  score 0.198  via [('Acme Assistant', 0.71)]
B4  score 0.198  via [('Acme Assistant', 0.71)]
```
{% endfig %}

Reading it:

- **A1** is mentioned by three matching entities. Best contribution 0.310 (agent harness) plus 0.3 times (0.207 + 0.198) is 0.431. Agreement between entities lifts an article, but only at 30% weight.
- **A3** is found through "tool use" and also through the one-hop neighbour "eval loop": 0.207 + 0.3 x 0.180 = 0.261. Without the expansion it would score 0.207, tied with A4.
- **B1 to B9** each get 0.198. The hub drags nine unrelated-looking articles into the list at a flat score, which is the failure the damping limits but does not remove.

The `matched_via` list is what the lane reports about why an article matched.

### Step 5: fuse with the other lanes

The lane does not take part in rank-based fusion. Its raw score is multiplied by 0.025 and added to the reciprocal-rank sum of the other lanes (`_ENTITY_SCORE_SCALE`). A rank-1 article from one lane gets 1/61 = 0.0164; an entity score of 0.431 adds 0.0108. The entity lane can therefore nudge an article up or promote an article no other lane found, but it cannot outvote two agreeing lanes.

All of this runs only when `ENTITY_SEARCH_ENABLED` is true. It defaults to false (see "What the evaluation found").

## How the graph gets built

### One call, three outputs

`analyze_article_task` runs after the article-level embedding finishes (it is dispatched from `tasks/embedding.py`). It sends the article title and the first 2,500 words, with one prompt, to the model configured as `LLM_MODEL_ARTICLE_ANALYSIS_OPENAI` (`gpt-4o` in `core/config.py`; the module docstring says `gpt-4o-mini`, which is stale). The structured response has domain tags, concept tags, entities and relations.

The prompt tells the model to extract 3 to 8 entities, prefer CONCEPT entities (named ideas) over people and organizations, skip names that appear only as quote sources, copy one verbatim sentence as `mention_context`, and give each entity a one-sentence reason it matters. For relations: only when the article states the connection, 0 to 4 of them, a predicate of 3 to 8 words, a strength from 1 to 5.

### Tables

{% fig "models/entity.py: case-insensitive uniqueness is a database rule" %}
<!--src content-queue-backend/app/models/entity.py#42-52-->
```python
# Functional unique index on (user_id, lower(name)) — enforces case-insensitive
# dedup at the DB layer. upsert_entity() queries by lower(name) and uses a
# SAVEPOINT to handle the rare concurrent-insert race without wiping the outer tx.
__table_args__ = (
    Index(
        "uq_entity_user_lower_name",
        "user_id",
        func.lower(name),
        unique=True,
    ),
)
```
{% endfig %}

`entity_mentions` is unique on (entity, article) and `entity_relations` on (source, target, predicate, article), so running the task twice on one article writes nothing new. Relation `weight` is the strength divided by 5, so it lies between 0.2 and 1.0.

`upsert_entity` looks up `lower(name)` for the user first. If the row exists it is returned and the new description and type are discarded (the first form seen wins). If two workers insert the same name at once, the unique index rejects the second; the code inserts inside a savepoint, catches the error, and returns the row the other worker created.

Concept tags are promoted too: every concept tag that no extracted entity already covers becomes a CONCEPT entity with an empty description and an empty mention. That guarantees every analysed article has some concept nodes.

### Relations are checked against the entity list

The model is asked for relations by name, so it can name an entity it did not list. The task drops such relations:

{% fig "tasks/article_analysis.py: a relation needs two known entities" %}
<!--src content-queue-backend/app/tasks/article_analysis.py#330-340-->
```python
for r in result.relations:
    source = entity_map.get(r.source.lower().strip())
    target = entity_map.get(r.target.lower().strip())
    if source is None or target is None:
        logger.debug(
            f"Skipping relation {r.source!r}→{r.target!r}: entity not found"
        )
        continue
    predicate = r.predicate.strip()[:120] if r.predicate else ""
    if not predicate:
        continue
```
{% endfig %}

`docs/design/systems/entity-graph-search.md` records the effect of an equivalent schema-level check: before it, a backfill produced 3,386 relations that pointed at entities not in the extracted list; afterwards 97 grounded relations on a 62-article corpus (reported in that document; not re-measured here).

### Embedding the entities

An hourly sweep (`embed-new-entities-sweep`) embeds entities that have no vector, up to 100 per user per run, from the text `TYPE: name — description`. Embedding the type and description, not just the name, was a fix: a bare name such as "Slop" sits far from a descriptive query.

### Merging duplicates

Exact duplicates are prevented by the unique index. Near duplicates ("Claude", "Claude.ai", "Claude Code") are handled by a weekly task in `tasks/entity_dedup.py`:

1. For each embedded entity, fetch up to 20 neighbours (`_ANN_K`) of the same user with cosine similarity of 0.82 or more.
2. Collapse each pair found twice into one.
3. Ask a small model one yes/no question per pair.
4. For confirmed pairs, `merge_entity` moves the loser's mentions and relations to the winner, dropping edges the winner already has, and deletes the loser. The winner is the entity with the lower UUID string, so the choice is arbitrary but repeatable.

{% fig "tasks/entity_dedup.py: the verification question" %}
<!--src content-queue-backend/app/tasks/entity_dedup.py#50-55-->
```python
_VERIFY_PROMPT = """\
Are the following two entity names referring to exactly the same real-world entity?
Answer YES or NO only. Do not explain.

Entity A: {a}
Entity B: {b}"""
```
{% endfig %}

The question says "exactly the same real-world entity", so "Claude" and "Claude Code" (a product and a tool made on it) will often be answered NO. That is correct in general and unhelpful for the search problem the merge was meant to solve. Whether it does so was not measured (inferred from the prompt wording).

## What the graph looks like

Aggregate counts from the development database, for the user with the largest graph (measured 2026-10-25; counts and rates only):

| Measure | Value |
|---|---|
| Entities | 944 (692 CONCEPT, 117 TOOL, 62 ORGANIZATION, 60 PERSON, 13 PAPER) |
| Entities with an embedding | 801 |
| Entities mentioned in exactly one article | 756 (80%) |
| Entities mentioned in two or more articles | 188 |
| Most articles for one entity | 31 |
| Relations | 349 |
| Entities that touch at least one relation | 287 (30%) |
| Entities whose nearest neighbour has cosine 0.82 or more | 251 of 801 |
| Same, 0.90 or more | 120 of 801 |

Two things follow. A graph in which 80% of nodes appear in one article cannot bridge articles through those nodes, since a bridge needs a node with two or more articles. And 251 entities have a neighbour above the dedup threshold, so the weekly merge has plenty to consider (the development database may simply not have run it recently; not checked).

## What the evaluation found

`evals/retrieval/results/report.md` (run 2026-07-06; 61 articles, 45 hand-written queries) compares four variants and reports recall at 10 results, MRR and NDCG (defined in [Measuring retrieval](/retrieval/measuring-retrieval/)). Variants C and D are the entity lane; D is the form described above.

| Variant | R@10 | MRR | NDCG@10 |
|---|---|---|---|
| A: article-level embeddings + keyword | 0.7459 | 0.8276 | 0.6922 |
| B: A + passage (chunk) embeddings | 0.7652 | 0.8310 | 0.7038 |
| C: B + entity lane, rank-based | 0.7470 | 0.8206 | 0.6886 |
| D: B + entity lane, score added (this chapter) | 0.7581 | 0.7934 | 0.6796 |

D beats A by 1.2 percentage points of recall and loses to B by 0.7 points, with lower MRR (0.7934 vs 0.8310) and NDCG as well. The report's own ship criterion was a gain of at least 2 points; it concluded "investigate further". The project then set `ENTITY_SEARCH_ENABLED = False` in `core/config.py`, and `hybrid_search` skips the lane (and the query embedding that only it needed) unless the flag is on.

The gains and losses, from the same report:

- **Four wins** (recall at 10, B to D) where articles share no wording but share an entity: 0.67 to 1.00 on a query about a chatbot's effect on work, 0.75 to 1.00 on a critique of tech culture, 0.33 to 0.67 on AI-generated content, 0.25 to 0.50 on AI recommendation and culture. In the first, the entity for the chatbot appears in all three expected articles, which share no vocabulary.
- **Five regressions** caused by fragmented entities ("Claude", "Claude.ai", "Claude Code" as separate nodes, each pulling a different article set), entities extracted from only the first part of an article, and vocabulary mismatch between the query and the extracted names.
- **Section 11 of the report** tested whether parameters could fix this: lowering the hub-article cap from 4 to 3 changed recall on none of the five regressions, and raising the gate from 0.40 to 0.50 would not help because the wrong entities already scored above 0.50. Its conclusion: "The lever is extraction quality, not retrieval parameters." (The hub cap it tests no longer exists in the code; only the IDF damping remains.)

Statistics matter here. On 45 queries, the difference between A and B is 8 queries better, 5 worse and 32 unchanged; a bootstrap over those 45 paired differences (computed from the author's local per-query results file, which is git-ignored; 10,000 resamples) gives a 95% interval of -3.1 to +7.1 points for the mean. For D against B the interval is -5.0 to +3.4 points. Neither separates from zero. The evidence supports "the entity lane is not clearly better" and does not support "it is worse".

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/article_analysis.py` | the single analysis call, tag writing, entity and relation upsert |
| `content-queue-backend/app/core/entity_graph.py` | `upsert_entity`, `upsert_mention`, `get_entity_neighbors` (recursive query), `merge_entity` |
| `content-queue-backend/app/models/entity.py` | `Entity`, `EntityMention`, `EntityRelation` |
| `content-queue-backend/app/tasks/entity_embedding.py` | the text that is embedded for each entity |
| `content-queue-backend/app/tasks/entity_dedup.py` | candidate pairs, the yes/no check, the merge |
| `content-queue-backend/app/tasks/entity_backfill.py` | the hourly embed sweep and the daily catch-up for old articles |
| `content-queue-backend/app/core/hybrid_search.py` | `_entity_search`, `_score_entity_articles`, fusion |
| `content-queue-backend/app/api/search.py` | the connections endpoints |
| `frontend/components/ConnectionsPanel.tsx` | the panel |

The scheduled work (hourly embed sweep of up to 100 entities per user, daily backfill that queues up to 50 un-analysed articles, weekly dedup) is in `content-queue-backend/app/core/celery_app.py`. The weekly dedup holds a Redis lock so a slow run is not overlapped by the next.

## Highlight connections

### The problem it solves

Reading an article, you underline a sentence. Somewhere in your library you underlined something related, months ago. Connections surface it.

### Endpoints

All are in `api/search.py` and all use the current user's highlights only.

| Endpoint | Returns |
|---|---|
| `GET /search/connections/{highlight_id}` | per highlight: connected articles, with up to 2 passages each |
| `GET /search/connections/article/{content_id}/highlights` | per article: each of its highlights (first 30 by position, only those with a connection) and its connected articles |
| `GET /search/connections/{highlight_id}/insight/{article_id}` | one sentence explaining the link, or `{insight: null}` |
| `GET /search/connections/article/{content_id}` | an older article-level view, still routed |

### The query

For one highlight, the backend runs a single nearest-neighbour query over the user's other highlights:

{% fig "api/search.py: highlights in other articles, above a similarity threshold" %}
<!--src content-queue-backend/app/api/search.py#201-223-->
```python
connection_query = text(
    """
    SELECT
        h.id,
        h.text,
        h.content_item_id,
        ci.title        AS article_title,
        ci.author       AS article_author,
        ci.original_url AS article_url,
        ci.tags         AS article_tags,
        (1 - (h.embedding <=> CAST(:source_embedding AS vector))) AS similarity
    FROM highlights h
    JOIN content_items ci ON h.content_item_id = ci.id
    WHERE h.user_id = :user_id
        AND h.content_item_id != :source_article_id
        AND h.embedding IS NOT NULL
        AND ci.deleted_at IS NULL
        AND LENGTH(h.text) >= 20
        AND (1 - (h.embedding <=> CAST(:source_embedding AS vector))) >= :threshold
    ORDER BY h.embedding <=> CAST(:source_embedding AS vector)
    LIMIT :limit
    """
)
```
{% endfig %}

The filters: other articles only, not deleted, highlight text at least 20 characters, similarity at or above the threshold, nearest first, at most 100 rows. The threshold is a query parameter whose default is `settings.SIMILARITY_THRESHOLD_CONNECTIONS`, and the frontend does not send one. In `core/config.py` that setting is **0.3**.

`ARCHITECTURE.md` gives 0.7 in section 11c ("0.5 was effectively zero") and 0.75 in section 12. Neither matches the code. The value in a deployed environment can be overridden by an environment variable, which was not checked. This chapter describes the code default.

### Grouping, shared tags and ranking

The rows are grouped by connected article. For each group the two best-scoring passages are kept, and the article's score is the best of them. Shared tags (the intersection of the source article's tags and the connected article's tags) are listed on the result. They do not filter: section 11c of `ARCHITECTURE.md` says only connections with a shared tag are returned, but the code only uses the shared-tag count as the first sort key, ahead of similarity.

{% fig "api/search.py: shared tags sort first, similarity second" %}
<!--src content-queue-backend/app/api/search.py#278-280-->
```python
# Sort: shared-tag connections first, then by best passage similarity
enriched.sort(key=lambda x: (-x[0], -x[1]))
return [conn for _, _, conn in enriched]
```
{% endfig %}

If shared tags were required, the feature would return nothing on the development library, where no pair shares one.

### What the similarity numbers look like

Measured 2026-10-25 on the development database, for the user with the most highlights: 48 highlights (at least 20 characters, embedded, in articles that are not deleted) spread over 18 articles. Aggregates only.

| Quantity | Value |
|---|---|
| Highlight pairs from different articles | 1,066 |
| Cosine similarity of those pairs | median 0.136, 90th percentile 0.244, 99th percentile 0.358, maximum 0.549 |
| Pairs at 0.3 or more / 0.5 or more / 0.7 or more | 33 / 1 / 0 |
| Best match per highlight (other articles) | median 0.311, 90th percentile 0.405 |
| Highlights with at least one connected article at 0.3 | 27 of 48 (21 have none) |
| Same at 0.5 / at 0.7 | 2 / 0 |
| Most connected articles for one highlight at 0.3 | 5 |
| Highlight-to-article pairs that share a tag | 0 of 816 |

At the documented 0.7, this library would show no connection for any highlight. At 0.3 about half the highlights have one. Highlights are short and about varied topics, so their similarities sit below the passage-to-passage figures in [Chunking and embedding](/retrieval/chunking-and-embedding/) (random chunk pairs there: median 0.27). A threshold of 0.3 is near the top 3% of this distribution (33 of 1,066 pairs), which suggests it filters noise but is permissive on a library with more highlights, where random pairs reaching 0.3 will multiply. No evaluation of connection quality exists; the 0.3 was not tuned against labelled examples (not found in the repository).

### The insight sentence

For each connected article the panel requests an insight lazily. The endpoint checks the highlight belongs to the user, looks up `insight:{highlight_id}:{article_id}` in Redis, and on a miss calls the language model (`LLM_MODEL_INSIGHT_OPENAI`, `gpt-4o-mini`) with the highlight text and up to three passages, asking for one sentence naming the specific idea that connects them. The result is stored for 604,800 seconds (7 days):

{% fig "api/search.py: cache write" %}
<!--src content-queue-backend/app/api/search.py#551-555-->
```python
if redis_client is not None:
    try:
        redis_client.setex(cache_key, 604800, insight)  # 7 days
    except Exception:
        pass
```
{% endfig %}

Behaviour to know:

- **No invalidation.** Editing or deleting the highlight, or the connected article gaining highlights, does not clear the key. The old sentence stays for up to 7 days. The key holds no hash of the text, so a changed highlight keeps the old sentence.
- **Failures are silent.** Redis down means every request calls the model; a model failure returns `{insight: null}` and the panel shows nothing. The error is logged, not shown.
- **The passages given to the model may not be the matched ones.** The endpoint loads the first three highlights of the connected article (`limit(3)` with no ordering by match), not the two that matched. For an article with many highlights the sentence may explain a link to a passage that is not the one the user sees (inferred from the code).

### The panel

`ConnectionsPanel.tsx` has two modes, chosen by whether a highlight is selected. **Mode 2** (nothing selected) makes one request to the grouped endpoint and lists each highlight as a card with its connected articles as compact rows (title, author and domain, shared tags, score); clicking a row selects that highlight and scrolls to that article in Mode 1. **Mode 1** (a highlight is selected, for example by clicking it in the text) requests the per-highlight endpoint and shows one card per connected article: title, score, shared tags and the insight line above the matched passages. Clicking a passage opens that article at the highlight. Errors show inline with a retry, and a back button returns to Mode 2.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Extract entities and tags in one call | Separate calls per task | One round trip; tags and entities see the same text. The input is the first 2,500 words only, so ideas developed late in a long article are missed. |
| Entities per user, unique by lower-cased name | Global entities shared across users; fuzzy matching at write time | No cross-user leakage; exact duplicates impossible by construction. Near duplicates need the weekly task. |
| Entities and relations in three Postgres tables | A graph database | The graph is small (hundreds of nodes) and the queries are one hop; a recursive query is enough. The access layer is one module, so it could be swapped. |
| IDF damping of hub entities | A hard cap on hub expansion (earlier design) | Smooth; no cliff at a count. It does not stop a hub that matches the query name exactly (similarity 1.0), a case the report documents. |
| Entity score added to the sum, scaled by 0.025 | Rank-based fusion (variant C) | Keeps the confidence information. Measured: slightly better recall than C, worse MRR. |
| Lane off by default | Delete it; leave it on | The code, tests and data are kept for a re-test once extraction improves. Cost of leaving the flag off: the extraction call is still made for every article, because it also writes the tags. |
| LLM yes/no check before merging | Merge by similarity alone; merge by name rules | Avoids merging related but distinct ideas. Cost: tiny; risk: the question may be too strict for the merges that would help. |
| Connections by highlight embeddings, no entity involvement | Use the entity graph or article embeddings | Highlights are what the user chose; comparing them is direct and needs no extra model call. |
| Insight generated on demand, cached 7 days | Generate on save; no cache | The cost is paid only for connections someone looks at. |

No ADR in `docs/decisions/` covers the entity graph or connections.

## How it fails

| Failure | Cause | Effect | What was done |
|---|---|---|---|
| Hallucinated relations | The model names entities it did not list | 3,386 invalid relations in one backfill (reported) | Relations must reference listed entities |
| Backlog of 6.5 million queue messages | Each analysed article dispatched its own entity-embedding job, and a backfill analysed many articles | Found in failure-injection testing, 2026-07-28 (`docs/changelog/2026-07-28-failure-injection-fixes.md`) | Per-article dispatch removed; an hourly sweep embeds new entities |
| Fragmented entities | "Claude", "Claude.ai", "Claude Code" are separate nodes | Evaluation regressions on product queries | Weekly dedup exists; fragmentation was named as the largest cause and has not been re-evaluated |
| Hub flooding | A frequent entity matches the query name exactly | Nine or more unrelated articles at one score (see walkthrough) | IDF damping; not eliminated |
| Concurrent insert of one entity | Two workers analyse articles with the same new entity | A unique violation would abort the transaction | Insert inside a savepoint, catch, reuse the winner's row |
| Stale insight | Highlight edited within 7 days | Old sentence shown | None |
| No embeddings yet | A highlight saved moments ago | The per-highlight endpoint returns 400 "no embedding yet" | The grouped endpoint skips such highlights |

## What is weak or missing

Ranked by how likely each is to matter.

1. **The entity lane's value is unproven, and extraction is its limit.** It is off. The evaluation points at extraction quality, not retrieval parameters. Any re-test needs fixes upstream first: longer input than the first 2,500 words, fewer fragmented nodes, and a measure of how often the right concept is extracted.
2. **Extraction cost without a consumer.** The analysis call runs for every article even though the entity lane is off. Tags need the call, so it is not wasted, but the entity part of the output is unused by search today.
3. **The connection threshold is unvalidated and the docs disagree.** 0.3 in code, 0.7 and 0.75 in `ARCHITECTURE.md`. With no labelled examples there is no way to say which is right. The fix is a small set of highlight pairs judged by hand.
4. **Soft-deleted articles in the entity lane.** The lane's mention query and hydration step do not filter deleted articles (inferred from the code), and mentions are removed only when an article is hard-deleted after 7 days. In the development database 131 of the 192 articles with mentions are soft-deleted. The lane is off, so this is dormant. The fix is a deleted check in the mention query.
5. **Stale and mismatched insights.** No invalidation, and the passages sent to the model are the first three highlights, not the matched ones.
6. **Smaller code and schema issues.** The dedup query probably does not use the HNSW index its comment promises (a close variant run through `EXPLAIN` chose the per-user index and a sort; inferred, and harmless at about 1,000 entities per user). The `article_count` column is never written (0 for every entity in the development database; search counts mentions in SQL). The older article-level endpoint uses `1 - distance / 2` as similarity, so a threshold of 0.3 passes every pair there (all 1,066 above) and it returns up to 20 rows per highlight.

{% say %}
After an article is embedded, one model call extracts its tags, its three to eight key ideas as entities, and a few relations between them. Entities are stored per user, deduplicated by lower-cased name, embedded, and merged weekly when two look like the same thing. A search lane can match the query to entities and score the articles that mention them, damping popular entities so they do not flood the list. I built that and evaluated it on 45 queries: it won on a few queries where two articles share a named idea but no wording, and lost on others, mostly because extraction was patchy, so it is switched off by default. Separately, highlight connections compare a highlight with the ones I made in other articles, group by article, show the best two passages and a cached one-sentence explanation. The weak spots are that the connection threshold is 0.3 in code while the docs say 0.7, and that nobody has measured connection quality.
{% endsay %}

## Check yourself

{% qa "Why is the entity with the highest similarity to the query not always the one that contributes most?" %}
Each contribution is similarity divided by log2(2 + n), where n is the number of articles mentioning the entity. A tool mentioned in ten articles at similarity 0.71 contributes 0.198, less than a concept in two articles at 0.62 (0.310). The lane assumes a very common entity says little about any one article.
{% endqa %}

{% qa "The entity lane beat the baseline on four queries and still lowered the average. Why not simply keep the wins?" %}
Because the same lane lost on others, and nothing in the retrieval step can tell the two cases apart: both come from entities that match the query. Lowering the hub cap or raising the threshold changed nothing on the regressed queries. The losses came from extraction (fragmented or missing entities), so fixing them means changing what is extracted, not how it is scored.
{% endqa %}

{% qa "Highlight connections default to a similarity of 0.3 in code. What would happen with 0.7, and what does that tell you about documentation?" %}
On the development library no pair of highlights reaches 0.7 (the maximum is 0.549), so the panel would always be empty. Documentation that states a number the code does not use, as `ARCHITECTURE.md` does here, has to be checked against the setting and the data.
{% endqa %}

{% qa "You edit a highlight and reopen its connections the same day. Which parts update and which do not?" %}
The connected articles and passages update, because they are computed on each request (provided the highlight's embedding was recomputed). The insight sentence does not: it is cached under the highlight and article ids for 7 days with no invalidation.
{% endqa %}

{% qa "What would you build first if you wanted the entity lane to earn its place?" %}
Measure extraction, not retrieval: for each evaluation query, check whether the expected articles have an entity that a person would call the link. Then lengthen the extraction input beyond 2,500 words, make the dedup question merge product-family names, and re-run the same 45 queries. The existing report says extraction is the limit, so parameter changes would be wasted effort.
{% endqa %}
