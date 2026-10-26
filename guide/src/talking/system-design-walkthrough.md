---
layout: layouts/chapter.njk
title: The 10-minute system-design walkthrough
part: 6
order: 1
summary: A spoken script for presenting the whole of sed.i in ten minutes, with a time budget per section, the numbers to quote, and fifteen follow-up questions with model answers.
verified: 2026-10-26
permalink: /talking/system-design-walkthrough/
---

## In one minute

This chapter is a script for presenting sed.i aloud, as in a system-design interview or an architecture review, plus the questions that usually follow. The talk has ten sections and a ten-minute budget: requirements and scale, the one-page architecture, the write path, the read path, the data model, the LLM layer, reliability, scaling to 10x and 100x, what is weak today, and what to do next.

Every fact comes from another chapter of this guide, and every number carries one of three labels. **Measured** means a script or benchmark recorded it. **Reported** means a production figure quoted from an audit or from the owner's notes and not re-checked here. **Estimate** means arithmetic on those figures. The honest shape of the talk is that sed.i is small and nearly idle in production, so the interesting questions are whether the design is correct, how it recovers, and what changes as it grows. Saying the weak points before you are asked is the strongest move available.

## The problem

**How do you explain a whole system in ten minutes so that a listener can judge it, and how do you survive the follow-up questions?**

The obvious approach is to tour the technology list: Next.js, FastAPI, Celery, Postgres, Redis, OpenAI. It fails because a list of parts says nothing about reasons, numbers or failures, and the listener cannot tell whether you understand the system or only know its names. The second obvious approach is to memorize the chapters of this guide. It fails at the first question that is off script.

The structure below fixes both. It starts from requirements and numbers, follows two real paths through the system (one write, one read), tells one failure story on each path, and ends with an honest ranking of weaknesses. The follow-up section then gives short answers built from the same facts, so an answer can be rebuilt from the evidence instead of recalled.

## Mental model

A guided tour of a building. First the floor plan (what the building is for and how big it is), then a walk down two main corridors (save and search), then a look at where the fire exits are and where the walls are cracked, then the renovation plan. A visitor who has seen the cracks pointed out by the guide trusts the rest of the tour.

| Term | Meaning here |
|---|---|
| Scale assumption | A stated size of load or data that the design is judged against, with its label (measured, reported, estimate). |
| Failure story | One real incident or test told in three beats: what broke, how it was found, what changed. |
| Lane | One retrieval method inside search: filter, keyword, semantic, entity. |
| Fail open | When a helper (Redis for limits, budgets, locks) is down, allow the request instead of refusing it. |
| Sweep | A scheduled job that finds unfinished rows in Postgres and re-dispatches them. |
| Outbox | A row written in the same transaction as the work it announces, so a separate job can send the message later; here the `pending` item row plays this role. |
| HNSW | An approximate nearest-neighbour index over vectors; exact search compares with every vector instead. |
| RRF | Reciprocal rank fusion: merge ranked lists by summing 1 / (60 + rank) per list. |
| p50 | The median of the samples. Production memory figures below are 7-day p50s of 5-minute Railway samples. |

{% fig "The ten minutes" %}
```text
0:00  1 requirements and scale        1:00
1:00  2 one-page architecture         1:00
2:00  3 write path + failure story    1:30
3:30  4 read path + retrieval story   1:30
5:00  5 data model, consistency       0:45
5:45  6 LLM layer and its controls    0:45
6:30  7 reliability, what breaks      1:00
7:30  8 scale: 10x, then 100x         1:15
8:45  9 what is weak, ranked          0:45
9:30 10 what to do next               0:30
10:00
```
{% endfig %}

## Walkthrough: the ten minutes, section by section

Each section gives the time, a script in first person, and the facts behind it. The scripts total about 1,300 words, which at a speaking pace of about 130 words per minute (an assumption; the scripts were not timed aloud) fits the budget. Cut from the facts, not from the failure stories.

### 1. Requirements and scale assumptions (1:00)

> sed.i is a read-it-later app for one reader at a time. You save a link, read it in a quiet reader, highlight, and search by keyword or by meaning. A research agent and an MCP server let an AI client use the same library. The requirements are: saving returns fast; every saved page becomes readable and then searchable; search answers interactively; one user never sees another user's data; the bill stays small, and the LLM bill is capped per user. Production is small. The last migration scanned about 98 active items in total. The services use about 1.1 GB of memory between them and under 0.02 vCPU on average. So the question is not whether it handles load. It is whether the design is correct, whether it recovers when something breaks, and what has to change as it grows.

