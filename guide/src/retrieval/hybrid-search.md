---
layout: layouts/chapter.njk
title: Hybrid search
part: 2
order: 3
summary: How one search box turns a query into a route, runs keyword, filter and semantic lanes, and merges their rankings with reciprocal rank fusion.
verified: 2026-10-26
permalink: /retrieval/hybrid-search/
---

## In one minute

The search box sends every query to one function, `hybrid_search`. A small set of regular-expression rules, with no model call, classifies the query as a filter (`tag:coffee`, a domain, a known author), a keyword search (one to three words, or a quoted phrase), a semantic search (a question), or hybrid (four or more words). Keyword search runs inside Postgres against a text index and costs nothing. Semantic search embeds the query with OpenAI, caches the vector in Redis for an hour, and compares it with the user's stored passages. Hybrid runs both and merges the two ranked lists with reciprocal rank fusion, which scores each article as the sum of 1 / (60 + rank) over the lists it appears in. The modal search (`mode=full`) skips the router and runs every lane. Each lane that calls an outside service fails open: if OpenAI or Redis is down, the other lanes still answer.

## The problem

**How do you combine search methods that score on different scales, without a model to decide which to trust?**

Keyword search and meaning search fail in opposite ways, as [RAG from first principles](/retrieval/rag-from-first-principles/) showed. Keyword search misses paraphrases; semantic search blurs names and codes. Running both is easy. Combining the results is not, because the scores are incomparable. A keyword rank score runs from 0.001 to about 0.06 (measured below); a cosine similarity runs from 0.3 to 1.0. Adding them, or averaging, lets whichever lane has bigger numbers win regardless of quality.

Two simpler designs fall short. Always running every lane for every query costs an embedding call per keystroke-sized query and slows simple lookups: searching for a single word does not need a model. Picking one lane per query by rule is cheap but wrong whenever the rule guesses wrong. The design here uses rules to skip work when the answer is obvious, and fusion to hedge when it is not.

## Mental model

Picture two judges at a talent show who score on different scales: one gives out of 10 and the other out of 1,000. Instead of adding the scores, ask each judge only for a ranking, then give each act points for its place in each ranking. An act that both judges put near the top beats an act that one judge loved and the other never saw. That is reciprocal rank fusion (RRF). It uses only positions, so it needs no calibration between lanes.

| Term | Meaning here |
|---|---|
| Lane | One retrieval method that returns a ranked list of articles: filter, keyword, semantic or entity. |
| Route | The classifier's choice of what to run for a query: `filter`, `keyword`, `semantic` or `hybrid`. |
| Operator | A `key:value` token such as `tag:coffee` or `after:2026-01-01`. |
| tsvector | Postgres's indexed form of text: normalised words with positions, searched with `@@`. |
| Prefix match | A query term followed by `:*`, matching every word that starts with it. |
| RRF | Reciprocal rank fusion: score = sum over lists of 1 / (k + rank), here k = 60. |
| Fail open | When a part breaks, return less (an empty lane) rather than an error. |

{% fig "From request to results" %}
```text
GET /search/semantic?query=...&mode=auto|full
  |  rate limit (30/min, 300/hour)
  v
classify_query  ---- operators? --> filter lane (SQL WHERE)
  |            \---- 1-3 words  --> keyword lane (tsvector)
  |             \--- question    --> semantic lane (embed + vectors)
  |              \-- 4+ words    --> keyword + semantic, fused (RRF)
  v
date post-filter (after: / before:)
  v
articles + (always) highlight keyword search
```
{% endfig %}

## Walkthrough: one query through the router and every lane

The query is `design systems for writing`. It is a made-up phrase that happens to match words in the development library. Results are shown as letters in order of first appearance, with scores, because article titles and ids from that database are not reproduced here. Everything was run read-only against the development database (the session was set read-only) with the real functions in `content-queue-backend/app/core/hybrid_search.py`.

