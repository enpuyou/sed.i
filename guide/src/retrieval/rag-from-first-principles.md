---
layout: layouts/chapter.njk
title: RAG from first principles
part: 2
order: 1
summary: Why a language model needs retrieval, how embeddings and nearest-neighbour search work, and which parts of sed.i are retrieval only and which are full retrieval-augmented generation.
verified: 2026-10-26
permalink: /retrieval/rag-from-first-principles/
---

## In one minute

A language model has never seen your library, and it can read only a limited amount of text per request. Retrieval-augmented generation (RAG) solves both problems in two steps: first find the few passages that matter for the question (retrieve), then paste them into the prompt and ask the model to answer from them (generate). To find passages by meaning, every passage is turned into a list of numbers called an embedding, so that passages about similar things get lists that point in similar directions. Search then means finding the stored lists closest to the question's list. Doing that exactly means comparing against everything; an index called HNSW trades a little accuracy for not having to. sed.i uses retrieval alone for search, similar items and highlight connections, and full RAG for the research agent, the MCP `synthesize_topic` tool and the one-sentence connection insights. One fact that surprises people: the main search lane does not use the HNSW index at all.

## The problem

The question this chapter answers: **how does a program find the right few passages out of thousands, by meaning rather than by exact words, and what does it then do with them?**

Start with what a model cannot do. A language model knows what was in its training data. Your saved articles, highlights and notes were not in it. Asking "what did I read about grind size?" gets either a refusal or an invented answer.

The obvious fix is to paste the whole library into the prompt. That fails for two reasons. The context window (the amount of text a model reads per request) is finite, and a library of 71 articles runs to millions of characters. And even where it fits, cost and latency scale with prompt length, and models answer worse when the relevant sentence is buried in a pile of irrelevant ones.

The second obvious fix is keyword search: find articles containing the words in the question. It is fast and exact, and it misses everything phrased differently. "Why is my shot sour?" shares no words with a paragraph that says "under-extracted espresso tastes acidic". Searching by meaning needs a different representation of text.

## Mental model

Think of a map where every passage of text is a pin. Passages about similar things are pinned close together: espresso passages in one district, Python passages in another. To search, you drop a pin for the question and look at the nearest pins. An embedding model is the machine that decides where each pin goes. The map has 1,536 directions instead of two, and you cannot read the axes. Only distances between pins mean anything.

| Term | Meaning here |
|---|---|
| Embedding | The list of 1,536 numbers a model returns for a text. A point on the map. |
| Vector | A list of numbers treated as a point or an arrow. An embedding is a vector. |
| Similarity / distance | A number saying how close two vectors are. Several definitions exist; see the walkthrough. |
| Nearest-neighbour (NN) search | Given a query vector, find the stored vectors closest to it. |
| Exact search | Compare the query with every stored vector. Always right, cost grows linearly. |
| Approximate NN (ANN) | Use an index to compare with only a fraction of the vectors; may miss a true neighbour. |
| HNSW | Hierarchical Navigable Small World: an ANN index built as layers of a proximity graph. |
| Recall | Of the true top-k neighbours, the fraction the approximate search returned. |
| Retrieval | Finding the passages. RAG adds generation: the model writes an answer from them. |

{% fig "Retrieval-augmented generation" %}
```text
question
  |  embed (turn into 1,536 numbers)
  v
query vector
  |  nearest-neighbour search over stored vectors
  v
top passages            <- retrieval stops here for search
  |  paste into the prompt with the question
  v
language model
  |  generate
  v
answer grounded in the passages   <- full RAG
```
{% endfig %}

## Walkthrough: from two dimensions to 1,536

The input is the question "why is my shot sour?" and four documents. Everything in the first three steps is computed by scripts in the backend virtualenv, and the output is pasted as printed.

### Step 1: a two-dimensional toy

Pretend the model has only two axes: how much a text is about coffee, and how much about programming. The coordinates below are made up (illustrative); a real model chooses its own axes. The script ranks the four documents against the question three ways.

