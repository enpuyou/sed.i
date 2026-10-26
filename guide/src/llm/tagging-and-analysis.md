---
layout: layouts/chapter.njk
title: Tagging and article analysis
part: 3
order: 2
summary: How one model call turns an article into tags, entities and relations, how the answer is checked, and how tags become reading themes.
verified: 2026-10-25
permalink: /llm/tagging-and-analysis/
---

## In one minute

After an article is embedded, a background task sends its title and the first 2,500 words to a language model (gpt-4o) in one request. The prompt asks for four things: one or two domain tags (the field), three or four concept tags (the ideas), three to eight entities (named people, tools, concepts, papers, organisations), and up to four relations between those entities. The answer must fit a typed schema; if it does not, the library asks the model again with the error. The tags go in the article's `tags` column, each new tag is embedded and stored in `tag_embeddings`, and the entities and relations go into the entity graph tables. Once a week a second job groups each user's tag embeddings into "reading themes" by linking tags whose vectors are close.

This is also the chapter where one retry setting turns out to have been inert twice: once because a function swallowed the error, and once because a library wrapped it.

## The problem

The question this chapter answers: **how does a pile of saved articles get labels that are specific enough to group related reading, and how do you make a language model's free-form answer safe to write to a database?**

The obvious designs fall short.

1. **Let the user tag everything.** Nobody does, and the library that most needs structure is the one with hundreds of unread saves.
2. **Ask the model for "a few tags" in free text.** You get "AI", "Technology", "Interesting". Those match half the library and group nothing.
3. **Embed the article and take the nearest neighbours' tags.** The code once had this as a first pass (`find_similar_tags_by_embedding` still exists in `tasks/tagging.py`), and the file's own comment says why it was dropped: it spread coarse tags from similar articles and blocked fine-grained ones.
4. **Trust the model's JSON.** A model can return a one-word tag, an invented entity type, or a relation between two things it never listed. Writing that to Postgres puts garbage into search and the graph.

So the design has two halves: a prompt that demands specific two-to-four-word tags with examples, and a schema that rejects or repairs what the prompt did not prevent.

## Mental model

Think of a librarian who reads each new book and fills in a card with a shelf (the domain), a few subject headings (the concepts) and a list of people and ideas the book argues about, with arrows between them. A second job, once a week, looks at all the subject headings in the user's library and pins together headings that mean nearly the same thing, so that the shelf "coffee" appears even though no book was labelled exactly that.

| Term | Meaning here |
|---|---|
| Domain tag | The field an article belongs to: "coffee brewing". One or two per article. |
| Concept tag | A precise idea the article discusses: "grind size effects". Three or four. |
| Entity | A named thing in the article: person, concept, organisation, paper or tool. Stored once per user, case-insensitively. |
| Relation | A stated link between two entities, with a short predicate and a strength from 1 to 5. |
| Structured output | A model reply forced into a fixed shape (here a Pydantic class) and validated. |
| Tag embedding | The 1,536-number vector for a tag's text, stored once per distinct label in `tag_embeddings`. |
| Reading theme (cluster) | A group of a user's tags whose vectors are close, with the articles that carry them. |
| Union-find | A small data structure that merges items into groups and answers "are these two in the same group?" quickly. |

{% fig "From article to rows" %}
```text
content_items.full_text (HTML)
  | strip tags, keep first 2,500 words
  v
one gpt-4o call, forced into ArticleAnalysisResponse
  | validators: tags 2+ words, drop bad relations, fix entity types
  v
python checks: tag <= 6 words and <= 100 chars
  |
  +--> content_items.tags            (domain first, then concepts)
  +--> tag_embeddings                (embed only labels not seen before)
  +--> entities + entity_mentions    (extracted entities, then concept
  |                                   tags promoted to CONCEPT entities)
  +--> entity_relations              (only if both ends exist)

weekly, per user:
  tag_embeddings -> cosine matrix -> union-find at 0.60 -> reading_clusters
```
{% endfig %}

## Walkthrough: one article through the analysis

The input is a short article about espresso, written for this guide (about 70 words, so it is far smaller than a real article). The code ran for real against a scratch Postgres database. The language model did not run: its two replies below were written by hand and fed to the real client through a stand-in for the HTTP layer, so everything after "the model replies" is real code on illustrative model output. The embedding calls went to the fake OpenAI server (canned vectors).