One honest substitution: the semantic lane needs an OpenAI embedding of the query, and the guide may not call OpenAI. I replaced the embedding call with a stored chunk vector (the first chunk of the article the keyword lane ranked fourth). The SQL, the ranking and the cache are real; the vector is not the embedding of the typed text, so the semantic ranks below show how the lanes interact, not how good the real semantic ranking is for this phrase.

### Step 1: classify

The router is a function of the string and the user's known author names. These are its first rules, in order:

<!--src content-queue-backend/app/core/search_router.py-->
```python
# 1. OPERATORS — explicit power-user syntax always wins
meta = _extract_operators(q)
if meta:
    return "filter", meta

# 2. EXACT PHRASE — quoted text → keyword (tsvector phrase search)
if _EXACT_PHRASE_PATTERN.search(q):
    return "keyword", {}
```

Operators are matched by the regular expressions in `_OPERATOR_PATTERNS`: `author:`, `tag:`, `site:`, `is:read|unread|archived`, `before:YYYY-MM-DD`, `after:YYYY-MM-DD`. The rest of the function:

<!--src content-queue-backend/app/core/search_router.py-->
```python
# 3. DOMAIN — looks like a URL domain → site filter
if _DOMAIN_PATTERN.match(q_lower):
    return "filter", {"site": q_lower}

# 4. KNOWN AUTHOR — matches user's library data → filter by author
if user_authors and q_lower in user_authors:
    return "filter", {"author": q}

# 5. QUESTION — interrogative start or trailing ? (checked before short-keyword
#    so "anything about stoicism?" doesn't get misrouted as keyword)
if q.endswith("?") or _QUESTION_STARTS.match(q):
    return "semantic", {}
```

After that come the short-keyword rule (at most three words and none of a list of question words anywhere in the query) and the default, hybrid. I ran `classify_query` on 20 realistic queries, with one known author ("paul graham") (captured):

| Query | Route | Metadata | Rule that fired |
|---|---|---|---|
| `espresso` | keyword | | 1 to 3 words |
| `react hooks` | keyword | | 1 to 3 words |
| `cold brew ratio` | keyword | | 1 to 3 words |
| `cold brew ratio guide` | hybrid | | 4 words, default |
| `why is my shot sour` | semantic | | question start |
| `anything about stoicism?` | semantic | | trailing `?` |
| `how does attention work` | semantic | | question start |
| `building products with AI` | hybrid | | 4 words, default |
| `"cold brew"` | keyword | | quoted phrase |
| `tag:coffee` | filter | tag = coffee | operator |
| `author:Paul Graham` | filter | author = Paul Graham | operator |
| `after:2025-01-01 espresso` | filter | after = 2025-01-01 | operator (the word `espresso` is dropped) |
| `stratechery.com` | filter | site = stratechery.com | domain pattern |
| `Paul Graham` | filter | author = Paul Graham | known author |
| `paul graham essays` | keyword | | 3 words (the author rule needs an exact match) |
| `find notes on sleep` | hybrid | | "find" alone is not a question start; 4 words |
| `explain` | semantic | | question start, one word |
| `c++` | keyword | | 1 to 3 words |
| `is:unread before:2026-01-01` | filter | is = unread, before = 2026-01-01 | operators |
| `what` | semantic | | question start |

Our query has four words, no operator, no question mark and no question word, so the route is `hybrid`.

### Step 2: the keyword lane

`keyword_search` builds a Postgres text query from two dictionaries and ORs them. For a multi-word query:

<!--src content-queue-backend/app/core/hybrid_search.py#161-163-->
```python
else:
    simple_query_sql = "websearch_to_tsquery('simple', :query)"
    params = {"query": query, "uid": user.id, "lim": limit, "simple_token": query}
```

and the SQL ranks matches with `ts_rank_cd(search_vector, websearch_to_tsquery('english', :query) || <simple query>, 32)`. The `english` form stems and drops stop words, the `simple` form keeps words as typed, and `||` means either may match. For a single alphanumeric word the simple form becomes a prefix query: `llm` becomes `llm:*`, which matches `llms`. The combined query for `espresso machines llm`, as Postgres prints it:

```text
'espresso' & 'machin' | 'llm':*
```