{% fig "Toy 2-D embeddings, captured output" %}
```text
document                           |d|  cosine    dot  euclid
grind size for espresso           0.96   0.998  0.870   0.071
water temperature, extraction     1.65   0.997  1.500   0.743
python generators explained       0.95   0.216  0.188   1.167
coffee for developers             0.78   0.814  0.578   0.532
cosine  ranking: grind > water > coffee > python
dot     ranking: water > grind > coffee > python
euclid  ranking: grind > coffee > water > python
```
{% endfig %}

Two things to read off. The espresso documents are nearest the question and the Python document is farthest, so the toy map works. And the three measures disagree on second place: that is the next step.

### Step 2: three ways to measure closeness

Take one pair, the question (0.90, 0.15) and "grind size for espresso" (0.95, 0.10), and compute all three measures by hand-written formulas:

{% fig "One pair, three measures (captured)" %}
```text
cosine   = 0.8700 / (0.9124 * 0.9552) = 0.9982
dot      = 0.9*0.95 + 0.15*0.1 = 0.8700
euclid   = sqrt((0.9-0.95)^2 + (0.15-0.1)^2) = 0.0707
after scaling both to length 1: cosine 0.9982, dot 0.9982,
  euclid^2 0.0036, 2-2*cos 0.0036
```
{% endfig %}

| Measure | Formula | Sees direction | Sees length | Larger means |
|---|---|---|---|---|
| Cosine similarity | dot(a, b) / (length(a) * length(b)) | yes | no | more similar |
| Dot product | sum of a[i] * b[i] | yes | yes | more similar |
| Euclidean distance | length of (a - b) | yes | yes | less similar |

Cosine similarity ignores how long the vectors are and looks only at the angle. The dot product rewards long vectors: in step 1 the long "water temperature" vector (length 1.65) wins under dot product although "grind size" points almost exactly at the question. Euclidean distance penalises length differences for the same reason.

The last line of the output is the useful fact. When every vector has length 1, the three measures carry the same information: cosine equals the dot product, and squared Euclidean distance equals 2 - 2 * cosine. OpenAI's embedding vectors have length 1 (the stored ones measure 0.9999 to 1.0002), so for sed.i the choice of measure does not change rankings. pgvector's three operators are `<=>` (cosine distance, which is 1 minus cosine similarity), `<->` (Euclidean distance) and `<#>` (negative dot product, negative so that "smaller is closer" holds for all three). sed.i uses `<=>` everywhere.

### Step 3: the same pair at 1,536 dimensions

Two real stored chunks from the development database (different articles, same user), measured with the three pgvector operators in one query. Only numbers are shown:

{% fig "Real pair, 1,536 dimensions (captured with psql)" %}
```text
cosine_sim     | 0.35490
cosine_dist    | 0.64510      <- the <=> operator; 1 - 0.35490
dot_product    | 0.35490      <- minus the <#> operator
euclid_dist    | 1.13587      <- the <-> operator
norm_a         | 1.00006      <- vector lengths
norm_b         | 0.99995
euclid_sq      | 1.29021
two_minus_2cos | 1.29020      <- matches euclid_sq
```
{% endfig %}

Nothing changed except the number of axes. Because both vectors have length 1, cosine and dot product are identical and squared distance is 2 - 2 * cosine to five decimals. A similarity of 0.35 is in the noise band: [Chunking and embedding](/retrieval/chunking-and-embedding/) measured random pairs from different articles at a median of 0.27.

### Step 4: exact search, and why it is not always enough

Exact search computes the distance from the query to every stored vector and sorts. It needs no index and is never wrong. Its cost is linear in the number of vectors.

I measured the shape of that cost in a scratch database (not the dev data): 20,000 random 1,536-dimension vectors in a table with a user column, then the aggregate the main search lane runs over one user's rows:

| Rows scanned | Execution time (measured, one run, local Docker) |
|---|---|
| 500 | 12.9 ms |
| 6,500 | 203.9 ms |
| 20,000 | 220.3 ms (all rows; the planner used parallel workers) |

That is about 30 microseconds per vector in the single-process runs, so the cost is proportional to how many chunks one user has. The development library has 557 chunks for its main user, which takes about 5 ms. Extrapolating (illustrative, not measured) a user with 100,000 chunks would wait a few seconds. Approximate search exists to avoid that.