### Step 1: the prompt

`analyze_article` converts the stored HTML to plain text, takes the first 2,500 words, and fills a template. The request body the client sent (captured) had `model: gpt-4o`, `max_tokens: 1000`, a forced tool call named `ArticleAnalysisResponse` whose parameters are the JSON schema of the output class, and no `temperature` field, so the API default applies. The prompt, captured from that request (the middle is abbreviated here; the full text is 639 words before the article is inserted, 1,049 tokens measured with the `o200k_base` tokenizer, an approximation for gpt-4o):

```text
Analyze this article for a personal reading library.

TASK 1 — TAGGING
Generate tags at two levels:
  DOMAIN (1-2 tags): The specific field or area this article belongs to.
    Specific enough to cluster related articles; broad enough to group more than one.
    Examples: "distributed systems", "sleep science", "venture capital", ...
  CONCEPTS (3-4 tags): Precise ideas the article actually discusses.
    Two articles sharing a concept tag should genuinely discuss the same idea.
    Examples: "context window limits", "circadian rhythm disruption", ...
  Rules: 2-4 words per tag. No single-word tags. No generic filler ("Technology", "Business").

TASK 2 — ENTITY AND RELATION EXTRACTION
  [... about 55 lines: entity types, what to skip, relation rules, good and bad
       predicate examples ...]

Article title: How espresso extraction works

Article text:
How espresso extraction works Espresso forces hot water through finely ground
coffee at high pressure. Grind size sets how quickly water can pull flavor out
of the coffee. [... rest of the article ...]

Return JSON:
{
  "domain_tags": ["...", "..."],
  "concept_tags": ["...", "...", "..."],
  "entities": [
    {"name": "...", "type": "...", "description": "one sentence on why this entity matters to the argument", "mention_context": "verbatim sentence"}
  ],
  "relations": [
    {"source": "...", "predicate": "3-8 words", "target": "...", "strength": 1-5, "description": "verbatim or near-verbatim sentence from article"}
  ]
}
```

Three prompt techniques are visible. It gives **positive and negative examples** ("Good: availability heuristic", "Bad: generic labels like AI"). It states **numeric limits** ("3-8 entities", "0-4 relations", "Zero is correct when no clear connection is stated"), which gives the model permission to return less. And it gives **grounding rules**: `mention_context` must be a verbatim sentence, which makes a made-up entity easier to spot.

The function that builds and sends it:

<!--src content-queue-backend/app/tasks/article_analysis.py#147-169-->
```python
def analyze_article_with_llm(
    title: str, text: str, user_id: str | None = None
) -> ArticleAnalysisResponse:
    """Single-pass extraction: tags, entities, and grounded relations together."""
    words = text.split()
    excerpt = " ".join(words[:_MAX_TEXT_WORDS])
    with braintrust_span(
        TASK_ARTICLE_ANALYSIS,
        input={"title": title},
        metadata={"user_id": user_id},
    ):
        return llm_client.structured_chat(
            messages=[
                {
                    "role": "user",
                    "content": _ANALYSIS_PROMPT.format(title=title, text=excerpt),
                }
            ],
            response_model=ArticleAnalysisResponse,
            task=TASK_ARTICLE_ANALYSIS,
            max_tokens=1000,
            user_id=user_id,
        )
```

The call goes through the [LLM gateway](/llm/gateway/): budget check, span, model chosen from the task name.

### Step 2: the model replies, and the first reply is rejected

The first illustrative reply used a one-word domain tag:

```json
{"domain_tags": ["espresso"], "concept_tags": ["grind size effects"],
 "entities": [], "relations": []}
```

The validator on the output class rejects any tag with fewer than two words:

<!--src content-queue-backend/app/core/llm_schemas.py#70-84-->
```python
@field_validator("domain_tags", "concept_tags", mode="before")
@classmethod
def validate_tags(cls, tags: list) -> list:
    result = []
    for tag in tags:
        tag = str(tag).strip()
        words = tag.split()
        if len(words) < 2:
            raise ValueError(
                f"Tag '{tag}' is a single word. All tags must be 2-4 words."
            )
        result.append(tag)
    return result[
        :4
    ]  # domain capped at 4 total; concept same — combined enforced in task
```

