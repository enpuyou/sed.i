---
layout: layouts/chapter.njk
title: Glossary
part: 0
order: 3
anatomy: custom
summary: Every term the guide uses, defined in one to three sentences and checked against the code.
verified: 2026-10-25
permalink: /orientation/glossary/
---

This glossary covers the product, retrieval, language-model, infrastructure and reliability vocabulary of sed.i. It started from `CONTEXT.md` and every definition was checked against the code; where the two disagree the entry follows the code, and the difference is listed in this chapter's discrepancy file. Where a general term names something sed.i does not have (a circuit breaker, for example), the entry says so, because the absence is part of what you should be able to explain. Terms are alphabetical. A link at the end of an entry points to the chapter that explains the idea.

{% say %}
This is the dictionary for the whole guide. If a word in a chapter is not defined where it first appears, it is here. The entries are short on purpose: a definition, where it lives in sed.i, and a link to the chapter that explains it. For the reliability words, several entries tell you that sed.i does not have the thing yet, such as a circuit breaker or an SLO, so that I can say so accurately instead of claiming the pattern.
{% endsay %}

### A

**Access token.** A signed JWT that proves who is calling the API. It is valid for 1,440 minutes (24 hours, `ACCESS_TOKEN_EXPIRE_MINUTES`), is stored in an httpOnly cookie for the web app, and is sent as a Bearer header by the extension and MCP clients. See [Auth and security](/platform/auth-and-security/).

**acks (acknowledgements).** A Celery worker tells the broker it has received a message so the broker can stop holding it. Celery's default is to acknowledge before the task runs; the code sets no `acks_late` anywhere (searched `app/`), so a worker killed mid-task can lose that message, and sweeps are the recovery. Inferred from the code and Celery's documented default. See [Background work](/platform/background-work/).

**Alembic.** The migration tool for SQLAlchemy. Migrations live in `content-queue-backend/alembic/versions/` and are applied with `alembic upgrade heads`. See migration.