### Step 5: how HNSW avoids scanning everything

HNSW is a graph. Each vector is a node linked to a few of its near neighbours. The nodes are also placed on layers: all of them on the bottom layer, about 1 in M on the layer above, about 1 in M squared on the next, and so on. The upper layers are sparse, so their links are long jumps across the map. The bottom layer is dense, so its links are short steps.

A search starts at the top layer and walks greedily: from the current node, look at its neighbours, move to whichever is closest to the query, repeat until no neighbour is closer. Then drop to the next layer, starting from that node, and repeat. On the bottom layer the search keeps a beam: it tracks the best `ef_search` candidates it has seen, expands the most promising one, and stops when no unexplored candidate can beat the worst one in the beam. The best k of the beam are the answer.

{% fig "HNSW search" %}
```text
layer 3   o-------------------o            long jumps, few nodes
             \
layer 2   o---o-----o----o---o--o
                \
layer 1   o-o-o-o-o-o-o-o-o-o-o-o-o
                     \
layer 0   ooooooooooooooooooooooooooo      every vector; beam search here
          start at top, walk down, widen the search only at the bottom
```
{% endfig %}

I wrote a 60-line version in Python and ran it on 2,000 random two-dimensional points (illustrative; this is a toy, not pgvector, and the links are built by brute force). The script assigns layers with the formula from the HNSW paper, links each node to its 8 nearest neighbours in each layer it belongs to, and counts distance computations:

{% fig "Toy HNSW over 2,000 points, captured output" %}
```text
nodes per layer: [2000, 253, 29, 2]
one query, ef_search=10:
  layer 3: walk [557, 1546]
  layer 2: walk [1546, 1944, 648]
  layer 1: walk [648, 987]
  distance computations: 64 of 2000; found 10 of the true top 10

200 random queries, top 10:
ef_search  recall@10  avg distance computations
       10      0.999        54
       20      1.000        67
       40      1.000        91
      100      1.000       160
```
{% endfig %}

The layer sizes shrink by about a factor of 8, which is M. The query touched 64 of 2,000 vectors and still found the true top 10. The table shows the central trade-off: raising `ef_search` widens the beam, which costs more distance computations and buys more recall.

Recall is the fraction of the true top-k that the approximate search returns. A recall of 0.999 at k=10 means one true neighbour in a thousand was missed. Two dimensions are easy, though. In 1,536 dimensions the geometry is harder, and recall depends on how clustered the data is.

I measured that on two datasets:

| Data | ef_search 10 | 40 (the default) | 100 | 400 |
|---|---|---|---|---|
| Random 1,536-d vectors, 20,000 rows, 30 queries (measured, scratch) | 0.563 | 0.630 | 0.713 | 0.993 |
| The development library's own 573 chunks, each used as a query (measured) | 0.998 | 0.9995 | 0.9995 | 0.9997 |

Random vectors in 1,536 dimensions are nearly equidistant from each other, so there is no structure for the graph to exploit: this is the worst case. Real embeddings cluster, and on the real library the index is almost indistinguishable from exact search. The remaining 0.0003 is probably ties between identical vectors that sort in a different order (not verified). The repository's earlier benchmark in `evals/hnsw-index/results/report.md` found the same low recall on random vectors and drew the same conclusion.

### Step 6: the three parameters

The index definitions in the development database are all identical in shape (read from `pg_indexes`):

{% fig "The four HNSW indexes (captured)" %}
```sql
CREATE INDEX content_chunks_embedding_hnsw ON public.content_chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m='16', ef_construction='64')
-- same options on content_items, highlights and entities
```
{% endfig %}

| Parameter | Set when | Meaning | Trade-off |
|---|---|---|---|
| `m` (16) | index build | Links per node per layer (the bottom layer allows twice as many in pgvector) | Higher: better recall, bigger index, slower build |
| `ef_construction` (64) | index build | Beam width while inserting each vector into the graph | Higher: better graph, slower build |
| `ef_search` (40, pgvector default) | each query | Beam width at query time | Higher: better recall, slower query |

`vector_cosine_ops` says the index is built for the cosine operator. An index built for `<=>` is used only by queries that order by `<=>`.

