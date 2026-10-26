---
layout: layouts/chapter.njk
title: Chunking and embedding
part: 2
order: 2
summary: How a saved article becomes searchable passages, and how each passage becomes a list of numbers that can be compared.
verified: 2026-10-25
permalink: /retrieval/chunking-and-embedding/
---

## In one minute

sed.i does not search whole articles. After an article is saved, a background task cuts its text into passages of roughly 350 tokens, splitting at headings first and at sentence ends second. It puts the article title and the passage's position in front of each passage, then sends the passages to OpenAI's embedding model in batches. The model returns 1,536 numbers per passage, and those numbers are stored next to the passage text in the `content_chunks` table. At search time the query is turned into numbers the same way, every passage is compared with it, and each article is ranked by its best-matching passage.

Chunking exists for two reasons. One vector for a 5,000-word article is an average of everything in it, so a question about one paragraph matches the average poorly. And the embedding model accepts at most 8,192 tokens per input, so a long article cannot be embedded in one piece at all.

## The problem

**Why is an article split into pieces before it is embedded, and how are the pieces chosen?**

Take the obvious design first: one embedding per article. It is cheap (one API call per article) and simple to query. It has three problems.

1. **Blurring.** An embedding is a single point. An article that covers grind size, water temperature and pressure becomes one point somewhere between the three topics. A query about water temperature is compared with that blend, not with the paragraph that answers it.
2. **A hard input limit.** `text-embedding-3-small` accepts 8,192 tokens per input. sed.i's per-article embedding (`tasks/embedding.py`) truncates to the first 8,000 tokens, so anything after roughly 6,000 words is invisible to search.
3. **Whole articles are the wrong unit of evidence.** Features such as highlight connections and the research agent need the passage that matched, not only the article.

The opposite extreme, one embedding per sentence, fails differently: "It rose 3% that year" means nothing without the sentence before it. Passages of a few hundred tokens keep enough context to be understood alone and are short enough to stay about one thing.

## Mental model

Think of a library. The catalog entry for a book (title and a blurb) tells you what the book is about on average. Index cards, one per page of notes, let you find the page that answers a specific question. Chunking makes the index cards.

An **embedding** is the position of a piece of text on a map of meaning that has 1,536 axes. Texts about similar things land near each other. You cannot read the axes; only distances between points carry information. The distance used here is **cosine similarity**: the cosine of the angle between two vectors. It is 1.0 for the same direction, 0 for unrelated, and negative for opposite. OpenAI's vectors have length 1 (the stored ones measure 1.0000 to 1.0002), so cosine similarity is the same as the dot product.

{% fig "Saving: from article to rows" %}
```text
article HTML
  |  split at headings (h1 to h4)
  v
sections
  |  long ones: split at sentence ends, 40 words of overlap
  v
chunks (about 350 tokens each)
  |  add prefix: From the article "<title>" (section i of n):
  v
texts
  |  embed, up to 100 per call
  v
rows in content_chunks: text + 1,536 numbers
```
{% endfig %}

{% fig "Searching: from question to ranked articles" %}
```text
"why is my shot sour?"
  |  embed (one call; cached in Redis for 1 hour)
  v
query vector
  |  compare with every chunk of this user's articles
  v
one score per chunk
  |  keep the best score per article (MAX)
  v
ranked articles
```
{% endfig %}

| Term | Meaning here |
|---|---|
| Token | A piece of text the model reads; about 1.2 to 1.5 tokens per English word. Measured below: 447 words was 522 tokens. |
| Chunk | One passage of an article, stored as one row of `content_chunks`. |
| Embedding | The 1,536 numbers the model returns for a text. Stored as a pgvector `vector(1536)`: 6,148 bytes per chunk. |
| Cosine similarity | How close two embeddings are, from -1 to 1. |
| Contextual prefix | The sentence `From the article "<title>" (section i of n):` placed before the passage when it is embedded (not when it is stored). |

## Walkthrough: one article, start to finish

The input is a short article about espresso, written for this guide. It has an intro, a long section on grind size (28 sentences), a short section on water temperature, and a one-line section on time.