`instructor` catches the error and sends a second request containing three messages: the original prompt, the model's own reply as a tool call, and a message from the tool reporting the failure (captured, start of that message):

```text
Validation Error found:
<failed_attempts>
<generation number="1">
<exception>
    1 validation error for ArticleAnalysisResponse
domain_tags
  Value error, Tag 'espresso' is a single word. All tags must be 2-4 words.
```

The model sees exactly what it got wrong, in the words the validator wrote. This is why validator error messages are written as instructions. Note that the rejected attempt is paid for: the two calls cost twice one call.

### Step 3: the second reply, and what the validators do to it

The second illustrative reply was valid in shape but had traps:

```json
{"domain_tags": ["coffee brewing", "espresso preparation"],
 "concept_tags": ["grind size effects", "water temperature extraction",
                  "channeling and bitterness",
                  "how fine grind speeds up extraction in the cup"],
 "entities": [
   {"name": "Extraction", "type": "TECHNIQUE", ...},
   {"name": "Channeling", "type": "CONCEPT", ...}],
 "relations": [
   {"source": "Channeling", "target": "Extraction", "predicate": "disrupts even", "strength": 4, ...},
   {"source": "Channeling", "target": "Crema", "predicate": "reduces", "strength": 3, ...}]}
```

Four things happen, each in a different place:

| In the reply | What the code did | Where |
|---|---|---|
| Entity type `TECHNIQUE`, not one of the five allowed | Mapped to `CONCEPT` through an alias table; any unknown type also becomes `CONCEPT` | `_normalize_entity_type` in `core/llm_schemas.py` |
| Relation to `Crema`, which is not in the entity list | Dropped silently by a model validator | `filter_relations_to_entity_list` in `core/llm_schemas.py` |
| Fourth concept tag with 9 words | Dropped silently by the task, because tags longer than 6 words or 100 characters are rejected | `_validate_label` in `tasks/article_analysis.py` |
| Two domain tags | Kept (the task keeps at most 2 domain and 4 concept tags) | `analyze_article` |

Notice the split. The schema validator raises on a problem the model can fix (a single-word tag), which triggers a re-ask. The Python after it quietly drops what is merely too long. The mismatch is deliberate cost control: a re-ask is a second paid call, so cheap-to-discard output is discarded instead. It also means the "2-4 words" instruction is only enforced from below; the actual upper bound is 6.

### Step 4: what was written

After the run, the scratch database held (captured):

```text
content_items.tags  ['coffee brewing', 'espresso preparation',
                     'grind size effects', 'water temperature extraction',
                     'channeling and bitterness']
entities            Channeling (CONCEPT, with description)
                    Extraction (CONCEPT, with description; was TECHNIQUE)
                    channeling and bitterness (CONCEPT, no description)
                    grind size effects (CONCEPT, no description)
                    water temperature extraction (CONCEPT, no description)
entity_mentions     5 rows (2 with the verbatim sentence, 3 with empty context)
entity_relations    1 row: disrupts even, weight 0.8
tag_embeddings      5 rows: 2 'domain', 3 'concept', 1,536 numbers each
```

Two details are worth noticing. The three concept tags were **promoted to entities**: after the extracted entities are written, every concept tag that is not already an entity name becomes a `CONCEPT` entity with no description. The comment in the code gives the reason: every article gets searchable concept nodes even when the extraction used different names for the same idea. And the relation's weight is `strength / 5`: strength 4 became 0.8, so weights lie between 0.2 and 1.0 for the graph walk described in [Entity graph and connections](/retrieval/entity-graph-and-connections/).

The Redis counter for the user held $0.0075 after the run: two gpt-4o calls of 900 prompt and 150 completion tokens each (the token counts in my stand-in), $0.00375 each. The retry cost was recorded.