| Number | Value | Status | Source |
|---|---|---|---|
| Active items in production | about 98 | Reported: the URL-normalization migration logged `scanned: 98` | [Deploy and operations](/platform/deploy-and-operations/) |
| Services in the 7-day baseline | 5: API, worker with beat, Prefect, Postgres, Redis | Reported; Prefect has since been stopped, so 4 run | [Memory](/reliability/memory/) |
| Memory p50 per service | worker 482 MB, API 315 MB, Prefect 222 MB, Postgres 158 MB, Redis 6 MB | Measured by Railway metrics, 5-minute samples | [Memory](/reliability/memory/) |
| Average CPU | below 0.02 vCPU on every service | Measured, same query | [Memory](/reliability/memory/) |
| Memory bill | about $11 a month | Estimate: 1.1 GB at an assumed $10 per GB-month | [Memory](/reliability/memory/) |
| Chunks per article | 10.4 average, median 9 | Measured on the development library | [Chunking and embedding](/retrieval/chunking-and-embedding/) |
| Chunks in production | about 1,000 | Estimate: 98 items x 10.4 | this chapter |

Two limits belong in the same breath. The number of production users and the production Postgres connection limit are not recorded anywhere this guide checked, so say "not verified" if asked.

### 2. The one-page architecture (1:00)

> Four things I run and a few I rent. A Next.js frontend on Vercel. A FastAPI backend on Railway, which also serves the MCP endpoint in the same process. A Celery worker with its scheduler, beat, in one process on Railway. A small Cloudflare Worker in front of the API that handles cross-origin rules. They share two stores: Postgres with pgvector, which holds all durable data, and Redis, which holds the job queue, caches, rate limits, locks and per-user spend counters. I rent OpenAI for embeddings and chat, with Amazon Bedrock as a chat fallback, and four observability tools. The rule that shapes everything is that the API never does slow work inline: saving writes a row and queues a message; searching is one SQL query. It is a modular monolith: one codebase, two programs.

{% fig "Topology" %}
```text
 Browser (Vercel)    Extension (Chrome, Safari)
        \                 /
         Cloudflare Worker (CORS)
                 |
          FastAPI (Railway)  ----> Redis: queue, cache, limits, locks
            |   (REST + MCP)           |
            |                   Celery worker + beat (one process)
            v                          |
     Postgres + pgvector <-------------+
                                       v
                           OpenAI (Bedrock for chat fallback)
```
{% endfig %}

Facts to have ready: Postgres and Redis are Railway-managed; Prefect exists in the repository but is stopped and disabled in production; the worker's start command is a dashboard setting, not a file in the repository. See [The system on one page](/orientation/system-on-one-page/).

### 3. The write path: saving a URL (1:30)

> POST content is rate limited, then the API normalizes the URL: lowercase host, no tracking parameters, no fragment. It looks for an active duplicate with one indexed query, inserts a row with status pending, commits, and only then puts a message on the queue and returns 201. The worker fetches the page with a 10 MB and 60 second cap, extracts the article, marks it completed, then embeds it and fans out two more tasks: tags and entities from one language-model call, and passage embeddings in batches of 100. The failure story is from a July test. I restarted Redis during a save. Redis told Docker it was healthy for about eight seconds while it was still loading and refusing commands, so the row committed and the queue send failed. The item sat at pending forever. The fix was a sweep every five minutes that re-dispatches anything still pending after ten minutes. The row is the outbox and the sweep is the relay, so recovery takes at most about fifteen minutes. What it does not cover, and I would say so, is an item stuck in processing after a worker is killed mid-task.

{% fig "Write path and its safety nets" %}
```text
POST /content -> insert 'pending', commit -> queue -> 201
   worker: extract_metadata -> extract_full_content ('completed')
        -> generate_embedding -> analyze_article + chunk embeddings

 stuck state                 sweep                       covered
 pending > 10 min            recover_stale_pending_items  yes
 chunks missing              process_all_missing_chunks   yes
 highlight embed missing     process_all_missing_embed.   yes
 processing (worker killed)  none                         no
 completed, no item embed    none                         no
```
{% endfig %}

Facts: the rate limit on this route is 10 per minute and 50 per hour per client address (from code); a save took 0.2 s at a 2,000-item library after the memory fixes (measured). A second story if time allows: the 6.5-million-message queue found by accident in the same July testing ([Distributed-systems review](/reliability/distributed-systems/)).