{% fig "The input (shape only; the long section is abbreviated)" %}
```html
<h1>How espresso extraction works</h1>
<p>Espresso forces hot water through finely ground coffee at high pressure. ...</p>
<h2>Grind size</h2>
<p>Grind size sets how quickly water can pull flavor out of the coffee. A finer grind
exposes more surface area, ... [28 sentences, 447 words] ...</p>
<h2>Water temperature</h2>
<p>Most machines hold the water between 90 and 96 degrees Celsius. ...</p>
<h2>Time</h2>
<p>About 28 seconds.</p>
```
{% endfig %}

### Step 1: split

`split_article_into_chunks` first inserts a marker before every `<h1>` to `<h4>` and splits there, giving four sections. Each section is stripped of tags. A section of 466 words or fewer becomes one chunk. A section with fewer than 20 words is merged into the previous chunk instead of standing alone. A longer section is cut at sentence ends.

The numbers 466, 40 and 20 come from these constants:

{% fig "tasks/chunk_embeddings.py: the sizing constants" %}
<!--src content-queue-backend/app/tasks/chunk_embeddings.py#35-45-->
```python
# Target chunk size in tokens (approximate via word count: 1 token ≈ 0.75 words)
_TARGET_TOKENS = 350
_TARGET_WORDS = int(_TARGET_TOKENS / 0.75)  # ~467 words
_OVERLAP_WORDS = 40
_MIN_CHUNK_WORDS = 20  # discard tiny leftover chunks
# Hard cap: word counts miss text with no spaces/punctuation (CJK, minified code,
# base64), which can otherwise yield one chunk over OpenAI's 8192-token input limit.
_MAX_CHUNK_CHARS = 4000
# Chunks embedded and inserted per API call. Bounds peak memory; well under
# OpenAI's 2048-inputs-per-request limit.
_EMBED_BATCH_SIZE = 100
```
{% endfig %}

Running the function on the article gives four chunks (captured by running the code):

{% fig "Output of split_article_into_chunks on the espresso article" %}
<!--captured python /tmp/guide_ex/chunk_demo.py-->
```text
[0]  33 words   "How espresso extraction works Espresso forces hot water through ..."
                ends "... changes in the process change the taste a lot."
[1] 447 words   "Grind size Grind size sets how quickly water can ..."
                ends "... pattern is more useful than any single good shot."
[2]  98 words   "for every shot. With those four numbers you can ..."
                ends "... usually need a finer setting and a longer shot."
[3]  34 words   "Water temperature Most machines hold the water between 90 ..."
                ends "... water leaves the shot sour. Time About 28 seconds."
overlap between chunks 1 and 2: 40 words shared
```
{% endfig %}

Reading the output:

- **Chunk 0** is the title and the intro. At 33 words it is above the 20-word minimum, so it stays separate.
- **Chunk 1** is most of the grind section. The next sentence would have pushed it past 466 words, so the splitter closed the chunk at 447.
- **Chunk 2** begins at "for every shot." because the splitter copies the last 40 words of the previous chunk into the next one. This overlap means a sentence that straddles the cut appears in both chunks. The overlap is counted in words, so it can begin in the middle of a sentence, as it does here.
- **Chunk 3** is the water temperature section. The one-line "Time" section had 4 words, below the 20-word minimum, so it was merged into this chunk instead of being kept alone.

Headings become plain text at the start of their chunk ("Grind size Grind size sets ..."): the heading and the first sentence run together.

{% aside %}
**Why a minimum of 20 words?** Very short sections (a heading and one line) make poor passages: too little text for the embedding to say anything specific. Merging them into the neighbour keeps the heading's words in some chunk without creating a near-empty vector.
{% endaside %}

The sentence-splitting loop is the part that produces chunks 1 and 2:

{% fig "tasks/chunk_embeddings.py: cutting a long section at sentence ends, with overlap" %}
<!--src content-queue-backend/app/tasks/chunk_embeddings.py#95-110-->
```python
sentences = re.split(r"(?<=[.!?])\s+", section)
current: list[str] = []
current_words = 0
for sentence in sentences:
    s_words = len(sentence.split())
    if current_words + s_words > _TARGET_WORDS and current:
        chunk_text = " ".join(current)
        if len(chunk_text.split()) >= _MIN_CHUNK_WORDS:
            chunks.append(chunk_text)
        # Overlap: carry last _OVERLAP_WORDS into the next chunk
        overlap_words = " ".join(current).split()[-_OVERLAP_WORDS:]
        current = overlap_words + sentence.split()
        current_words = len(current)
    else:
        current.extend(sentence.split())
        current_words += s_words
```
{% endfig %}