Finally, the article's `entities_analyzed_at` was set. That timestamp is what the daily backfill uses to find articles never analysed.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/article_analysis.py` | The prompt, the call, the writes, the Celery task and a backfill dispatcher |
| `content-queue-backend/app/core/llm_schemas.py` | The output classes and their validators |
| `content-queue-backend/app/tasks/tagging.py` | The older tag-only path (see below) and `upsert_tag_embeddings` |
| `content-queue-backend/app/models/tag_embedding.py` | The `tag_embeddings` table |
| `content-queue-backend/app/tasks/clustering.py` | Weekly clustering into `reading_clusters` |
| `content-queue-backend/app/models/reading_cluster.py` | The cluster table |
| `content-queue-backend/app/api/themes.py` | `GET /themes` |
| `frontend/app/themes/page.tsx` | The Reading themes page |
| `frontend/components/ReaderArticle.tsx` | Shows tags as Field and Ideas |

### Where it is triggered

`tasks/embedding.py` dispatches `analyze_article_task` right after an article's own embedding is stored. A daily beat task (`backfill_missing_entities_task`) dispatches it for articles with no analysis timestamp. The older tag-only task, `generate_tags_task` in `tasks/tagging.py`, is not dispatched by any code under `app/`; it can only be reached through `backfill_semantic_tags`, which no schedule calls. Its comment in `core/celery_app.py` says the combined task is the live path. `tagging.py` still matters because its `upsert_tag_embeddings` and `_validate_label` are the model for the copies inside `article_analysis.py`.

### The constants

<!--src content-queue-backend/app/tasks/article_analysis.py#48-50-->
```python
_MAX_LABEL_WORDS = 6
_MAX_LABEL_CHARS = 100
_MAX_TEXT_WORDS = 2500
```

The text limit bounds the prompt cost. On the development library (71 active articles, measured) the median article is 1,612 words and 20 of 71 are over 2,500, so about 28% of articles are truncated before analysis; the model never sees their ends.

### Tag embeddings

<!--src content-queue-backend/app/tasks/article_analysis.py#179-195-->
```python
existing = {
    row.label
    for row in db.query(TagEmbedding.label)
    .filter(TagEmbedding.label.in_(labels))
    .all()
}
new_labels = [lbl for lbl in labels if lbl not in existing]

if new_labels:
    with braintrust_span(
        TASK_TAG_EMBEDDING,
        input={"count": len(new_labels), "tag_type": tag_type},
        metadata={"user_id": user_id},
    ):
        result = llm_client.embed(new_labels, user_id=user_id)
    for label, embedding in zip(new_labels, result.embeddings):
        db.merge(TagEmbedding(label=label, embedding=embedding, tag_type=tag_type))
```

Only labels never seen before are embedded, so the table acts as a cache of label vectors. It has no `user_id`: one row per distinct label across all users, with a unique constraint on the text. On the development database it holds 7,183 rows (1,306 `domain`, 5,807 `concept`, 70 with no type from before the split), although the library has only 65 tagged articles at about 5 tags each. The surplus comes from earlier test and evaluation data (inferred; nothing prunes the table).

### The older tagger

`tasks/tagging.py` keeps a smaller version of the same idea: a prompt with domain and concept examples across six fields, the first 800 words of the article, the user's existing tags as context, gpt-4o-mini and a `TagResponse` of just a tag list. The prompt text differs from the analysis prompt but asks for the same two levels. It remains useful as a reading of the prompt idea without the entity part, and `ARCHITECTURE.md` still describes it as the live tagging path (see the discrepancies list).

### Tags on screen

`content_items.tags` is an ordered list: domain tags first, then concept tags. The reader view shows `tags[0]` under the label "Field" and `tags[1:]` joined with a middle dot under "Ideas". List cards show tags together with `auto_tags`, a separate column for suggestions. Because the display assumes exactly one domain tag, a second domain tag appears among the "Ideas".

## Reading themes

The weekly job turns tag vectors into groups the user can browse. For one user it does this (`tasks/clustering.py`):

1. Load the user's non-deleted articles that have tags. Stop if there are fewer than 10.
2. Collect the distinct tags and look up their embeddings.
3. Normalise the vectors and compute the full matrix of cosine similarities between every pair of tags.
4. Link any two tags with similarity of at least 0.60, using union-find, so linked tags end up in one group.
5. Turn each group into a cluster: the union of the articles carrying its tags. Drop clusters with fewer than 3 articles.
6. Label the cluster with the tag that appears on the most of its articles, delete the user's old clusters, and insert the new ones.