### 4. The read path: searching (1:30)

> A query goes to one function. Regular expressions classify it with no model call, in under two microseconds: operators and domains become filters, one to three words go to Postgres full-text search, questions go to semantic search, and four or more words run both and merge. Semantic search embeds the query, caches the vector in Redis for an hour, and runs one SQL query that computes the exact cosine distance to every passage the user has and keeps the best passage per article. The two lists are merged with reciprocal rank fusion, which scores each article as the sum of one over sixty plus its rank, so the lanes never need comparable scores. The retrieval story is that the table has an HNSW index and the main lane does not use it. The query aggregates per article, and the index can only answer nearest-k queries. I checked with EXPLAIN: 557 rows scanned, 5 milliseconds. Exact is right at this size, because it has no recall problem. When I tested the index with a per-user filter on 20,000 vectors it returned one row instead of ten, which is why I did not switch.

Facts: about 30 microseconds per vector and 5 ms for 557 chunks; index recall 0.9995 at k=10 on the real 573 chunks and 0.63 on random vectors (all measured, [RAG from first principles](/retrieval/rag-from-first-principles/)); recall at 10 on 45 queries 0.7459 article-level against 0.7652 passage-level, interval on the difference -0.031 to +0.071 (measured, [Measuring retrieval](/retrieval/measuring-retrieval/)). Do not claim the passage design is proven better. A failed lane returns nothing and the response does not say so ([Hybrid search](/retrieval/hybrid-search/)).

### 5. Data model and consistency (0:45)

> One Postgres database with 19 tables, almost all hanging off users, and every query carries a user_id filter; there is no row-level security. A saved page is one row. Passages, highlights, entities and tags hang off it. Deleting sets a timestamp and a daily job removes the row seven days later. A partial unique index on user and URL, where not deleted, makes duplicates impossible at the database level. Consistency is simple: there is one Postgres primary, so reads see their own writes. Derived data, meaning the embedding, passages, tags and entities, is eventually consistent, and each has a cheaper fallback: keyword search works once the text is extracted, and the article-level vector serves search until its passages exist. Redis is never the source of truth. The connection pool is three plus two per process, with a ten-second timeout.

Facts: [Data model and Postgres](/platform/data-model/). Soft delete is a convention that three paths ignore: the text-to-SQL tool, the highlight endpoints and the entity graph. Say that if asked about deletion.

### 6. The LLM layer and its controls (0:45)

> Every model call goes through one class, with 27 call sites in 15 files. A task names itself, like tagging or synthesis, and the class picks the model: the cheap one for most tasks, the larger one for article analysis, SQL generation and synthesis. Calls time out at 30 seconds with the SDK's own retries turned off. A chat call retries once after a second and then falls back to Bedrock. Embeddings never fall back, because vectors from two models are not comparable and would quietly corrupt search. Each user has a daily budget of five dollars in Redis, checked before the call and recorded after. A test parses the source and fails the build if a call site forgets the user id or the tracing span. The text-to-SQL tool is guarded by a parser: one SELECT, seven tables, a user predicate on every table, a 500 millisecond timeout. What I would flag: the structured calls, ten of the 27, skip the fallback; there is no circuit breaker; and stacked retries can turn one failure into twelve provider calls.

Facts: [The LLM gateway](/llm/gateway/), [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/). Prices in the code, per million tokens: gpt-4o-mini $0.15 in and $0.60 out, gpt-4o $2.50 and $10.00, embeddings $0.02. The text-to-SQL tool can return trashed items.

### 7. Reliability and what breaks first (1:00)

> There are four invariants: a user never sees another user's data; a saved URL is never silently lost; derived data converges to the source; LLM spend per user is bounded. The mechanisms are: the row is the truth and the queue message is only a hint; tasks are safe to run twice, for example passages are deleted and replaced in one transaction; sweeps repair what fell behind; helpers fail open; downloads are bounded. I tested these on purpose in July and found real bugs: retry settings that never fired because a task swallowed the error, and a time limit that does nothing. What breaks first is the worker. It runs one task at a time with the solo pool, task time limits are not enforced, and the scheduler lives in the same process, so one hung task stops every background job. I measured it: a task with a two-second limit ran its full eight. The second weakness is detection. There are no alerts or metrics, and the September outage, where a leftover Procfile made the worker run the web server, was noticed by looking.

Facts: [Distributed-systems review](/reliability/distributed-systems/) and [Background work](/platform/background-work/). The 2-second-limit test was captured on macOS with Celery 5.6.2; under the prefork pool the same task stopped at 2 seconds.