The `search_vector` column is rebuilt by a database trigger from title and author (weight A), description and tags (weight B) and the full text (weight C), each indexed through both dictionaries (`content_items_search_vector_update`, migration `e3f7a2c9b1d4_add_full_text_to_search_vector.py`). A match in a title therefore ranks above the same word deep in the body. Because every field is indexed twice, a word that both dictionaries produce identically (such as "espresso") is stored twice; this probably doubles its weight in the rank, though I did not measure that.

The lane on the example query returned 19 articles. The top eight:

{% fig "Keyword lane (captured; letters label articles)" %}
```text
keyword lane: 19 rows (fetch limit 30)
  rank  1  A  ts_rank_cd 0.0640
  rank  2  B  ts_rank_cd 0.0138
  rank  3  C  ts_rank_cd 0.0082
  rank  4  D  ts_rank_cd 0.0063
  rank  5  E  ts_rank_cd 0.0056
  rank  6  F  ts_rank_cd 0.0044
  rank  7  G  ts_rank_cd 0.0034
  rank  8  H  ts_rank_cd 0.0026
```
{% endfig %}

The `32` argument divides the raw rank by itself plus one, which maps it into 0 to 1. The scores here are tiny and drop fast; nothing about them is comparable to a cosine similarity. The lane silently stops at 50 rows (`limit = min(limit, 50)`).

### Step 3: the semantic lane, the cache and the cost

`_semantic_search` first checks that the user has any embedded article (otherwise it returns at once, before any API call). Then it asks the cache for the query vector. The cache lives in `content-queue-backend/app/core/embedding_cache.py`:

<!--src content-queue-backend/app/core/embedding_cache.py-->
```python
normalized = query.lower().strip()
cache_key = "qemb:" + hashlib.sha256(normalized.encode()).hexdigest()[:16]

cached = redis_client.get(cache_key)
if cached is not None:
    return json.loads(cached)

embedding = call_embed(query, user_id=user_id)
redis_client.setex(cache_key, 3600, json.dumps(embedding))
return embedding
```

The key is `qemb:` plus the first 16 hex characters of the SHA-256 of the lowercased, trimmed query. The time to live is 3,600 seconds. Captured from the run: the key for our query had 16 hex characters, a remaining time to live of 3,600 s right after it was set, and a stored value of 20,783 bytes (the vector as JSON text). I called the lane three times: the first call made 1 embedding call; the same query again made 0 more; the query in upper case with trailing spaces made 0 more. The key contains no user id and no model name, which has two consequences covered under "How it fails".

On a miss the lane makes one embedding call through the LLM gateway, which first checks the user's daily budget ($5 default) and records the spend afterwards. Cost model: `text-embedding-3-small` is priced in the code at $0.02 per million tokens. A query of about 6 tokens costs about $0.00000012 (illustrative; the token count is assumed). A cache hit costs nothing and skips the budget check. The keyword route makes no embedding call unless it finds nothing.

Then the SQL from [Chunking and embedding](/retrieval/chunking-and-embedding/) runs: one exact distance per chunk of the user, best per article. The lane returned 30 rows with real scores:

{% fig "Semantic lane (captured; stand-in query vector)" %}
```text
semantic lane: 30 rows; embed calls so far: 1
cache key: qemb:3366d0bcaf276c00  ttl(s): 3600  value bytes: 20783
  rank  1  D  cosine 1.0000
  rank  2  I  cosine 0.8647
  rank  3  F  cosine 0.5791
  rank  4  H  cosine 0.5609
  rank  5  J  cosine 0.5592
  rank  6  A  cosine 0.5342
  rank  7  K  cosine 0.5188
  rank  8  L  cosine 0.5170
  ... rank 30  cosine 0.3297  (lowest returned)
second identical query -> embed calls: 1
same text upper-case + spaces -> embed calls: 1
```
{% endfig %}

Article D is first at 1.0000 because the stand-in vector is one of its own chunks. Rank 30 still has a cosine of 0.33, which is noise level (random chunk pairs have a median of 0.27). There is no similarity threshold in this lane: it returns the best `limit` articles whatever their scores. I confirmed that with a query vector that points nowhere in particular (a vector with a single non-zero entry): the lane returned 10 articles with scores of 0.047 down to 0.030. A question about a topic the user never saved still gets ten confident-looking results.