<!--src content-queue-backend/app/tasks/clustering.py#99-116-->
```python
# Union-Find: merge tags with similarity >= threshold
parent = list(range(len(clusterable)))

def find(x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x

def union(a: int, b: int) -> None:
    pa, pb = find(a), find(b)
    if pa != pb:
        parent[pa] = pb

for i in range(len(clusterable)):
    for j in range(i + 1, len(clusterable)):
        if sim[i, j] >= _SIMILARITY_THRESHOLD:
            union(i, j)
```

### Running it on made-up tags

To show the shape of the result I put nine made-up tags and twelve made-up articles into the scratch database. The vectors are hand-built: coordinates on two axes per topic, so the similarities are known. Then I called the real `cluster_user_tags` (captured):

```text
coffee tag similarities (cosine):
grind size effects      1.00  0.98  0.57 -0.26
espresso extraction     0.98  1.00  0.71 -0.09
water temperature       0.57  0.71  1.00  0.64
pour over ratios       -0.26 -0.09  0.64  1.00
{'clusters': 3, 'status': 'completed'}
label='circadian rhythm' articles=3 tags=['circadian rhythm', 'sleep hygiene']
label='consensus protocols' articles=3 tags=['consensus protocols', 'raft leader election']
label='grind size effects' articles=5 tags=['grind size effects', 'espresso extraction', 'water temperature', 'pour over ratios']
```

The sourdough tag stood alone on one article and was dropped by the 3-article minimum. The coffee group is the one to study. "Grind size effects" and "pour over ratios" have a similarity of -0.26, which is the vectors pointing in nearly opposite directions, and they are in the same cluster anyway. The chain explains it: grind (to) extraction at 0.98, extraction (to) water temperature at 0.71, water temperature (to) pour over at 0.64. Each link clears 0.60, and union-find merges the whole chain. This behaviour is called **single linkage**: one close pair is enough to join two groups.

On the real development library, measured with a read-only query: 11 clusters; the largest has 35 articles and 44 tags, which is 35 of the 65 tagged articles (54%); the other ten have 3 to 6 articles each. A big merged theme next to many small ones is what chaining looks like in practice.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| One call for tags, entities and relations | Separate tagging and entity calls (the earlier design) | Fewer calls and the model can use one reading of the article for both. The cost is that a failure loses both outputs. |
| gpt-4o for analysis, gpt-4o-mini for the old tagger | gpt-4o-mini for all | The analysis prompt asks for judgement (what is central, what is only mentioned). The module docstring says gpt-4o-mini; the setting `LLM_MODEL_ARTICLE_ANALYSIS_OPENAI` says gpt-4o, and the request I captured used gpt-4o. At the price table's rates, an article of 2,500 words costs roughly $0.017 per analysis on gpt-4o against $0.001 on gpt-4o-mini (illustrative: 4,400 prompt tokens, 600 completion tokens). I did not find an evaluation that justifies the premium; the tagging evaluations in `evals/` compare prompts, not these two models. |
| Pydantic validators plus `instructor` re-ask | Parse JSON by hand and retry in a loop; OpenAI's native structured outputs | Gives validation errors back to the model for free. A hand loop would repeat what the library does. Native structured outputs guarantee shape but not the semantic rules (two-word minimum). |
| Drop bad output rather than re-ask | Re-ask on any imperfection | Re-asks cost a call each. Over-long tags and dangling relations are cheap to discard. |
| Promote concept tags to entities | Keep tags and entities separate | The entity graph then covers every article with at least its concept tags, even if the extraction listed none. |
| Cluster tags, not articles | Cluster article embeddings (k-means) | Tags are short, human-readable and already embedded; the cluster gets a ready-made label. The cost is the dependence on tag vocabulary. |
| Union-find at a fixed 0.60 | k-means; hierarchical clustering with a cut height | No number of clusters to choose, no extra dependency, one parameter. Single linkage chains, as shown. |
| Global `tag_embeddings` table | Per-user | One embedding per distinct label saves calls. Labels are not secret by themselves, but the table is shared. |

## How it fails