### 8. How it scales: 10x, then 100x (1:15)

> Today is about 98 items and an estimated thousand passages. Ten times is a thousand items and about ten thousand passages; a hundred times is ten thousand items and a hundred thousand passages, about 1.4 GB of vectors and index together. Nothing in the data tier needs to change for the first, and the exact search lane stays well under a second even if one user held all of it. What changes first is the worker tier: split the queue into interactive and bulk, move I/O tasks to a pool that enforces limits, run PDF extraction on its own worker, and give beat its own service. Next is connections: five per process adds up, so add PgBouncer when the sum approaches the Postgres limit. Then a read replica for search, with a rule that a user reads from the primary briefly after a write. For vectors, ADR 0001 sets the trigger at five million vectors or 200 milliseconds p95; a hundred times today is around two percent of that. If one user gets very large, route that user to the index with iterative scan on, or prefilter by article.

| Step | When (status of the trigger) | What changes |
|---|---|---|
| Fix the worker tier | Before 10x; the limit is not enforced today (measured) | Interactive and bulk queues; pool that enforces time limits; PDF worker; beat as its own service; per-task database session first |
| Separate the broker from the cache | Before 10x; one Redis serves queue, caches, limits, locks (code) | Second Redis or a no-eviction policy for the queue; eviction policy not verified |
| Add PgBouncer | When processes x 5 nears `max_connections` (dev 100; production not checked) | Transaction pooling in front of Postgres; estimate: each added process adds up to 5 connections, so the total grows with replicas, not with load |
| Read replica for search | When search CPU competes with writes (not measured) | Route search only; keep a user on the primary briefly after a write |
| Per-user cost of exact search | 30 microseconds per vector (measured); a user with 10,000 passages is about 0.3 s (estimate) | Above a few seconds: HNSW with iterative scan, or prefilter by article, for that user |
| Partition by user | About 10 million chunks (plan in the distributed-systems chapter) | `user_id` first in composite indexes; hash-partition `content_chunks` and `highlights` |
| Dedicated vector store | ADR 0001 triggers: 5M vectors, 200 ms p95, multi-vector scale, 100 writes a second | Qdrant self-hosted was the named candidate; or shard users across clusters |

{% fig "Vector size arithmetic (illustrative scale-up of measured values)" %}
```text
measured: one stored vector 6,148 bytes; HNSW index about 1.33x vectors
          (573 chunks: 4,677,632 bytes of index)
 100,000 chunks ->  615 MB vectors +  about 820 MB index
 100,000,000    ->  615 GB vectors +  about 820 GB index   (estimate)
```
{% endfig %}

The filtered-search fact belongs here. On 20,000 random vectors split across four users, an HNSW query with a user filter returned 1 row instead of 10, because the index returns its 40 nearest candidates across all users and the filter then discards most of them. pgvector's iterative scan (`hnsw.iterative_scan`, off by default and not set anywhere in the code) returned all 10. That is the risk that appears when many users share one big index; the single-user development data cannot show it ([RAG from first principles](/retrieval/rag-from-first-principles/)).

### 9. What is weak today, ranked (0:45)

> I keep a ranked register and re-checked each item against the code. First, the worker will fetch any address a user saves, because the one download function never checks where it points: that is server-side request forgery, and the fix is half a day in one function. Second, two settings default to unsafe values: debug is on by default and there is no check on the signing key length. Third, I have not verified that backups exist or that a restore works. Fourth, a worker killed mid-task leaves an item processing forever, and a completed item whose embedding was lost is never retried. Fifth, the dashboard fetches one page of fifty and never asks for more, so a large library looks truncated. Sixth, the single worker slot. And behind all of them, nothing alerts.

The order comes from the rule in [Gaps and roadmap](/reliability/gaps-and-roadmap/): user impact times likelihood, divided by effort, with security and data loss first. One item found in [Auth and security](/platform/auth-and-security/) is not in that register: the public profile route returns the owner's email address (the fix is a separate response schema).

### 10. What to do next (0:30)

> Week one: the one-hour fixes, which are safe defaults, catching the duplicate-save error, a readiness endpoint and keeping processing status during retries, plus checking backups and the Redis eviction setting in the dashboards. Weeks two and three: the address check in the fetch function, a sweep for processing items, and an item-level embedding sweep. Then paging the dashboard. In month two, the worker tier as one piece of work. In month three, metrics and alerts, starting with one number: the age of the oldest item that is pending or processing. After that the vector benchmark and a chunk-size sweep, so scaling decisions come from measurements.