### Step 7: which queries actually use the index

This is the part to get right. The HNSW index serves one query shape: `ORDER BY embedding <=> q LIMIT k`, with no aggregate on top. Anything else (a `GROUP BY`, a `MAX`, a filter that has to see every row) cannot use it.

The main semantic search lane is not that shape. It computes `MAX(1 - (embedding <=> q))` per article for all of one user's chunks. I ran `EXPLAIN (ANALYZE)` on the development database using a stored chunk vector as the query (user id and vector elided):

{% fig "Semantic lane, default planner (captured)" %}
```text
HashAggregate (actual rows=49 loops=1)
  Group Key: cc.content_item_id
  ->  Nested Loop (actual rows=543 loops=1)
        ->  Seq Scan on content_chunks cc (actual rows=557 loops=1)
              Filter: ((embedding IS NOT NULL) AND (user_id = '<user>'))
              Rows Removed by Filter: 16
        ->  Memoize (actual rows=1 loops=557)
              ->  Index Scan using content_items_pkey on content_items ci
Execution Time: 5.018 ms
```
{% endfig %}

It reads all 557 chunks of the user, computes 557 distances, and keeps the best per article. The exact distance is computed for every chunk, so this lane has no recall problem; its cost is linear in the user's chunk count, which Step 4 measured. Forcing the planner away from sequential scans (`SET enable_seqscan = off`) changes the access path to the user-id index but not the amount of work: still 557 rows.

The other vector queries in the code are of the top-k shape: highlight connections and similar items in `content-queue-backend/app/api/search.py`, and per-article chunk retrieval in `content-queue-backend/app/tasks/research.py`. They add a user or article filter and, for connections and similar items, a similarity threshold. I ran each shape on the development database. With the default planner none of them used the HNSW index, because at 573 chunks, 76 highlights and a few hundred items a sequential or bitmap scan plus a sort is cheaper than walking a graph:

{% fig "Top-k over chunks, default planner (captured)" %}
```text
Limit (actual rows=5 loops=1)
  ->  Sort (actual rows=5 loops=1)
        Sort Method: top-N heapsort  Memory: 25kB
        ->  Seq Scan on content_chunks (actual rows=557 loops=1)
              Filter: ((embedding IS NOT NULL) AND (user_id = '<user>'))
Execution Time: 1.886 ms
```
{% endfig %}

The index is eligible when the query has no filter. Dropping the `WHERE` and turning sequential scans off (so the planner has to choose an index) gives:

{% fig "Unfiltered top-k with sequential scans disabled (captured)" %}
```text
Limit (actual rows=5 loops=1)
  ->  Index Scan using content_chunks_embedding_hnsw on content_chunks
        Order By: (embedding <=> '<q>')
Execution Time: 39.860 ms
```
{% endfig %}

That time includes loading index pages that were not yet in memory, so it should not be compared with the 1.9 ms above. The point is that the plan node appears.

What happens with a per-user filter at a size where the index matters? The scratch database answers that: 20,000 random vectors split across four users, a B-tree on the user column, and an HNSW index (`m=16`, `ef_construction=64`).

{% fig "Scratch database, 20,000 rows (captured)" %}
```text
1: no filter                   -> Index Scan using chunks_hnsw
2: user 3 (about 6,500 rows)   -> Index Scan using chunks_hnsw
                                  Filter: (user_id = 3)
                                  Rows Removed by Filter: 5
3: user 1 (500 rows)           -> Bitmap Heap Scan on chunks + top-N sort
                                  (the planner ignored the HNSW index)
4: user 1, HNSW forced         -> Index Scan using chunks_hnsw
                                  Filter: (user_id = 1)
                                  Rows Removed by Filter: 39
                                  actual rows=1 (asked for 10)
```
{% endfig %}

Case 4 is the known weakness of filtered approximate search. The index returns its `ef_search` closest candidates (40), and the user filter is applied afterwards. Only 1 of the 40 belonged to user 1, so the query returned 1 row instead of 10. pgvector 0.8 has a setting for this (`hnsw.iterative_scan`, off by default): with `relaxed_order` the same query returned all 10 rows. In case 3 the planner avoided the problem by choosing the user-id index and sorting 500 rows exactly. So whether a per-user top-k query is exact or approximate depends on the planner's cost estimate, which depends on table size and statistics. sed.i has not set the iterative scan option anywhere (inferred from searching the code); the effect is a risk that appears with many users on one table, not something the single-user development data can show.