### Step 4: fuse with RRF

The function is five lines:

<!--src content-queue-backend/app/core/hybrid_search.py#218-224-->
```python
scores: dict[str, float] = {}
for ranked_list in lists:
    for rank, id_ in enumerate(ranked_list, start=1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)

fused = sorted(scores, key=lambda x: scores[x], reverse=True)
return fused[:limit] if limit is not None else fused
```

Each article gets 1 / (60 + rank) from each list it appears in, and the scores are summed. First a small example computed by the real `rrf_fuse` (captured; lists are made up):

{% fig "RRF on two short lists (captured)" %}
```text
id  kw rank sem rank   1/(60+kw) 1/(60+sem)       sum
a         1        2     0.01639    0.01613   0.03252
c         3        1     0.01587    0.01639   0.03227
b         2        4     0.01613    0.01562   0.03175
e      None        3     0.00000    0.01587   0.01587
d         4     None     0.01562    0.00000   0.01562
rrf_fuse: ['a', 'c', 'b', 'e', 'd']
```
{% endfig %}

Article `a` wins by being second in one list and first in the other, and `c` is a close second. `e` and `d` appear in only one list and rank last, though `e` is third in the semantic list. Being in both lists is worth more than being first in one: the best possible score from one list is 1/61 = 0.01639 and a rank-4 article in both lists gets 2/64 = 0.03125.

The constant k = 60 controls how much a top rank is worth. A small k makes the top positions dominate; a large k flattens the curve so that agreement between lists matters more. With lists `[p, q, s, c]` and `[r, t, u, c]` (p is first in one list, c is fourth in both), the order changes with k (captured):

| k | First two |
|---|---|
| 1 | p, r |
| 10 | c, p |
| 60 | c, p |
| 1000 | c, p |

The value 60 comes from the original RRF paper; sed.i has not tuned it.

Now our query. The fusion of the two lanes above, with the rank in each list:

{% fig "Fusion of the keyword and semantic lanes (captured)" %}
```text
fusion (top 8):  kw_rank sem_rank  1/(60+kw) + 1/(60+sem) = score
   1 D      4    1   0.03202
   2 A      1    6   0.03154
   3 F      6    3   0.03102
   4 H      8    4   0.03033
   5 G      7   11   0.02901
   6 K     13    7   0.02862
   7 L     12    8   0.02859
   8 C      3   20   0.02837
```
{% endfig %}

D is fourth by keyword and first by (stand-in) semantic similarity, and tops the fused list. A is first by keyword and sixth by meaning and is second. Then the code throws the sums away and rewrites each score from the final position:

<!--src content-queue-backend/app/core/hybrid_search.py#868-874-->
```python
for rank, id_ in enumerate(fused_ids, start=1):
    if id_ not in item_lookup:
        continue
    result = dict(item_lookup[id_])
    result["score"] = 1.0 / (60 + rank)
    result["match_type"] = "hybrid"
    fused_results.append(result)
```

The result a client sees (captured): `D 0.01639, A 0.01613, F 0.01587, H 0.01562 ...`, every item with `match_type` "hybrid", and the order identical to `rrf_fuse`. The score is therefore a function of rank only, so it says nothing about how much better the first result is than the second.

### Step 5: the same query in full mode

`mode=full` ignores the router and runs all lanes. In the code it runs the filter lane (only if the query contains operators or is a domain or known author), the keyword lane, the semantic lane, and, if `ENTITY_SEARCH_ENABLED` is true, the entity lane. The flag defaults to `False` (confirmed by running with the default settings): the retrieval evaluation found the entity lane lowered ranking quality, and it is covered in [Entity graph and connections](/retrieval/entity-graph-and-connections/). Each lane fetches three times the page size, and the lanes are fused as before:

<!--src content-queue-backend/app/core/hybrid_search.py#786-795-->
```python
_scores: dict[str, float] = _dd(float)
for _rank, _id in enumerate(filter_ids, 1):
    _scores[_id] += 1.0 / (60 + _rank)
for _rank, _id in enumerate(kw_ids, 1):
    _scores[_id] += 1.0 / (60 + _rank)
for _rank, _id in enumerate(sem_ids, 1):
    _scores[_id] += 1.0 / (60 + _rank)
for r in entity_results:
    _scores[r["id"]] += r.get("score", 0.0) * _ENTITY_SCORE_SCALE
all_fused = sorted(_scores, key=lambda x: _scores[x], reverse=True)[:fetch]
```

On the example query, full mode returned the same ten articles in the same order as auto mode (captured: "ids equal: True"), which is expected here since the filter lane is empty. One labelling quirk: every one of the ten results had `match_type` "keyword", although the fused list included articles found only by the semantic lane. The label is set by whichever lane first put the article in a lookup table (filter, then keyword, then semantic), so it says "first lane that found it", not "the lane that ranked it". The synthesis tool and the research agent call `hybrid_search` in this mode.

### Step 6: when a lane fails

I ran the same query with each outside dependency broken (captured):

| Condition | What happened |
|---|---|
| OpenAI call raises | The semantic lane logged one warning (`_semantic_search degraded ...`) and returned `[]`. The hybrid route then returned 10 keyword results, all labelled `keyword`. No error reached the caller, and the response does not say the semantic lane was missing. |
| Redis unreachable | The inner try block failed, the lane called OpenAI directly, and search worked. Two identical queries made 2 embedding calls instead of 1 (no cache). |

The code that does this:

<!--src content-queue-backend/app/core/hybrid_search.py#309-321-->
```python
try:
    import redis as redis_lib

    r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
    r.ping()
    query_embedding = get_or_create_query_embedding(
        query, redis_client=r, user_id=str(user.id)
    )
except Exception:
    # Redis unavailable — call OpenAI directly
    from app.core.embedding_cache import call_embed

    query_embedding = call_embed(query, user_id=str(user.id))
```

This block catches every exception, including an OpenAI failure on the first attempt, and then calls OpenAI again. If OpenAI is the thing that is down, the query makes two failed calls, and the outer handler turns the second failure into an empty lane. A budget error (`BudgetExceededError`, raised by the gateway when a user passes the daily ceiling) takes the same path, so a user over budget silently loses semantic search.

The keyword lane and the filter lane have no such protection. They are plain database queries; if the database fails the request fails. The module docstring says the function "Returns [] on any failure — never raises", which is true only of the semantic and entity lanes.

### Step 7: dates, offsets and the highlight lane

Date operators `after:` and `before:` are extracted before routing. In auto mode any operator at all sends the whole query to the filter lane, which applies only the operators. The remaining words are ignored. Measured on the development library:

| Query | Mode | Result |
|---|---|---|
| `espresso` | auto | 0 keyword matches, so the route fell back to semantic: 10 results labelled `semantic_fallback` |
| `after:2020-01-01 espresso` | auto | 10 results, all `filter`: every item after the date, regardless of "espresso" |
| `design after:2026-05-01` | auto | 10 results, all `filter` |
| `design after:2026-05-01` | full | 9 results, though 22 of the user's items are on or after the date |

The last row shows the second problem. In full mode the date filter runs after the page is cut to the requested size, in Python (`_apply_date_filter`). The top ten fused results included one older keyword match, the filter removed it, and the page came back with 9 results instead of 10. A page can come back short, and a late page can come back empty, even though matching items exist.

Paging works by fetching `offset + limit` results and slicing. The endpoint accepts `limit` from 1 to 50 and any `offset`. The keyword lane is capped at 50 rows, so for a keyword-route query asking for offset 50 or more, the lane returns at most 50 rows and the slice is empty (inferred from the code; the development library has too few matches to show it).