## How it is built here

The talk has no code of its own. This table says which chapter holds the evidence for each section, so an answer can be traced.

| Section | Evidence |
|---|---|
| 1 Requirements and scale | [Memory](/reliability/memory/) (production baseline), [Deploy and operations](/platform/deploy-and-operations/) (98 items) |
| 2 Architecture | [The system on one page](/orientation/system-on-one-page/) |
| 3 Write path | [Saving a URL](/ingestion/saving-a-url/), [Extracting an article](/ingestion/extracting-an-article/), [Distributed-systems review](/reliability/distributed-systems/) |
| 4 Read path | [Hybrid search](/retrieval/hybrid-search/), [RAG from first principles](/retrieval/rag-from-first-principles/), [Measuring retrieval](/retrieval/measuring-retrieval/) |
| 5 Data model | [Data model and Postgres](/platform/data-model/) |
| 6 LLM layer | [The LLM gateway](/llm/gateway/), [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/) |
| 7 Reliability | [Background work](/platform/background-work/), [Distributed-systems review](/reliability/distributed-systems/) |
| 8 Scale | [RAG from first principles](/retrieval/rag-from-first-principles/) (index measurements), [Decision log](/talking/decision-log/) (ADR 0001 triggers) |
| 9 and 10 Weak and next | [Gaps and roadmap](/reliability/gaps-and-roadmap/) |

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Start from requirements and numbers | Start from the technology list | Numbers let the listener size the problem; a list only shows vocabulary. |
| Follow two paths (save, search) | Walk component by component | A path shows how components interact and where each one fails. |
| One failure story per path | A list of failure modes | A story is remembered and shows the incident, the diagnosis and the change. |
| Say the weaknesses before being asked | Wait for the question | It shows the register exists, and it takes the most dangerous question off the table. |
| Quote numbers only with their label | Round numbers without a source | A reviewer who catches one unlabelled estimate stops trusting the others. |
| Say "effectively once", not "exactly once" | Claim exactly-once | The claim cannot hold across a database, a broker and an outside API. |
| Ten minutes, ten sections | A 30-minute deep dive | The long version is the rest of this guide; the short one forces a ranking of what matters. |

## Questions you will be asked

Fifteen questions with model answers, each built from facts in the linked chapters.

{% qa "1. Is anything here exactly-once?" %}
No, and no design with a database, a broker and a third-party API can promise it. The documented Celery default is early acknowledgment, so a worker killed mid-task loses that message. On this stack the message stayed in Redis's unacked set after a kill and was not redelivered within 12 seconds; redelivery after the one-hour visibility timeout is read from the source, not tested ([Background work](/platform/background-work/)). What the system claims is effectively-once effects: the row is the truth and the message a hint; sweeps re-dispatch pending items, missing chunks and missing highlight embeddings; tasks are idempotent (chunks are deleted and replaced in one transaction, a unique index stops duplicate saves, token rotation is one conditional update). Duplicate paid calls are accepted and bounded by the per-user budget. The holes are items stuck in processing and completed items with no embedding.
{% endqa %}

{% qa "2. What happens if Redis dies?" %}
Saves still work: the row commits at pending and the queue send fails, and the sweep re-dispatches it once Redis returns. Queued messages are lost and no task runs while the broker is down. Rate limits, the LLM budget check and sweep locks fail open. The query-embedding cache falls back to calling OpenAI, so search still works. MCP sign-in keeps its codes and refresh tokens in Redis, so it would fail (inferred from the key table). Items mid-extraction stay in processing with no sweep. Redis persistence and eviction on Railway were not verified. The structural weakness is that queue, caches, locks and limits share one Redis ([Gaps and roadmap](/reliability/gaps-and-roadmap/), item 19).
{% endqa %}

{% qa "3. What happens if OpenAI is down for an hour?" %}
Saving and reading are unaffected, because extraction does not call OpenAI. Embedding tasks retry at 60, 120 and 240 seconds (about 7 minutes) and then stop. There is no item-level sweep, so an item that exhausted its retries stays without an embedding, searchable by keyword only, until that sweep exists; highlight embeddings and missing chunks do recover. Tagging and analysis use the structured path, which has no fallback, and a rate-limit error is wrapped in an exception the task does not retry on, so those tasks end as failed ([Tagging and article analysis](/llm/tagging-and-analysis/)). Plain chat calls fall back to Bedrock if it is configured; production credentials were not verified. Search degrades silently to keyword results. Embeddings have no fallback on purpose.
{% endqa %}