### Step 8: storage arithmetic

Measured on the development database:

| Quantity | Value | How |
|---|---|---|
| One stored `vector(1536)` | 6,148 bytes | `pg_column_size(embedding)`. The floats are 1,536 x 4 = 6,144 bytes; the other 4 bytes are header (inferred) |
| 573 chunk embeddings | 3,522,804 bytes (3.4 MiB) | 573 x 6,148 |
| HNSW index on those 573 | 4,677,632 bytes | `pg_relation_size('content_chunks_embedding_hnsw')` |
| Index bytes per vector | 8,163 | 4,677,632 / 573 |
| Scratch: 20,000 vectors | 163,848,192 bytes | 8,192 bytes per vector, one 8 KiB page each (inferred) |

The graph with its links is about 1.33 times the size of the raw vectors. The other three indexes measured 6,815,744 bytes (entities, 831 vectors), 1,605,632 (items) and 630,784 (highlights, 76 vectors). Illustrative scale-up: 100,000 chunks would be about 615 MB of vectors and about 820 MB of index. The scratch build of 20,000 vectors took 127.7 seconds with the default build memory, and Postgres warned that the graph no longer fit in `maintenance_work_mem`; builds get slow when the graph outgrows that setting.

### Step 9: why keyword search is still needed

Embeddings capture meaning and blur exact strings. A search for an error code, a person's name, an acronym or a version number depends on those exact characters, and an embedding model may place "ERR_CONNECTION_REFUSED" near any text about network errors, with no guarantee that the passage containing the code is nearest (illustrative; not measured on this library). Keyword search over a tsvector matches the characters and costs no API call.

PostgreSQL turns text into a tsvector: a sorted list of lexemes (normalised words) with positions. The `english` dictionary drops stop words and stems words to a root; the `simple` dictionary lowercases only. Captured from the development database:

{% fig "to_tsvector on one sentence (captured)" %}
```text
english: 'bean':2 'espresso':3 'machin':4 'roast':1
simple:  'beans':2 'espresso':3 'machines':4 'roasting':1

english: 'api':3 'llms':1          <- text "LLMs and APIs"; 'and' dropped
simple:  'and':2 'apis':3 'llms':1
```
{% endfig %}

Stemming makes "roasting" match "roast" (good for prose). It also mangles identifiers: "machines" becomes "machin", and "LLMs" stays "llms" in English but "APIs" becomes "api". Indexing both dictionaries lets either form match. [Hybrid search](/retrieval/hybrid-search/) shows how sed.i queries both, and how it combines the keyword and meaning results.

## Where sed.i retrieves and where it does RAG

| Feature | Retrieves | Generates | Kind |
|---|---|---|---|
| Search (`GET /search/semantic`) | Keyword, filter, semantic lanes, fused | nothing | retrieval only |
| Similar items (`GET /search/{id}/similar`) | Items near this item's embedding | nothing | retrieval only |
| Highlight and article connections | Highlights near this highlight's embedding | nothing | retrieval only |
| Connection insight sentence | Connected article's highlights (found by the connections query) | One sentence from a small chat model, cached 7 days | RAG, minimal |
| Research agent | Hybrid search for candidates, then top chunks per article by similarity to each sub-question | Per-article summaries, then a synthesis, then verification | full RAG |
| MCP `synthesize_topic` | `hybrid_search` in full mode, 10 articles | A structured synthesis over title, description and highlight of each article | RAG |
| MCP `search_content`, `find_similar` | Same as search and similar | nothing | retrieval only |