| Failure | What happens | What was done |
|---|---|---|
| Model returns a one-word tag | Validation error, re-ask with the error text | `instructor` retries up to 2 attempts in total |
| Model invents an entity type or a relation to a missing entity | Type mapped to `CONCEPT`; relation dropped | Validators in `core/llm_schemas.py` |
| Budget reached | Task returns `budget_exceeded`; nothing written | `BudgetExceededError` caught in `analyze_article` |
| Provider rate limit or timeout | Intended: Celery retries up to 3 times (60, 120, 240 s). Actual for OpenAI: see below | Fixed in 2026-07; the fix does not reach this path |
| Every tag fails the length check | The article stays untagged but is marked analysed, so no retry | Inferred from the code |
| 6.5 million queued messages | `analyze_article` once dispatched an entity-embedding task per article; a bulk run flooded the queue | The dispatch was removed; an hourly sweep covers it (changelog, finding 1) |

### The dead `max_retries`, twice

This is the main lesson of the chapter. Celery retries a task only if the task function **raises**. The changelog entry for 2026-07-28 (`docs/changelog/2026-07-28-failure-injection-fixes.md`, findings 2 and 3) records that `analyze_article()` caught every exception, including OpenAI rate limits and timeouts, and returned `{"status": "failed"}`. Celery saw a normal return, so `max_retries=3` on the task never fired. The same pattern in `generate_tags_with_llm` was worse: it returned an empty list, which its caller treated as "the model found no tags", a successful and permanent outcome. An outage looked identical to an article with nothing to tag.

The fix made both functions re-raise the transient errors (`TRANSIENT_CHAT_ERRORS`, the tuple of rate limit, timeout and connection errors from the gateway) and let the Celery wrapper catch exactly those and call `self.retry` with exponential backoff:

<!--src content-queue-backend/app/tasks/article_analysis.py#383-403-->
```python
except BudgetExceededError as e:
    logger.warning(f"Skipping article analysis for {content_item_id}: {e}")
    return {"content_item_id": content_item_id, "status": "budget_exceeded"}

except TRANSIENT_CHAT_ERRORS:
    # Let the caller decide: analyze_article_task retries these via
    # Celery; a direct/test call has no retry machinery, so it still
    # needs a rollback before propagating.
    try:
        db.rollback()
    except Exception:
        pass
    raise

except Exception as e:
    logger.error(f"Failed to analyze article {content_item_id}: {e}")
    try:
        db.rollback()
    except Exception:
        pass
    return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}
```

The general lesson is about **failure semantics of a framework**: a retry setting is a contract with the framework's own mechanism (here "raise to retry"), and any code between the failure and the framework that converts an exception into a return value cancels the contract without any visible error. The setting reads correctly, the tests pass, and nothing happens.

**The fix has the same hole one level down.** The `except TRANSIENT_CHAT_ERRORS` clause only fires if the exception that arrives is one of those types. On the OpenAI path, `structured_chat` goes through `instructor`, which wraps failures. I tested this with the real gateway, with the HTTP layer answering 429 (rate limited):

```text
(1) simulated at structured_chat boundary -> raised RateLimitError -> Celery would retry
(2) real 429 through instructor -> failed (no exception reaches analyze_article_task)
```

Case 1 is how a test would simulate an outage (the gateway method raises `RateLimitError`). Case 2 is what the installed `instructor` version 1.15.1 does: it retried twice, then raised `InstructorRetryException`, which is not in the tuple, so `analyze_article` fell into its generic handler, returned `failed`, and Celery never retried. No test under `content-queue-backend/tests/` outside `test_llm_client.py` mentions `RateLimitError`, so none covers a task-level rate limit. The same applies to `generate_tags_with_llm`: under the same 429 it returned an empty list (the "no tags" outcome the 2026-07-28 change was meant to remove). I did not test the Bedrock branch (inferred: it raises `ValueError` after its own loop, also outside the tuple). The version range in `pyproject.toml` is `instructor >=1.7.0,<2.0.0`, so the behaviour on other versions was not checked.

A repair would be small: in `structured_chat`, unwrap `InstructorRetryException` to its underlying cause and re-raise that when it is a transient error, or give instructor a retry rule that only retries validation errors. A test that fails at the HTTP layer, as case 2 did, would have caught it.

## What is weak or missing

Ranked by how likely each is to matter.