{% qa "4. How would you scale search to 100M vectors?" %}
Arithmetic first: 100 million vectors at 6,148 bytes is about 615 GB, and the index is about 1.33 times that, roughly 820 GB (estimate scaled from measured sizes). One index that size will not fit in a normal node's memory, and the scratch build of 20,000 vectors already took 127.7 seconds. Queries are per user: at about 10,000 passages per user that is about 10,000 users, and the exact lane costs about 30 microseconds a vector, so about 0.3 seconds for a user that size (estimate). Hash-partition the chunk table by user so each index is small and a filtered query touches one partition, and set iterative scan so a filter cannot return too few rows. Beyond one node, shard users across clusters or use a dedicated vector store, which ADR 0001 names. Quantization is untested here. Recall at that size is not measured, so benchmark first.
{% endqa %}

{% qa "5. How is rate limiting designed?" %}
A sliding-window log in Redis: each request adds a timestamped member to a sorted set, with a per-minute and a per-hour window per route and a Retry-After on rejection. It covers four routes: saving a URL (10 per minute, 50 per hour), login (10 and 30), register (5 and 15), semantic search (30 and 300). It fails open. Three flaws: the key is the client address, not the user, because nothing sets `request.state.user`, so behind a proxy many users may share a bucket; check and add are two round trips, so concurrent requests can overshoot; and password-reset routes are not limited. The fix is to key by user id after authentication and make check-and-add one Lua script. The per-user daily LLM spend cap is a separate control ([Auth and security](/platform/auth-and-security/)).
{% endqa %}

{% qa "6. How is tenant isolation enforced?" %}
By convention in application code: every query filters by the caller's user id, and tests check that another user's item returns 404. The text-to-SQL tool adds a parser check that requires a user predicate on every user-scoped table, and LLM spend is capped per user. There is no row-level security, so a query that forgets the filter would leak and the database would not object. `tag_embeddings` has no owner, the query-embedding cache is keyed by text only, and soft delete is ignored on three paths. The fix is row-level security keyed on a per-request setting, estimated at two days ([Data model and Postgres](/platform/data-model/)).
{% endqa %}

{% qa "7. How do you do zero-downtime migrations?" %}
Railway runs the migration once per deploy in a pre-deploy command, before the new API container serves traffic; a failure stops the deploy. The API keeps the default overlap, so old code runs against the new schema for a while: migrations must be additive, expanded first and contracted in a later release, rolling forward rather than back. Vector indexes are built by a script with `CREATE INDEX CONCURRENTLY` because that cannot run in a transaction. CI fails on more than one migration head. The honest example is the URL-normalization migration: it blocks writes (not reads) for a few seconds, is irreversible, and on production scanned 98 rows and rewrote 21 (reported). There is no canary, and destructive changes need a tested backup, which was not verified ([Deploy and operations](/platform/deploy-and-operations/)).
{% endqa %}

{% qa "8. Why Celery and not Kafka or Temporal?" %}
The work is a few task types per saved item, with the database row as the state and sweeps as the repair, and Redis was already needed. Celery gives retries, schedules and one worker process. Kafka is a durable log for several independent consumers at high throughput; there is one consumer of the save event, so a real outbox table makes sense only when a second appears. ADR 0005 reserved Temporal for the research agent, but the agent runs as a Celery chord with its state in a database row and a five-minute sweep that marks stalled runs partial; no Temporal code exists. I would revisit past about ten steps or long waits. The cost is subtle delivery semantics and the single solo worker ([Decision log](/talking/decision-log/)).
{% endqa %}

{% qa "9. Why pgvector and not a vector database?" %}
Vectors live beside the rows they describe, so a search filters by user and deleted state and joins metadata in one query, with no dual write and no second system. Keyword and vector fusion is one query, and it costs nothing extra. The measured fit is good: the exact lane takes about 5 ms over 557 chunks. The costs: vector search shares CPU and memory with everything else, every query needs a user filter, and the HNSW indexes serve no production query shape on the development data while adding insert cost. ADR 0001 sets the triggers to move: 5 million vectors, 200 ms p95, multi-vector search at scale, or 100 writes per second.
{% endqa %}