### Step 2: add context

Before embedding, each chunk gets a prefix naming its article and position:

{% fig "tasks/chunk_embeddings.py" %}
<!--src content-queue-backend/app/tasks/chunk_embeddings.py#128-145-->
```python
def contextual_prefix(
    chunk_text: str,
    article_title: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Prepend article context to a chunk before embedding (Anthropic contextual retrieval).

    Without context, isolated chunks lose meaning — "revenue grew 3%" is useless
    without knowing which company. The prefix anchors the chunk to its source article.
    The chunk text appears exactly once in the output.
    """
    prefix = (
        f'From the article "{article_title}" '
        f"(section {chunk_index + 1} of {total_chunks}): "
    )
    return prefix + chunk_text
```
{% endfig %}

For chunk 2 the text sent to the model is:

```text
From the article "How espresso extraction works" (section 3 of 4): for every shot. With those
four numbers you can tell whether a change helped or whether the beans simply aged. ...
```

The reason is that a passage lifted out of its article loses its subject. "Keep notes: grind setting, dose, yield and time" is about espresso only because of the article around it. The prefix puts that subject back into the text the model reads. The prefix is not stored in `content_chunks.text`; only the embedding was computed from it.

Measured with the `cl100k_base` tokenizer (the one `text-embedding-3-small` uses):

| Chunk | Words | Tokens (chunk) | Tokens (with prefix) |
|---|---|---|---|
| 0 | 33 | 37 | 54 |
| 1 | 447 | 522 | 539 |
| 2 | 98 | 114 | 131 |
| 3 | 34 | 42 | 59 |

The prefix costs about 17 tokens per chunk. Chunk 1 is 522 tokens, not 350: the code assumes 0.75 words per token, and this text runs 0.86. The target is a word budget, not a token budget.

### Step 3: embed and store

The four prefixed texts go to the embedding model in one call (calls carry up to 100 texts). Each comes back as 1,536 floating-point numbers:

```text
[-0.015670776, 0.03515625, 0.044555664, 0.030838013, 0.0020523071, 0.046844482, ...]
```

(The first six numbers of a stored chunk from the development database. No single number means anything; only comparisons between whole vectors do.)

The task deletes the article's existing chunks and inserts the new rows in the same transaction, so re-running it replaces chunks and never leaves half an article:

{% fig "tasks/chunk_embeddings.py: embed in batches, insert, replace atomically" %}
<!--src content-queue-backend/app/tasks/chunk_embeddings.py#191-220-->
```python
db.query(ContentChunk).filter(
    ContentChunk.content_item_id == item.id
).delete()
for start in range(0, total, _EMBED_BATCH_SIZE):
    batch = chunks[start : start + _EMBED_BATCH_SIZE]
    texts_to_embed = [
        contextual_prefix(chunk, title, start + offset, total)
        for offset, chunk in enumerate(batch)
    ]
    result = llm_client.embed(texts_to_embed, user_id=str(item.user_id))
    if len(result.embeddings) != len(batch):
        raise ValueError(
            f"Embedding count mismatch: got {len(result.embeddings)} "
            f"for {len(batch)} chunks"
        )
    db.bulk_insert_mappings(
        ContentChunk,
        [
            {
                "content_item_id": item.id,
                "user_id": item.user_id,
                "chunk_index": start + offset,
                "text": chunk_text,
                "embedding": embedding,
            }
            for offset, (chunk_text, embedding) in enumerate(
                zip(batch, result.embeddings)
            )
        ],
    )
```
{% endfig %}

{% aside %}
**Why delete first?** Re-running the task must not duplicate chunks. Deleting and inserting inside one transaction means a failure halfway rolls back to the previous chunks, so readers never see a half-indexed article. The memory chapter explains why batches of 100 matter.
{% endaside %}