Retrieval-only features return a list; the user reads the results. RAG features put retrieved text into a prompt, so the answer is only as good as what was retrieved, and a wrong retrieval produces a fluent wrong answer. The research agent chapter ([The research agent](/llm/research-agent/)) covers the generation steps.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/hybrid_search.py` | `_semantic_search` (exact lane), `keyword_search`, `rrf_fuse` |
| `content-queue-backend/app/api/search.py` | `/search/semantic`, highlight connections (top-k with threshold), similar items, insight sentence |
| `content-queue-backend/app/tasks/research.py` | `_fetch_top_chunks`: per-article top-k over chunks |
| `content-queue-backend/app/mcp/tools/synthesis.py` | `synthesize_topic`: retrieve, build context, generate |
| `content-queue-backend/alembic/versions/f4c8b6e21a3d_add_hnsw_index_on_search_embedding_columns.py` | creates the HNSW indexes |

The only vector query in the code that has the index-friendly shape and no threshold is the per-article chunk lookup in the research agent:

<!--src content-queue-backend/app/tasks/research.py-->
```sql
SELECT text
FROM content_chunks
WHERE content_item_id = :item_id
  AND embedding IS NOT NULL
ORDER BY embedding <=> CAST(:q AS vector)
LIMIT :k
```

It filters to one article first, so it scans only that article's few chunks (the plan on the development database used `idx_content_chunks_content_item`, a bitmap scan of 2 rows). The HNSW index is irrelevant to it.

The connections and similar-items queries add `AND (1 - (embedding <=> q)) >= :threshold`, which the HNSW index cannot evaluate, so they filter rows after the vectors are read:

<!--src content-queue-backend/app/api/search.py-->
```sql
WHERE user_id = :user_id
    AND id != :source_id
    AND deleted_at IS NULL
    AND embedding IS NOT NULL
    AND (1 - (embedding <=> CAST(:source_embedding AS vector))) >= :threshold