{% qa "10. Why chunk articles at all?" %}
One vector per article blurs everything into an average; the embedding model accepts at most 8,192 tokens, so a long article cannot be embedded whole; and connections and the research agent need the matching passage. Chunks target about 350 tokens (counted as 466 words), split at headings then sentences, with 40 words of overlap and a title prefix added before embedding. Passage-level search scored 0.7652 recall at 10 against 0.7459 for article-level, but the interval on the difference includes zero. The sizes were never tuned, and counting in words can leave a chunk 50 percent over its token target ([Chunking and embedding](/retrieval/chunking-and-embedding/)).
{% endqa %}

{% qa "11. How do you know search got better?" %}
A labelled set of 45 queries over one library is scored with recall at 10, MRR and NDCG across four variants. It changed what shipped: the entity lane is off because it scored below passages alone, and a paid reranker was removed after its own evaluation said not to ship it. The limits: every pairwise difference has a 95 percent interval that includes zero, 28 to 34 of 45 queries do not change between variants, the queries come from one library and were also used to tune, and there are no real-user queries. The retrieval regression gate does not compare anything yet, because the results are not committed and the baselines are keyed differently from what the script reads. Clicks are logged but not stored. Next: a larger held-out set, a working gate, click data ([Measuring retrieval](/retrieval/measuring-retrieval/)).
{% endqa %}

{% qa "12. How do you control LLM cost?" %}
At one choke point. Every call goes through the gateway: a per-user daily ceiling of $5 in Redis, checked before a call and recorded after (so it can be overshot by about one call, and it fails open). Models are chosen per task: gpt-4o-mini costs $0.15 per million input tokens against $2.50 for gpt-4o. Query embeddings are cached for an hour, the keyword route skips the embedding call, analysis reads only the first 2,500 words, and a test fails the build if a call site omits the user id. The rate limit on saving also bounds ingestion spend. Gaps: an unknown model name prices at zero (a dated name from the real API may be undercounted, unverified), retries can multiply calls up to twelve, and no account-wide ceiling exists in the code (inferred) ([The LLM gateway](/llm/gateway/)).
{% endqa %}

{% qa "13. What would you monitor?" %}
Start with ingestion freshness: the age of the oldest item that is pending or processing. It measures user pain and catches a dead broker, a dead worker and the stuck states. On the development database it returns 4 pending items, the oldest 284 days, because the local worker rarely runs. Then queue length in Redis (the 6.5-million-message backlog was found by accident), worker and beat liveness, error rate and p95 per route, LLM spend per user and provider failure rate, pool timeouts and connection count, and memory trend, plus a readiness endpoint that checks Postgres and Redis. Proposed targets, not measurements: 99 percent of saves complete within 2 minutes, 99.9 percent within 15, API availability 99.5 percent over 30 days ([Distributed-systems review](/reliability/distributed-systems/)).
{% endqa %}

{% qa "14. What is the biggest risk?" %}
Two, ranked by cost. First, backups are unverified and no restore has been timed, so a bad migration or deletion could be unrecoverable; everything else on the list is recoverable if a restore works. Fix: enable a policy, restore once, record the time (about 2 hours). Second, server-side request forgery: the worker fetches any address a user saves and the one download function never checks it. Fix: validate scheme and address on every redirect hop in that function (about half a day). Behind both, nothing alerts, so a problem is found by a user ([Gaps and roadmap](/reliability/gaps-and-roadmap/)).
{% endqa %}

{% qa "15. How was the memory problem found and fixed?" %}
Railway bills for memory, so I measured with a harness that runs old and new commits as production does, over HTTP, on the same data. The first guess, that importing torch made the API large, was refuted (the API is 164 MB after import). The big costs were ordinary: one save loaded every row of the user's library with three large columns, 1,233 MB at 2,000 items (a first SQLite pass understated it 3.8 times); and a 20 MB page produced 7,634 chunks embedded in one call, peaking at 1,878 MB. Fixes: defer the heavy columns; normalize stored URLs so the duplicate check is one indexed lookup (1,233 MB to about 2 MB); cap HTML at 10 MB; embed 100 chunks at a time; page lists. Production then looked nearly idle, so the larger saving was stopping the unused Prefect service (222 MB) and removing worker deploy overlap (peak 840 MB to a reported 402 MB). Production was not re-measured afterward ([Memory](/reliability/memory/)).
{% endqa %}

## How it fails

The talk can go wrong in predictable ways.