Every request to `/search/semantic` also runs a separate highlight search (`_search_highlights`), whatever the route. It uses only the `simple` dictionary, so no stemming, over highlight text and notes. It excludes deleted articles but, unlike the article lanes, does not exclude untitled ones, and it receives the raw query including any date operators. The article lanes exclude items with a null or empty title: the dev library has 4 of its 71 active items untitled.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/search_router.py` | `classify_query` (the rules) and `parse_filter_query` (the filter lane) |
| `content-queue-backend/app/core/hybrid_search.py` | `keyword_search`, `_semantic_search`, `_entity_search`, `rrf_fuse`, `hybrid_search` |
| `content-queue-backend/app/core/embedding_cache.py` | `get_or_create_query_embedding`, `call_embed` |
| `content-queue-backend/app/api/search.py` | the `/search/semantic` endpoint and highlight search |
| `content-queue-backend/app/middleware/rate_limit.py` | 30 requests per minute and 300 per hour on `GET /search/semantic` |

The endpoint validates `query` (at least 3 characters), `limit`, `offset` and `mode` (`auto` or `full`), loads the user's author and tag sets for the classifier, calls `hybrid_search`, then bulk-loads the result items in one query. The tag set is loaded on every request but the classifier never uses it; tags only filter when the user types `tag:`.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Regex router, no model | LLM query classifier; always run everything | Under a millisecond, free, predictable. Wrong for edge cases (table above). |
| RRF with k = 60 | Weighted score sum; learned reranker; score normalisation | Needs no calibration between lanes; one constant. A Cohere reranker was built, evaluated and removed (see [Measuring retrieval](/retrieval/measuring-retrieval/)). |
| Dual dictionary (english and simple) | english only | Stemming for prose, exact forms for acronyms and identifiers. Cost: double index size for text columns. |
| Prefix match for a single token | None | "llm" finds "llms". Applies only to one alphanumeric word because `to_tsquery` raises an error on characters like `+`. |
| Exact semantic scan, no threshold | HNSW index; minimum similarity cutoff | Exact and simple at library scale. No cutoff means a result list is never empty, and never says "nothing relevant". |
| Fail open per lane | Raise on lane failure | A search box that returns keyword results beats one that returns a 500. Cost: silent degradation. |
| Hybrid for 4 or more words | Hybrid for everything | Short queries are usually names or terms where keyword is enough, and skipping the embedding saves a call. |
| Entity lane off by default | On | Evaluation showed lower recall at 10, MRR and NDCG than chunks only. |

## How it fails

| Failure | What causes it | What happens | Status |
|---|---|---|---|
| Semantic lane absent | OpenAI down, budget exceeded, Redis and OpenAI both down | Keyword results only, no signal to the client | Logged as a warning on the server |
| Cached vector from a different model | Cache key has no model name; the provider can be switched (OpenAI to Bedrock Titan, both 1,536 dimensions) | Up to one hour of query vectors from the old model compared with stored vectors from the new one | Not handled; the code comment says a provider change needs a full re-embed |
| Operator drops the query text | Any operator sends the whole query to the filter | `after:2020-01-01 espresso` returns every item after the date | Not handled |
| Short or empty page | Dates are applied after paging | 9 results where 10 were requested; empty late pages | Not handled |
| No results found, still ten returned | No similarity threshold | A nonsense or off-topic query returns the closest ten | By design |
| Keyword lane rows capped at 50 | `min(limit, 50)` | Deep pages on keyword queries are empty | Not handled |
| Rate limit evaded or shared | The limiter reads the user from `request.state.user`, which nothing in the application sets | Falls back to the client address, so it is per address, not per user (inferred from the code) | Not handled |

## What is weak or missing

Ranked by how likely each is to matter.

1. **No quality signal in the result.** The final score is `1/(60 + rank)` and the semantic lane has no cutoff, so the UI cannot tell a strong match from the least-bad one. A minimum similarity for the semantic lane is the smallest fix. The existing thresholds elsewhere (0.3 for connections and similar items, 0.4 for entities) show the pattern.
2. **Operators discard the rest of the query.** Combining a filter with text (`tag:coffee sour`) is the natural thing to type and it ignores the text in auto mode. The fix is to run the filter as a `WHERE` clause on the keyword and semantic lanes instead of replacing them.
3. **Dates are applied after paging.** Pushing the date range into each lane's SQL would fix short pages.
4. **Silent semantic failure.** Return a flag (for example `degraded: ["semantic"]`) so the interface can say "meaning search unavailable".
5. **Cache key lacks the model.** Add the model name to the key. Also, an OpenAI outage costs two failed calls per query because the Redis fallback retries the same call.
6. **The router is untested on real traffic.** No data records which route a real query took or whether it was the right one. The labelled evaluation (see [Measuring retrieval](/retrieval/measuring-retrieval/)) measures retrieval quality, not routing.
7. **Linear cost in chunks per user.** About 5 ms for 557 chunks on the dev library; the scratch measurements put it at roughly 30 microseconds per chunk (illustrative extrapolation: 100,000 chunks would take several seconds). If a user ever reaches that size, a pre-filter by article and an HNSW query is the next step, with the filtered-search caveat in [RAG from first principles](/retrieval/rag-from-first-principles/).
8. **The ARCHITECTURE.md claims about this area are partly stale.** Differences found while writing are in the guide's discrepancies folder (file 2.1-2.3).

{% say %}
Search goes through one function that first classifies the query with regular expressions: operators, domains and known authors become SQL filters, one to three words or a quoted phrase go to Postgres full-text search, questions go to semantic search, and anything longer runs both and merges them. Full-text search is free and indexes titles, authors, tags and body text through both an English and a plain dictionary. Semantic search embeds the query, caches the vector in Redis for an hour, and compares it with every passage the user has, taking the best passage per article. To merge the two I use reciprocal rank fusion: each article gets 1 over 60 plus its rank from each list, summed, so only positions matter and the lanes' scores never need to be comparable. If OpenAI or Redis is down, that lane returns nothing and the others still answer, though the user is not told. The weak points are that there is no similarity cutoff, an operator throws away the rest of the query, and date filters are applied after paging.
{% endsay %}

## Check yourself

{% qa "Why does RRF use ranks instead of the scores each lane returns?" %}
The lanes' scores are on unrelated scales: a keyword rank of 0.06 and a cosine of 0.6 mean different things, and adding them would let one lane dominate. Ranks are comparable across lanes, so summing 1 / (60 + rank) rewards articles that several lanes place near the top without any calibration.
{% endqa %}

{% qa "A user types `tag:coffee why is my shot sour` in the navbar. What runs, and what is lost?" %}
The operator rule fires first, so the route is `filter` with tag = coffee. Only the tag filter runs, ordered by date. The words "why is my shot sour" are ignored, so no keyword or semantic ranking happens. In modal (full) mode the filter lane runs alongside the other lanes, and the text does reach them.
{% endqa %}

{% qa "OpenAI is unreachable. What does a four-word query return, and how would a client know?" %}
The semantic lane catches the error, logs a warning and returns an empty list, so the hybrid route returns the keyword lane's results labelled `keyword`. The client cannot tell: the response has no flag for a missing lane.
{% endqa %}

{% qa "Two users type the same query within an hour. How many embedding calls are made, and what would break if the provider were switched in between?" %}
One, because the cache key is a hash of the lowercased query alone, shared across users. If the provider changed, the second user would still get the first user's cached vector, produced by the old model, compared against stored vectors from the new model, because the key does not include the model name.
{% endqa %}

{% qa "The semantic lane returns ten results for gibberish. Why, and what is the smallest fix?" %}
The query orders every article by cosine similarity and takes the top `limit`, with no minimum score. The smallest fix is a cutoff on the similarity in the SQL or after it, chosen from the noise level measured in [Chunking and embedding](/retrieval/chunking-and-embedding/): random pairs have a median of 0.27 and a 90th percentile of 0.44.
{% endqa %}

{% qa "Page 1 of a full-mode search with after:2026-05-01 has 9 results although 22 items match the date. Why?" %}
The page is cut to 10 first and the date filter is applied to those 10 in Python afterwards. One of the 10 was an older keyword match and was dropped. Applying the date inside each lane's SQL would keep pages full.
{% endqa %}