ORDER BY embedding <=> CAST(:source_embedding AS vector)
LIMIT :limit
```

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| pgvector inside Postgres ([ADR-0001](/talking/decision-log/)) | Qdrant, Pinecone, Weaviate | One datastore, one transaction, joins with `user_id` and `deleted_at`. The ADR sets migration triggers (5M vectors, 200 ms p95 latency). |
| Cosine distance (`<=>`) | Dot product, Euclidean | Equivalent for unit-length vectors; cosine stays correct if a provider ever returns unnormalised vectors. |
| Exact scan for the main lane | Use the HNSW index | At library scale exact is fast and never misses; a per-user filter makes HNSW unreliable (Step 7). The cost is linear in a user's chunk count. |
| HNSW defaults `m=16`, `ef_construction=64` | Larger values; IVFFlat | The pgvector defaults. Not tuned against sed.i data. IVFFlat needs training on existing data and is less accurate at the same speed. |
| Keep keyword search next to vectors | Embeddings only | Exact strings, no API cost, and a fallback when the embedding call fails. |
| Retrieve with embeddings, rank with RRF | Learned reranker | A Cohere reranker was built and removed; see [Measuring retrieval](/retrieval/measuring-retrieval/). |

## How it fails

| Failure | What causes it | What happens |
|---|---|---|
| Per-user top-k returns fewer than k rows | HNSW with a selective filter; `ef_search` candidates are filtered after retrieval | Fewer results than requested (1 of 10 in the scratch test). Not observable on single-user data. |
| Recall below 1 | Approximate search | A true neighbour is missed. On the development library: 0.9995 at k=10, ef_search 40. On random vectors: 0.63. |
| Index build is slow or memory-hungry | Graph larger than `maintenance_work_mem` | 127.7 s for 20,000 vectors in the scratch test; production builds the indexes with `CREATE INDEX CONCURRENTLY` in a deploy step so reads continue. |
| RAG answers confidently from a bad retrieval | The right passage was not retrieved | The model writes fluent text from the wrong sources. `synthesize_topic` filters cited sources to those it supplied, which stops invented citations but not a wrong retrieval. |
| Mixed embedding models | Provider switched after content was embedded | `EMBED_PROVIDER` can select a different 1,536-dimension model; old and new vectors would be incomparable. The code comment says a full re-embed is required. |

## What is weak or missing

Ranked by how likely each is to matter.

1. **`synthesize_topic` never shows the model a highlight.** Its context builder asks for the user's latest highlight on each article, but the filter uses `r.get("user_id")` and the search results do not contain a `user_id` key. The filter therefore compares the column to NULL and matches nothing. On the development data the context for 10 articles contained 0 highlight lines, although 4 of those articles have highlights (measured; the cause is read from the code).
2. **`synthesize_topic` discards the matching passage.** Retrieval finds each article by its best chunk, then the context is built from title, description and a highlight, not from that chunk. The research agent does pass chunks; this tool does not.
3. **The main lane scales linearly.** Fine at hundreds of chunks per user; see [Hybrid search](/retrieval/hybrid-search/) for the numbers and what would change it.
4. **Filtered ANN is untested.** No query sets `hnsw.iterative_scan`, and the development data cannot show the shortfall. A second user with a large library could hit it in the connections and similar-items queries once the planner chooses the index.
5. **The HNSW indexes mostly serve no query today.** On the development database none of the production query shapes chose them. They cost 13.7 MB of disk and slow every insert. The entity lane's query uses a threshold and a `GROUP BY`, so it cannot use its index either (inferred from the code; not run, the lane is off by default).
6. **The recall figures here come from small data.** Recall on the real library (573 chunks) says little about recall at 500,000 chunks.

{% say %}
A language model has not read my library and can only read so much at once, so I retrieve first and generate second. Every passage is turned into 1,536 numbers by an embedding model, so similar meanings end up pointing in similar directions. I measure closeness with cosine similarity; because OpenAI's vectors have length 1, cosine, dot product and Euclidean distance all give the same ranking. Exact search compares the query with every vector, which is always right and gets slower linearly. HNSW is a layered graph that walks greedily from a sparse top layer to a dense bottom layer, touching a small fraction of the vectors, and `ef_search` trades speed for recall. The surprise in sed.i is that the main search lane does not use that index: it computes exact distances over one user's chunks and takes the best per article, and on my small data the planner did not pick the index for the other queries either. Search, similar items and connections are retrieval only; the research agent, `synthesize_topic` and the insight sentences feed retrieved text to a model, which is full RAG.
{% endsay %}

## Check yourself

{% qa "All vectors from the embedding model have length 1. Which of cosine, dot product and Euclidean distance rank documents differently?" %}
None of them. For unit vectors the dot product equals the cosine, and the squared Euclidean distance equals 2 minus 2 times the cosine, so all three orderings are the same. They differ only for vectors of unequal length, as the 2-D toy shows where the long vector wins under the dot product.
{% endqa %}

{% qa "Why does the main semantic lane not use the HNSW index even though the table has one?" %}
The query aggregates: it takes the best score per article over all of a user's chunks. An HNSW index can only answer `ORDER BY embedding <=> q LIMIT k`, returning the nearest few vectors. The aggregate needs every chunk's distance, so the planner scans the user's chunks and computes exact distances.
{% endqa %}

{% qa "A top-k query with WHERE user_id = X is run through an HNSW index and returns 1 row instead of 10. What happened and what are two fixes?" %}
The index returned its `ef_search` nearest candidates (40) across all users, and the user filter then removed most of them. One fix is to enable pgvector's iterative scan, which keeps scanning until enough rows pass the filter. Another is to avoid the index for per-user queries, scanning that user's rows exactly through a user-id index, as the planner did for the 500-row user.
{% endqa %}

{% qa "Raising ef_search from 40 to 400 improved recall on random vectors from 0.63 to 0.99 but barely changed it on the real library. Why?" %}
Random 1,536-dimensional vectors are all about equally far from each other, so the graph has no structure to guide the walk and a wide beam is needed. Real embeddings cluster by topic, so a narrow beam already lands in the right neighbourhood.
{% endqa %}

{% qa "Which of these is RAG rather than retrieval only: the search box, the similar-articles panel, the connection insight sentence?" %}
The insight sentence. It retrieves a connected article's highlights and passes them with the source highlight to a chat model that writes one sentence. The search box and similar panel return ranked items and stop.
{% endqa %}

{% qa "Why keep a keyword lane if embeddings find meaning?" %}
Embeddings blur exact strings such as names, codes and acronyms, and cost an API call per query. The keyword lane matches characters exactly, is free, and still works when the embedding service is down.
{% endqa %}