**APIError.** The error class the frontend throws for any non-2xx response. It carries `status`, `detail` (the backend's `{ detail: string }` message) and `body` (the parsed response for structured payloads such as a duplicate-save 409). See [The frontend](/platform/frontend/).

**Auto-tags.** A column (`content_items.auto_tags`) meant for model-suggested tags that the user accepts into `tags` through `POST /content/{id}/tags/accept`. No task writes it any more: the tagging and analysis tasks write straight to `tags` (inferred from a search of `app/`), so the accept flow only acts on old data. See [Tagging and article analysis](/llm/tagging-and-analysis/).

### B

**Backpressure.** Slowing or refusing new work when the consumer cannot keep up, so a queue does not grow without bound. sed.i has none for the job queue: the API accepts saves at its rate limit regardless of how many tasks are waiting, and nothing alerts on queue length (gap G-09 in the distributed-systems review). Not the same as rate limiting, which protects against one caller. See [Distributed-systems review](/reliability/distributed-systems/).

**Beat.** Celery's scheduler: it puts a task on the queue at fixed intervals. In sed.i it runs inside the worker process (`--beat`), so a stuck worker also stops the schedule. The schedule is in `content-queue-backend/app/core/celery_app.py`. See [Background work](/platform/background-work/).

**Bedrock.** Amazon's hosted model service. It can replace OpenAI for chat (`LLM_PROVIDER`) and for embeddings (`EMBED_PROVIDER`); production uses OpenAI. The AWS resources are defined with Pulumi in `infra/`. See [The LLM gateway](/llm/gateway/).

**Braintrust.** A tracing and evaluation service. Every language-model call is wrapped in a Braintrust span carrying the user id, which gives cost per user and per task. See span and [The LLM gateway](/llm/gateway/).

**Broker.** The store where task messages wait for a worker. In sed.i, Redis. See [Background work](/platform/background-work/).

**Budget.** A per-user daily ceiling on language-model spend, default 5.0 dollars (`LLM_DAILY_BUDGET_USD_PER_USER`). The running total lives in Redis under `llm_spend:{user_id}:{date}`; a call over the ceiling raises `BudgetExceededError`, and tasks treat it as final rather than retrying. If Redis is down the check is skipped. See [The LLM gateway](/llm/gateway/).

### C

**Celery.** The Python library that defines background tasks, sends them through a broker, and runs them in workers. Tasks are in `content-queue-backend/app/tasks/`. See [Background work](/platform/background-work/).

**Chunk.** One passage of an article, about 350 tokens, stored as a row of `content_chunks` with its own embedding. Created by `generate_chunk_embeddings_task` (not `chunk_and_embed`, as `CONTEXT.md` says). See [Chunking and embedding](/retrieval/chunking-and-embedding/).

**Circuit breaker.** A guard that stops calling a failing dependency for a while instead of retrying every request. sed.i has none: a failing provider is retried (one retry, then the other provider, then the task's own retries) without jitter or a breaker. See [The LLM gateway](/llm/gateway/).

**Cloudflare Worker.** A small script at Cloudflare's edge. Here it answers browser cross-origin preflight requests, strips the `Origin` header, and forwards API calls to Railway (`cloudflare-worker/worker.js`). See [The system on one page](/orientation/system-on-one-page/).

**Connection.** A link between two highlights in different articles, found by comparing highlight embeddings with cosine similarity (threshold 0.3 in `core/config.py`, so the deployed value may differ). Computed on request and never stored. See [Entity graph and connections](/retrieval/entity-graph-and-connections/).

**ContentItem.** One saved link and everything derived from it, stored in `content_items` (`content-queue-backend/app/models/content.py`). Its `processing_status` runs `pending`, `processing`, `completed` or `failed`. See [Saving a URL](/ingestion/saving-a-url/).

**Contextual prefix.** The sentence naming the article and the passage's position that is placed before a chunk when its embedding is computed, but not stored. See [Chunking and embedding](/retrieval/chunking-and-embedding/).

**Cosine similarity.** The cosine of the angle between two vectors: 1 for the same direction, 0 for unrelated. pgvector's `<=>` operator returns cosine distance, so similarity is `1 - distance`. See [RAG from first principles](/retrieval/rag-from-first-principles/).

**Crate.** The user-facing name for the vinyl-record collection (`/crates`), fed by Discogs and stored in `vinyl_records`. Unrelated to reading; gated by the `SHOW_CRATES` flag, which defaults to on.

**CSRF (cross-site request forgery).** An attack in which another site makes a logged-in browser send a state-changing request. sed.i uses a double-submit token: a readable cookie and a matching header on every cookie-authenticated POST, PUT, PATCH and DELETE (`app/middleware/csrf.py`). Bearer-token clients are exempt. See [Auth and security](/platform/auth-and-security/).

### D

**DatabaseTask.** The base class for most Celery tasks (`app/tasks/base.py`). It gives each task a database session and closes it when the task returns.

**Draft.** The one markdown document attached to a list, stored in `drafts`. The database allows one per list per user. See [The frontend](/platform/frontend/).

### E

**Embedding.** A list of 1,536 numbers that represents the meaning of a text, produced by OpenAI's `text-embedding-3-small`. Stored on articles, chunks, highlights, entities and tag labels. Embeddings always use `EMBED_PROVIDER` with no fallback, because vectors from different models cannot be compared. See [RAG from first principles](/retrieval/rag-from-first-principles/).

**Entity.** A named thing extracted from an article by a language model: a person, concept, organization, paper or tool. Unique per user by lowercased name, with an embedding. See [Entity graph and connections](/retrieval/entity-graph-and-connections/).

**Entity graph.** The `entities`, `entity_mentions` and `entity_relations` tables: which entities appear in which articles and how they relate. An experimental search lane uses it; the lane is off by default (`ENTITY_SEARCH_ENABLED`) because an evaluation found it worse than chunks alone. See [Entity graph and connections](/retrieval/entity-graph-and-connections/).

**Ephemeral reader.** The `/read` page: reads an article extracted by the browser extension without saving it; saving it later carries over any highlights made. See [The browser extension](/ingestion/browser-extension/).

### F

**Feature flag.** A `NEXT_PUBLIC_*` environment variable that shows or hides unfinished UI, read in `frontend/lib/flags.ts`. There are six flags; three default on and three default off. See [The frontend](/platform/frontend/).

**fetchWithAuth.** The single function through which the frontend calls the API. It adds credentials and the CSRF header, refreshes the session once on a 401, and converts errors to `APIError`. See [The frontend](/platform/frontend/).

**Filter lane.** The search path for operator queries such as `author:` or `tag:` and for known authors and site names. It is a SQL filter with no embedding. See [Hybrid search](/retrieval/hybrid-search/).

### H

**Head-of-line blocking.** A slow item at the front of a first-in-first-out queue delaying everything behind it. sed.i has one queue with no routing (`celery_app.py` defines no `task_routes`), so a 300-second PDF extraction delays a user's save behind it. See [Distributed-systems review](/reliability/distributed-systems/).

**Highlight.** A text selection a user saved in an article, with character offsets, a color, an optional note and an embedding. Highlights shorter than 20 characters are left out of connection search. See [Entity graph and connections](/retrieval/entity-graph-and-connections/).

**HNSW (hierarchical navigable small world).** An index for approximate nearest-neighbor search over vectors. sed.i creates HNSW indexes (`m=16`, `ef_construction=64`) on four vector columns. The main chunk search query does not use one; it compares exactly. See [Hybrid search](/retrieval/hybrid-search/).

**Hybrid search.** Search that combines keyword and meaning-based retrieval. `GET /search/semantic` classifies each query and runs one method, or runs several and merges them with RRF. See [Hybrid search](/retrieval/hybrid-search/).

### I

**Idempotent.** Safe to run twice with the same result. Chunking replaces an article's chunks in one transaction; clustering replaces a user's clusters; article analysis upserts. Idempotency is what makes retries and sweeps safe. See [Background work](/platform/background-work/).

**Ingestion.** The path from a submitted URL to a finished `ContentItem`. The normal path goes through a queued extraction task; the extension path sends already-extracted HTML and skips the fetch. See [Saving a URL](/ingestion/saving-a-url/).

**InlineError.** The shared React component that shows an error next to the action that failed. The project rule is to use it rather than toast notifications. See [The frontend](/platform/frontend/).

### J

**JWT (JSON Web Token).** A signed string that carries a user id and an expiry. The server verifies the signature instead of looking the session up. See [Auth and security](/platform/auth-and-security/).

### L

**Lane.** One retrieval method inside full-mode search: filter, keyword, semantic, or entity. See [Hybrid search](/retrieval/hybrid-search/).

**Library.** All of a user's `ContentItem` rows that are not soft-deleted, read or not. The queue is its unread subset.

**List.** A named collection of articles (`lists`, with the join table `content_list_membership`), optionally shared or public, and the home of one draft. See [Data model and Postgres](/platform/data-model/).

**Lock (task lock).** A Redis key set with `SET NX EX` so only one firing of a sweep runs at a time (`app/core/task_locks.py`, key `task_lock:{name}`). If Redis is unreachable the lock is skipped and the task runs. See [Background work](/platform/background-work/).

### M

**MCP (Model Context Protocol).** A standard for letting an AI assistant call tools in another program. sed.i's MCP server is mounted in the API process and exposes 16 tools (`app/mcp/http_server.py`), not the 13 `CONTEXT.md` states. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/).

**Memory consolidation.** A nightly task that summarizes a user's recent reading activity into a profile row (`user_profiles`), used as context by later features. See [Memory consolidation](/llm/memory-consolidation/).

**Migration.** A versioned script that changes the database schema. Railway runs `alembic upgrade heads` in its pre-deploy step, before the new version serves traffic. See [Data model and Postgres](/platform/data-model/).

**MRR (mean reciprocal rank).** For each query, 1 divided by the rank of the first relevant result, averaged over queries. See [Measuring retrieval](/retrieval/measuring-retrieval/).

### N

**NDCG (normalized discounted cumulative gain).** A ranking score that rewards putting relevant results near the top, discounting each by the log of its rank and dividing by the best possible ordering. sed.i's version uses binary relevance. See [Measuring retrieval](/retrieval/measuring-retrieval/).

**Nixpacks.** The build tool Railway uses to turn the repository into a container image, configured by `nixpacks.toml`. See [Deploy and operations](/platform/deploy-and-operations/).

### O

**OAuth 2.1 with PKCE.** The sign-in flow MCP clients use. PKCE (proof key for code exchange) has the client send a hash of a secret first and prove it holds the secret when exchanging the code, so an intercepted code is useless. sed.i supports the S256 method. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/).

**OpenTelemetry (OTEL).** A standard for traces. sed.i instruments FastAPI, SQLAlchemy and Celery and exports to an OTLP endpoint when one is configured, or prints to the console otherwise. See [Deploy and operations](/platform/deploy-and-operations/).

**Outbox.** A pattern that writes an event to the database in the same transaction as the change, and a separate process publishes it. sed.i has no outbox table. A `pending` row acts as an implicit one: `recover_stale_pending_items` re-publishes rows that never reached the queue. See [Distributed-systems review](/reliability/distributed-systems/).

### P

**pgvector.** The Postgres extension that adds the `vector` column type, distance operators and HNSW indexes. See [Data model and Postgres](/platform/data-model/).

**PostHog.** A product analytics service. The backend records events such as sign-ups and logins; the frontend identifies the user. It turns itself off when `POSTHOG_API_KEY` is empty.

**Prefect.** A workflow orchestration tool. sed.i has an optional Prefect version of the ingestion pipeline (`app/workflows/ingestion.py`); in production the Prefect service is stopped and `PREFECT_ENABLED` is false. See [The system on one page](/orientation/system-on-one-page/).

**Processing.** The phase after ingestion when workers fill in an item's metadata, text, embedding, tags and chunks. The item's status is `processing` for the first part of it. See [Extracting an article](/ingestion/extracting-an-article/).

**Provider fallback.** When a chat call fails, the gateway retries once on the same provider for transient errors (rate limit, timeout, connection) and then tries the other provider. Embedding calls never fall back. See [The LLM gateway](/llm/gateway/).

**Public profile.** The `/[username]` page that shows a user's queue, lists or crates read-only if they chose to make it public.

### Q

**Query classifier.** The rule-based function `classify_query` (`app/core/search_router.py`) that picks filter, keyword, semantic or hybrid for a query with regular expressions and no model call. It took 1.8 microseconds per call in a local measurement. See [Hybrid search](/retrieval/hybrid-search/).

**Queue.** The user's unread, non-archived items. Not a table; a filtered view. Not to be confused with the Celery queue in Redis. See [What sed.i is](/orientation/what-is-sedi/).

### R

**RAG (retrieval-augmented generation).** Answering by first retrieving relevant text and then giving it to a language model as context. sed.i's research agent and MCP tools follow this pattern. See [RAG from first principles](/retrieval/rag-from-first-principles/).

**Railway.** The platform that runs the API, worker, Postgres and Redis. See [Deploy and operations](/platform/deploy-and-operations/).

**Rate limit.** A cap on requests per caller per window. Four routes are limited with sliding windows stored as Redis sorted sets; exceeding a limit returns 429 with `Retry-After`. If Redis is down the limiter lets requests through. See [Auth and security](/platform/auth-and-security/).

**Reader.** The `/content/[id]` page: `Reader.tsx` is the chrome and panels, `ReaderArticle.tsx` is the article body. See [The frontend](/platform/frontend/).

**Reading status.** A value computed, not stored: `archived` if archived, `read` if marked read or scrolled to 90 percent, `in_progress` if scrolled at all, otherwise `unread`. See [Data model and Postgres](/platform/data-model/).

**Reading theme.** A cluster of related tags (`reading_clusters`), built weekly from tag embeddings with union-find when a user has at least 10 tagged articles. Its page is behind a flag that defaults to off.

**recall@k.** The fraction of the relevant results that appear in the top k. See [Measuring retrieval](/retrieval/measuring-retrieval/).

**Redis.** The in-memory store used as Celery's broker and result backend, and for query-embedding caching, rate limits, spend counters, sweep locks and short-lived OAuth authorization codes. See [Background work](/platform/background-work/).

**Reranker.** A second model that reorders the top search results by reading each against the query. sed.i evaluated Cohere Rerank, found no case to ship it, and removed it on 2026-09-25; no reranker is in the search path. See [Measuring retrieval](/retrieval/measuring-retrieval/).

**Research run.** One execution of the multi-agent research pipeline (`research_runs`): a planner splits a question, sub-agents retrieve in parallel, and a synthesis step writes a brief. See [The research agent](/llm/research-agent/).

**RRF (reciprocal rank fusion).** A way to merge ranked lists: each item scores `1 / (60 + rank)` in each list, and scores are summed. It needs no common score scale. See [Hybrid search](/retrieval/hybrid-search/).

### S

**Sentry.** An error-tracking service used by both backend and frontend; off when `SENTRY_DSN` is empty. See [Deploy and operations](/platform/deploy-and-operations/).

**SLO (service level objective).** A target for reliability, such as "99 percent of saves finish in 60 seconds". sed.i defines none, and has no metrics to measure one; candidates are proposed in the distributed-systems review. See [Gaps and roadmap](/reliability/gaps-and-roadmap/).

**Soft delete.** Marking a row deleted with a `deleted_at` timestamp instead of removing it. Queries filter on `deleted_at IS NULL`. A daily beat task hard-deletes items soft-deleted more than 7 days earlier (`app/tasks/cleanup.py`), so rows are not kept forever, contrary to `CONTEXT.md`. See [Data model and Postgres](/platform/data-model/).

**Solo pool.** Celery's execution mode that runs tasks one at a time in the main process. The production worker uses it. Task time limits are not enforced under it. See [Background work](/platform/background-work/).

**Span.** One timed unit of work inside a trace, with inputs and metadata. sed.i uses OpenTelemetry spans for requests, queries and tasks, and `braintrust_span` for model calls. See [The LLM gateway](/llm/gateway/).

**Structured output.** A language-model response forced into a typed shape: the gateway's `structured_chat` returns a validated Pydantic object and retries on parse failure (using the `instructor` library for OpenAI). See [Tagging and article analysis](/llm/tagging-and-analysis/).

**Sweep.** A beat task that looks in the database for work that was dropped and queues it again: stale pending items, items without chunks, highlights without embeddings, entities without vectors. See [Background work](/platform/background-work/).

### T

**Tag.** A short label on an item. Tags come from the analysis task (domain and concept tags) or from the user. Tag labels are embedded into `tag_embeddings`. See [Tagging and article analysis](/llm/tagging-and-analysis/).

**Text-to-SQL.** The MCP tool `query_library` has a model write a SQL query from a question. The query is parsed with sqlglot and rejected if it contains writes or DDL, runs with a 500 ms statement timeout, and must be scoped to the user's rows. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/).

**Token.** A piece of text as a model reads it, about 1.2 to 1.5 per English word. Limits and prices are counted in tokens. The embedding model accepts at most 8,192 tokens per input. Not to be confused with an access token.

**trafilatura.** The Python library that extracts the main article text from a web page. See [Extracting an article](/ingestion/extracting-an-article/).

**tsvector.** Postgres's data type for full-text search: a processed list of words with positions. `content_items.search_vector` is filled by a database trigger from title and author (weight A), description and tags (B), and the full text (C), in English and simple dictionaries. `CONTEXT.md` omits the full text. See [Hybrid search](/retrieval/hybrid-search/).

### U

**URL normalization.** Lowercasing scheme and host, dropping tracking parameters and fragments, and trimming a trailing slash, so the same page saved twice is recognized. Implemented in `normalize_url` in `content-queue-backend/app/services/content.py`. Duplicates are blocked by a unique index on user and URL for non-deleted rows. See [Saving a URL](/ingestion/saving-a-url/).

### V

**Vercel.** The platform that hosts and deploys the Next.js frontend. See [Deploy and operations](/platform/deploy-and-operations/).

### W

**Worker.** A Celery process that takes tasks from the broker and runs them. sed.i runs one, with beat inside it. Not the same as a Cloudflare Worker. See [Background work](/platform/background-work/).

**Writing workspace.** The split-pane view at `/lists/[id]`: the article on one side, the draft editor on the other (`WritingWorkspace.tsx`). See [The frontend](/platform/frontend/).

## Check yourself

{% qa "A teammate says `CONTEXT.md` is the source of truth for how many MCP tools exist. How do you check, and what do you find?" %}
Count the tools registered in `content-queue-backend/app/mcp/http_server.py` (each is decorated with `@http_mcp.tool()`). There are 16; `CONTEXT.md` says 13. The code is the source of truth, and the mismatch is listed in the discrepancy file.
{% endqa %}

{% qa "What is the difference between rate limiting and backpressure, and which one does sed.i have?" %}
Rate limiting caps how often one caller can send requests; backpressure slows or refuses new work when the consumer is behind. sed.i has rate limits on four routes. It has no backpressure: the queue can grow without the API reacting.
{% endqa %}

{% qa "Why does idempotency matter for the sweeps?" %}
A sweep re-queues work it believes was dropped, but the original message may only have been slow, so the same task can run twice. If running twice produces the same state, the duplicate is harmless; if it did not, sweeps would corrupt data.
{% endqa %}

{% qa "A document says items are never hard-deleted. What does the code do?" %}
`cleanup_old_deleted_items` runs daily and permanently deletes items whose `deleted_at` is older than 7 days; their highlights go with them through the cascading foreign key. Soft delete is a 7-day undo window, not permanent retention.
{% endqa %}

{% qa "Name three reliability patterns sed.i does not implement, and what stands in for each." %}
No circuit breaker (retries and provider fallback stand in), no backpressure (rate limits on the API only), no SLO (nothing defined; there are no metrics to measure one). An outbox is also absent as a table; the `pending` row and its sweep act as one.
{% endqa %}