1. **Rate-limit retries do not reach Celery on the live path** (shown above). During an OpenAI outage, articles are marked `failed` on the first pass instead of retrying. A failed article keeps `entities_analyzed_at` empty, and the daily sweep `backfill_missing_entities_task` selects completed articles where it is empty (oldest first, in limited batches), so the article is dispatched again within about a day. The outage costs a delay, not permanent loss.
2. **Re-analysis overwrites tags.** The task assigns `item.tags` directly. The column's model comment calls it "User-confirmed tags", so any path that runs the task without `skip_tags` on an article whose tags the user edited replaces the edit (inferred from the code; the backfill sets `skip_tags` for already-tagged articles, but the dispatch after an article is re-embedded does not).
3. **No measurement of tag quality.** There is no evaluation of whether tags are specific, whether two articles that share a concept tag really discuss the same thing, or whether gpt-4o beats the cheaper model. The memory chapter shows what an evaluation looks like here; tagging has none.
4. **Single linkage chains.** One 54% theme on the development library hints that the 0.60 threshold merges too much. A stricter link rule (average linkage, or a higher threshold, or a cap on cluster size) would split it, and an evaluation would tell which.
5. **A theme is a set of article ids with no deletion handling.** `GET /themes` counts `article_ids` without checking that the articles still exist. Deleted articles remain in the count until the next weekly run (inferred from `api/themes.py`).
6. **The text limit hides the end of long articles** (28% of the development library), and the prompt allows the model to treat the first 2,500 words as the whole article.
7. **Sampling is left at the API default.** The request carries no `temperature`, so analysis is not repeatable run to run. The old chat path sets 0.0; the structured path does not.
8. **Documentation is out of step.** `ARCHITECTURE.md` describes tagging as "pgvector similarity first, LLM fallback", and the analysis module docstring says gpt-4o-mini. Both disagree with the code (see the discrepancies list).

{% say %}
When an article finishes embedding, a background task sends its first 2,500 words to gpt-4o in one call and asks for domain tags, concept tags, entities and relations. The reply has to fit a typed schema: a one-word tag is rejected and the model is shown the error and asked again, an invented entity type is mapped to concept, and a relation to an entity that was never listed is dropped. The tags go on the article, get embedded, and the entities and relations feed the graph. Once a week I group each user's tag vectors by linking any two tags that are at least 0.6 similar, which gives the reading themes. Two things to know are weak. Retries for rate limits were dead configuration once because the function swallowed the error, and the fix still does not work for OpenAI because the instructor library wraps the exception. And the clustering chains, so one theme can swallow half the library.
{% endsay %}

## Check yourself

{% qa "The task is declared with max_retries=3 and the OpenAI API returns 429. Why might the task still never retry?" %}
Celery retries only when the task raises. If the code catches the exception and returns a failed status, Celery sees success. In this codebase that happened first because the function swallowed all errors, and after the fix because the instructor library wraps the rate-limit error in a different exception type that the `except TRANSIENT_CHAT_ERRORS` clause does not match.
{% endqa %}

{% qa "Why does the schema raise an error for a single-word tag but the task silently drops a ten-word tag?" %}
A re-ask costs a full extra model call, and a single-word tag is something the model can fix when told. An over-long tag is cheap to discard, so spending a second call on it is not worth it. The side effect is that the stated limit of four words is enforced from below only; the real upper limit is six.
{% endqa %}

{% qa "Two tags have cosine similarity of -0.2 but end up in the same reading theme. How?" %}
Union-find links any pair at or above 0.60 and merges groups transitively. If A is close to B and B is close to C, then A and C share a group regardless of how far apart they are. This is single linkage. The made-up coffee example shows exactly this chain.
{% endqa %}

{% qa "Why are concept tags also written as entities?" %}
So every analysed article has at least its concept tags as nodes in the entity graph, even when the extraction step named different entities or none. Search and the connections feature then have something to match on for every article.
{% endqa %}

{% qa "What would you change first to make tag retries trustworthy, and how would you test it?" %}
Make `structured_chat` re-raise the underlying transient error instead of the wrapped one (or restrict instructor's retries to validation errors). Test it at the HTTP layer: answer 429 and assert that the task calls `self.retry`. A test that raises the error at the gateway method skips the part that was broken.
{% endqa %}