A stored row holds `id`, `content_item_id`, `user_id`, `chunk_index` (0 to n-1), `text` and `embedding`. Across the development database (one person's library of 71 active items) there are 573 chunks, averaging 10.4 per article (median 9, largest 54). Chunk text averages 1,794 characters.

### Step 4: use the chunks at search time

For the query "why is my shot sour?", the search path embeds the query (one call, cached in Redis for an hour under a hash of the text), then runs this SQL:

{% fig "core/hybrid_search.py: score each article by its best chunk" %}
<!--src content-queue-backend/app/core/hybrid_search.py#330-340-->
```sql
WITH chunk_scores AS (
    SELECT cc.content_item_id AS id,
           MAX(1 - (cc.embedding <=> CAST(:q AS vector))) AS similarity
    FROM content_chunks cc
    JOIN content_items ci ON ci.id = cc.content_item_id
    WHERE cc.user_id = :uid
      AND ci.deleted_at IS NULL
      AND cc.embedding IS NOT NULL
      AND ci.title IS NOT NULL AND ci.title != ''
    GROUP BY cc.content_item_id
),
```
{% endfig %}

`<=>` is pgvector's cosine distance, so `1 - distance` is the cosine similarity. `MAX` per article means an article is as good as its single best passage. This query does not use the HNSW index on `content_chunks.embedding`: it computes the exact distance to every chunk of the user's articles and keeps the best per article (confirmed with `EXPLAIN` on the development database: a scan of 557 rows, then a hash aggregate). That is exact, with no missed results, and costs time proportional to the number of chunks the user has. Chapter 2.3 covers where that stops being fine. For the espresso article, the passage in chunk 1 ("the cup tastes thin and sour") would score highest and decide the article's rank.

The query above was not run against a real embedding for this guide (it needs an OpenAI call), so the ranking is illustrative. What follows is measured, on the real stored vectors.

## What the scores look like

A similarity number only helps if you know what is typical. On the development library:

| Pair | Cosine similarity |
|---|---|
| Two random chunks from different articles | median 0.27, 90th percentile 0.44, 99th percentile 0.59, maximum 0.85 (38,471 pairs) |
| Best match for a random chunk in another article | median 0.59, 90th percentile 0.78 (150 chunks) |
| Neighbouring chunks of the same article | median 0.78 (518 pairs) |

Read this as: 0.3 or below is noise, 0.5 to 0.6 is related, 0.8 or above is nearly the same content. The maximum of 1.00 for a best match means two different articles contain an identical passage (the same piece saved twice, or syndicated text).

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/chunk_embeddings.py` | `split_article_into_chunks`, `contextual_prefix`, `generate_chunk_embeddings`, and the sweep `process_all_missing_chunks` |
| `content-queue-backend/app/tasks/embedding.py` | embeds the whole article first, then dispatches chunking |
| `content-queue-backend/app/models/chunk.py` | the `ContentChunk` table |
| `content-queue-backend/app/core/llm_client.py` | `llm_client.embed`: provider, budget check, tracing |
| `content-queue-backend/app/core/hybrid_search.py` | `_semantic_search`: the query above |

Chunking runs after the article-level embedding finishes. A beat task every 5 minutes (`process_all_missing_chunks`) finds articles that have an embedding but no chunks, up to 100 per run, and dispatches them again. Articles that have no chunks yet are still searchable: the SQL's second branch falls back to the one article-level embedding.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| About 350 tokens per chunk | 128 (precise, loses context); 1,000+ (blurs again) | Common middle ground for prose. It was not tuned against an evaluation: see "What is weak". |
| Split at headings, then sentences | Fixed windows of N characters; semantic splitting that looks for topic shifts | Headings are free signal that authors put in. Fixed windows cut mid-sentence; semantic splitting costs extra embedding calls. |
| 40 words of overlap | None; 10 to 20% overlap | A sentence on a boundary stays whole in at least one chunk. Cost: about 9% duplicated text on a 447-word chunk. |
| Template prefix with title and position | An LLM writes a one-sentence context per chunk (the contextual retrieval technique this is modeled on) | A template is free and instant. An LLM-written blurb carries more information (for example what the section argues) but costs one LLM call per chunk. |
| Store the chunk text in the database | Re-slice `full_text` by offsets at read time | Search results and connections can show the matching passage with a single query. Cost: the text is stored twice (about 1 MB for 573 chunks). |
| Score an article by its best chunk (`MAX`) | Average of chunks; sum of the top 3 | One strong passage is enough evidence an article is relevant; an average would punish long articles that touch the topic once. |
| Replace all chunks on re-run | Update only changed chunks | Idempotent and simple. The article is re-embedded in full, which is cheap. |

## How it fails

| Failure | What causes it | What happens | What was done |
|---|---|---|---|
| Chunk over the model's input limit | Text with no spaces or sentence marks (CJK, minified code, base64) is counted as few words | The embedding call is rejected | Each chunk is cut at 4,000 characters (`_MAX_CHUNK_CHARS`) |
| More than 2,048 inputs in one request | A very large page produces thousands of chunks | OpenAI rejects the request | Embedding runs in batches of 100 |
| Memory spike | A 20 MB page produced 7,634 chunks, all vectors held at once: 1.9 GB measured | Worker near its memory limit | Batching, plus a download size cap. See [Memory: investigation and fixes](/reliability/memory/) |
| Provider returns fewer vectors than texts | API fault | Chunks would silently be dropped | The task raises and rolls back, keeping the old chunks |
| Daily LLM budget reached | Per-user spend ceiling | Task stops with `budget_exceeded`, old chunks kept | Retrying would not help, so it does not retry |
| Worker dies mid-run | Deploy, memory kill | Article has no chunks | The 5-minute sweep re-dispatches it; search uses the article-level embedding meanwhile |

The fallback is exercised in practice: in the development database 65 active items have an embedding, and 52 of them have chunks.

## What is weak or missing

Ranked by how likely each is to matter.

1. **The parameters were never tuned.** Size, overlap and the prefix come from common practice. The retrieval evaluation compares retrieval designs (article-level, chunk-level, with and without the entity lane), not different chunk sizes. There is no measurement saying 350 beats 200 or 600 for this content.
2. **Size is counted in words.** The code assumes 0.75 words per token; the sample measured 0.86 (447 words = 522 tokens), so a chunk can be 50% over its token target. A tokenizer is already installed (`tiktoken` is used for the article-level embedding), so counting real tokens would be a small change.
3. **Overlap starts mid-sentence, and headings merge into the first sentence.** Both make the text a little harder to read when shown as a search result.
4. **The prefix is a template.** It adds the title and position but nothing about what the section says. The "section 3 of 4" label is the chunk number, not a document section.
5. **Titles are baked in.** The title is part of the text that was embedded. If the title is edited later, the existing chunk embeddings still reflect the old title (inferred from the code; nothing re-chunks on a title change).
6. **Tables, lists and code are flattened to plain text.** A table becomes a run of words in reading order.
7. **Old chunks differ from new rules.** The development database holds chunks up to 5,410 characters, created before the 4,000-character cap existed.

{% say %}
When you save an article, a background job cuts it into passages of a few hundred tokens, at headings first and sentence ends second, with a little overlap so a sentence on a boundary is not lost. Each passage gets a one-line prefix with the article title so it still makes sense on its own, and then goes to OpenAI's embedding model, which returns 1,536 numbers that stand for its meaning. Those go in Postgres next to the passage. At search time I embed the query the same way and compare it with every passage; an article ranks by its best passage, so a long article can win on one paragraph. The weak spots are that the sizes were never tuned against an evaluation, and size is estimated in words, not real tokens.
{% endsay %}

## Check yourself

{% qa "Why does the code use MAX over an article's chunks rather than an average?" %}
An article is relevant if any part of it answers the query. An average would dilute one strong passage with all the unrelated ones, which is the same blurring that chunking was introduced to avoid.
{% endqa %}

{% qa "A 5,000-word article is saved. Roughly how many chunks, and which constants decide it?" %}
About 11 to 12 chunks: 5,000 words divided by the 466-word target, a little more because chunks close before the next sentence would overflow, and headings can add shorter chunks. The constants are `_TARGET_WORDS` (466), `_MIN_CHUNK_WORDS` (20) and the overlap of 40 words, which adds a few words to every chunk after the first in a long section.
{% endqa %}

{% qa "What does the contextual prefix change, and where is it stored?" %}
It changes the text that the embedding model reads, so the vector carries the article's subject. It is added only when computing the embedding; `content_chunks.text` stores the passage without it.
{% endqa %}

{% qa "The embedding provider returns 99 vectors for a batch of 100 texts. What happens?" %}
The task raises `ValueError` ("Embedding count mismatch"), the transaction rolls back, and the article keeps its previous chunks. Without that check, `zip` would silently drop one chunk.
{% endqa %}

{% qa "Two unrelated articles give a best-match similarity of 0.45. Is that a connection?" %}
No. On the measured library, random chunk pairs from different articles have a median of 0.27 and a 90th percentile of 0.44, so 0.45 is within noise. Related content typically scores 0.5 to 0.6 and above.
{% endqa %}