| Failure | What it looks like | What to do instead |
|---|---|---|
| Component tour | Ten minutes of names with no reasons | Follow the two paths and tell the failure stories |
| Unlabelled numbers | "It handles thousands of users" | Quote 98 items as reported and the arithmetic as an estimate |
| Claiming exactly-once | A reviewer asks what happens when a worker dies | Say effectively-once and name the two uncovered states |
| Per-user rate limit | The limiter keys by address | Say per address, and name the fix |
| Prefect or Temporal in production | Repeating the 2026-05 ADR text | Say Celery chain; Prefect is off and stopped; Temporal was never built |
| Defending instead of ranking | A listener finds a gap you did not mention | State the ranked register before the questions |
| Reading production facts as verified | "Backups are enabled" | Say not verified; the dashboards were not checked |
| Overrunning time | Section 4 takes five minutes | Cut facts, never the failure story |

## What is weak or missing

Ranked by how likely each is to hurt the talk.

1. **Production figures are quoted, not re-checked.** The 98 items, the memory p50s, the stopped Prefect service and the zero overlap come from audits and the owner's notes, not from a live look. The first sentence of any production number should say so.
2. **The scale section is arithmetic, not a load test.** The 10x and 100x rows scale measured per-vector costs and sizes linearly. Nothing was run above a 20,000-vector scratch table, and the number of production users is unknown.
3. **Not verified, so say so:** production Postgres connection limit, Redis persistence and eviction, whether backups run, whether Bedrock credentials exist in production (which decides whether chat fallback works), and whether `DEBUG` is false.
4. **The script was not timed aloud.** The 130 words per minute is an assumed pace. Rehearse with a timer and cut from the fact tables.
5. **Chapters disagree on one point.** The distributed-systems chapter lists provider rate limits as fixed by re-raising transient errors; the tagging chapter shows that for the structured path the error is wrapped and the retry does not fire. Answer 3 follows the code-checked chapter (3.2). It is recorded in the discrepancy notes for this chapter.
6. **The register omits one item.** The public-profile email exposure is in the auth chapter but not in the ranked register.

{% say %}
sed.i is a read-it-later app with search and an AI layer. In production it is small: about 98 active items, four services using about a gigabyte between them. A Next.js frontend and a FastAPI backend and a Celery worker with a scheduler, all sharing Postgres with pgvector and Redis. Saving a URL writes a pending row and queues a job; a sweep recovers jobs lost between the commit and the queue. Search classifies the query with rules, runs keyword and exact vector lanes, and fuses them by rank; the vector index exists but the main lane scans exactly, which is right at this size. All model calls go through one gateway with a per-user daily budget. What breaks first is the single worker with unenforced time limits, and nothing alerts. Scaling is: fix the worker tier, add a connection pooler, partition vectors by user, and move to a dedicated store at about five million vectors. Weak points, ranked: unchecked fetch addresses, unsafe defaults, unverified backups, stuck items.
{% endsay %}

## Check yourself

{% qa "Which scale numbers would you quote for today, and what is each one's status?" %}
About 98 active items, reported from the migration log; 1.1 GB combined memory and under 0.02 vCPU, measured by Railway metrics at 5-minute samples; about 1,000 chunks, an estimate from 98 items times 10.4 chunks per article measured on the development library. Anything above that, such as 10x and 100x rows, is arithmetic and is labelled estimate.
{% endqa %}

{% qa "Why does the write-path failure story end with what the fix does not cover?" %}
Because the first question is usually what else can be lost. The pending sweep covers one stuck state. An item stuck in processing after a worker is killed, and a completed item whose embedding was lost, are not covered. Naming them first shows the register is real.
{% endqa %}

{% qa "A listener says: so you have an HNSW index and your search is approximate. How do you reply?" %}
The index exists but the main search lane does not use it. That lane aggregates the best chunk per article over all of a user's chunks, which an HNSW index cannot serve, so it computes exact distances (5 ms over 557 chunks, measured). The index serves only nearest-k queries, and on the development data the planner chose it for none of them. It becomes relevant at scale, with the per-user filter caveat.
{% endqa %}

{% qa "Why is 100 million vectors less alarming when stated per user?" %}
Search is always for one user. At about 10,000 passages per user the exact lane costs roughly 0.3 seconds (estimate from 30 microseconds a vector), regardless of the total. The hard parts are the table and index size, build time, and filtered approximate search, which partitioning by user addresses.
{% endqa %}

{% qa "In which order would you present the weaknesses, and by what rule?" %}
By user impact times likelihood divided by effort, with security and data loss first: the unchecked fetch address, the unsafe defaults, unverified backups, then stuck items, the 50-item dashboard, the single worker, and the absence of alerts. Each has a fix of an hour to a day, and that is worth saying.
{% endqa %}
