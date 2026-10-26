---
layout: layouts/chapter.njk
title: The system on one page
part: 0
order: 2
summary: Every process in sed.i, what runs where, and one saved URL and one search query followed across all of them.
verified: 2026-10-25
permalink: /orientation/system-on-one-page/
---

## In one minute

sed.i is four things you run and several services you rent. The things you run are a Next.js frontend (on Vercel), a FastAPI backend (on Railway), a Celery worker with a built-in scheduler called beat (on Railway), and a Cloudflare Worker that sits in front of the API and handles browser cross-origin rules. They share two stores: Postgres with the pgvector extension, which holds all durable data, and Redis, which holds the job queue, caches, locks and counters. The services you rent are OpenAI (embeddings and language-model calls) and four observability tools (Sentry, OpenTelemetry to a trace backend, Braintrust, PostHog).

The rule that shapes everything: the API answers quickly and never does slow work inline. Saving a link writes one row and puts a message on a queue; a worker picks the message up, fetches the page, and calls OpenAI. Searching is the opposite: it runs in the API process, one SQL query against Postgres, plus at most one OpenAI call for the query itself.

## The problem

**What are the moving parts, and where does a given piece of work actually run?**

Without a map, every later chapter has the same gap. A reader sees "a Celery task embeds the article" and cannot say which machine runs it, where its input comes from, or where its output goes. The obvious simpler design is one web process that does everything inside the request: fetch the page, call OpenAI, write the rows, then answer. It is the right design for a prototype and fails on three counts. A slow or hostile website holds a request open. A restart loses work in flight. And a burst of saves cannot be spread out. The split into API, queue and worker is the answer to those three, and everything else in this chapter follows from it.

## Mental model

Think of a restaurant. The API is the front of house: it takes the order, writes a ticket, and tells the customer it is coming. Redis is the ticket rail. The worker is the kitchen: it takes one ticket at a time and does the slow cooking. Postgres is the pantry and the order book: everything that must survive a closing time. Beat is the manager who, on a timer, walks the kitchen looking for tickets that fell on the floor and puts them back on the rail.

The precise terms:

| Term | Meaning here |
|---|---|
| Process | One running program with its own memory. sed.i has several, listed below. |
| Broker | The store that holds queued task messages until a worker takes one. Redis is the broker. |
| Celery | The Python library that defines tasks, puts them on the broker, and runs them in workers. |
| Beat | Celery's scheduler. It puts a task on the queue on a fixed interval. Here it runs inside the worker process (`--beat`). |
| Sweep | A beat task that scans the database for work that was dropped and re-dispatches it. |
| pgvector | A Postgres extension that adds a vector column type and distance operators. |
| Proxy | A server that receives a request and forwards it to another server. |

{% fig "Topology: who talks to whom" %}
```text
 Browser (Next.js, Vercel)      Extension (Chrome, Safari)
          |                              |
          +--------------+---------------+
                         | HTTPS
                         v
            Cloudflare Worker (CORS proxy)
                         |
                         v
        FastAPI on Railway  (REST + MCP at /mcp-transport)
           |            |
           |            +--------> Redis <--------+
           v                       broker, cache, |
   Postgres + pgvector             limits, locks  |
           ^                                      |
           |                          Celery worker + beat
           +------------------------- (Railway) --+
                                       |
                                       v
                                OpenAI (embeddings, LLM)

 Side channels from API and worker: Sentry, OpenTelemetry,
 Braintrust, PostHog.  From the browser: Sentry, PostHog.
```
{% endfig %}

## Walkthrough: one saved URL and one search query

Two examples, because they take opposite paths. The saved URL is slow and asynchronous; the search query is fast and synchronous.

### Part A: saving a URL

The input, typed into the add form:

```text
HTTPS://Example.com/Posts/espresso/?utm_source=news&fbclid=XYZ#intro
```

#### A1: browser to API

The add form calls `POST /content` through `fetchWithAuth` in `frontend/lib/api.ts`. The base address comes from `NEXT_PUBLIC_API_URL`, which in production names the API's public domain, not Railway's. The request carries two cookies set at login (an httpOnly access token and a CSRF token) and the CSRF token again as a header. The extension sends a Bearer token instead of cookies.

Stored: nothing yet. Cookies live in the browser.

#### A2: the Cloudflare Worker

The request reaches the Cloudflare Worker (`cloudflare-worker/worker.js`) first. A browser sends a preflight `OPTIONS` request before cross-origin calls with custom headers; the Worker answers those itself and never forwards them. For real requests it deletes two headers and forwards the rest to the Railway service:

{% fig "cloudflare-worker/worker.js: the forwarding step" %}
<!--src cloudflare-worker/worker.js#64-66-->
```javascript
const forwardHeaders = new Headers(request.headers);
forwardHeaders.delete("Origin");
forwardHeaders.delete("Host");
```
{% endfig %}

The Worker exists because Railway's edge rejected requests carrying an `Origin` header from allowed sites (the file's header comment). It then adds its own CORS headers to the response. The allowed origins are a list in the Worker; the API has a second list for the same purpose in `content-queue-backend/app/main.py`.

Stored: the Worker keeps nothing. The origin address is a Cloudflare secret.

#### A3: FastAPI

Three middleware layers run before the route: CORS, a Redis-backed rate limiter, and a CSRF check that only applies to cookie-authenticated requests. The rate limiter treats `POST /content` as a limited route:

{% fig "middleware/rate_limit.py: per-route limits (per minute, per hour)" %}
<!--src content-queue-backend/app/middleware/rate_limit.py#124-129-->
```python
_LIMITED_ROUTES: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/content"): (10, 50),
    ("POST", "/auth/login"): (10, 30),
    ("POST", "/auth/register"): (5, 15),
    ("GET", "/search/semantic"): (30, 300),
}
```
{% endfig %}

Stored in Redis: a sorted set under `ratelimit:{identifier}:{window}` per window, expiring when the window ends.

The route handler `create_content_item` in `content-queue-backend/app/api/content.py` calls `ingest_url` in `content-queue-backend/app/services/content.py`. That function normalizes the URL, looks for an active duplicate, inserts a row and commits, and only then queues the job:

{% fig "services/content.py: insert, commit, then queue" %}
<!--src content-queue-backend/app/services/content.py#185-198-->
```python
item = ContentItem(
    user_id=user.id,
    original_url=normalized,
    submitted_via=submitted_via,
    processing_status="pending",
)
db.add(item)
db.commit()
db.refresh(item)

if dispatch_extraction:
    from app.tasks.extraction import extract_metadata

    extract_metadata.delay(str(item.id))
```
{% endfig %}

Running `normalize_url` on the input gives (captured by running the code):

```text
https://example.com/Posts/espresso
```

The scheme and host are lowercased, the tracking parameters and fragment are dropped, and the path keeps its case. The API answers `201` with the new item at status `pending`.

Stored in Postgres: one row in `content_items` with only `user_id`, `original_url`, `submitted_via` and `processing_status` set. Stored in Redis: one message on the `celery` queue. The chapter [Saving a URL](/ingestion/saving-a-url/) covers duplicates and the status machine.

#### A4: the worker fetches the page

The worker process runs `celery -A app.core.celery_app worker --pool=solo --beat`. It takes the message and runs `extract_metadata` in `content-queue-backend/app/tasks/extraction.py`: set status `processing`, download the page with a size and time limit (`content-queue-backend/app/core/fetch.py`), read the title, author and thumbnail from the page's metadata tags, and commit. Then it queues the second phase. In production the second phase is a plain Celery task (`extract_full_content`) because `PREFECT_ENABLED` is false; with the flag on, the code would submit a Prefect deployment run and fall back to the Celery task if that failed.

Stored in Postgres: `title`, `description`, `thumbnail_url`, `author`. See [Extracting an article](/ingestion/extracting-an-article/).

#### A5: the worker extracts the article

`extract_full_content` downloads the page again, runs trafilatura to get the article as HTML, and writes `full_text`, `word_count` and `reading_time_minutes`. It then sets `processing_status` to `completed` and queues `generate_embedding`. A Postgres trigger rebuilds the item's `search_vector` (the keyword index) on every insert or update, so keyword search works from this moment.

Stored in Postgres: `full_text`, counts, status. The row is now readable in the reader.

#### A6: the worker embeds

`generate_embedding` in `content-queue-backend/app/tasks/embedding.py` cuts the title, description and text to at most 8,000 tokens, asks OpenAI for one 1,536-number embedding through the LLM gateway (`content-queue-backend/app/core/llm_client.py`), stores it on the row, and fans out two more tasks:

{% fig "tasks/embedding.py: what happens after the article embedding" %}
<!--src content-queue-backend/app/tasks/embedding.py#178-183-->
```python
# Trigger combined analysis (tags + entities) and chunk embeddings
from app.tasks.article_analysis import analyze_article_task
from app.tasks.chunk_embeddings import generate_chunk_embeddings_task

analyze_article_task.delay(content_item_id)
generate_chunk_embeddings_task.delay(content_item_id)
```
{% endfig %}

Before each OpenAI call the gateway checks the user's daily spend in Redis (key `llm_spend:{user_id}:{date}`) and records the cost afterwards. Each call is also logged as a Braintrust span with the user id.

Stored: `content_items.embedding` in Postgres; a spend counter in Redis. See [The LLM gateway](/llm/gateway/).

#### A7: tags, entities and chunks

`analyze_article_task` makes one language-model call that returns tags, named entities and relations between entities. It writes `content_items.tags` and rows in `tag_embeddings`, `entities`, `entity_mentions` and `entity_relations`. `generate_chunk_embeddings_task` cuts the article into passages and stores one embedding per passage in `content_chunks`. See [Tagging and article analysis](/llm/tagging-and-analysis/), [Entity graph and connections](/retrieval/entity-graph-and-connections/) and [Chunking and embedding](/retrieval/chunking-and-embedding/).

#### A8: the safety nets

Any hop above can drop its message (Redis restart, worker killed mid-task). Beat runs sweeps that look in Postgres for the missing result and queue it again:

| Sweep | Interval | Looks for |
|---|---|---|
| `recover_stale_pending_items` | 5 minutes | items still `pending` after 10 minutes (the first message never arrived) |
| `process_all_missing_chunks` | 5 minutes | items with an embedding but no chunks |
| `process_all_missing_embeddings` | 5 minutes | highlights with no embedding |

Highlight embeddings are produced only by that last sweep: the docstring of `content-queue-backend/app/api/highlights.py` says that creating a highlight triggers embedding, but the handler queues nothing (see the discrepancy file for this chapter). The full beat schedule is in `content-queue-backend/app/core/celery_app.py`; [Background work](/platform/background-work/) covers it.

#### A9: what the reader sees

The frontend polls the item (`frontend/hooks/useProcessingPolling.ts`) until it reaches `completed`, then shows title, thumbnail and text. Searching for the article by meaning works only after A6 and A7 finish; searching by keyword works after A5.

### Part B: searching

The input, typed into the navbar search box:

```text
why is my shot sour?
```

#### B1: browser to API

`frontend/components/SearchBar.tsx` calls `searchAPI.semantic`, which is `GET /search/semantic?query=...` with default mode `auto`. The request goes through the same Worker hop as A2. The rate limiter allows 30 per minute per user for this route.

#### B2: classify

`hybrid_search` in `content-queue-backend/app/core/hybrid_search.py` first asks `classify_query` in `content-queue-backend/app/core/search_router.py` which kind of query this is. It is a handful of regular expressions, no model call:

{% fig "core/search_router.py: the question rule" %}
<!--src content-queue-backend/app/core/search_router.py#100-101-->
```python
if q.endswith("?") or _QUESTION_STARTS.match(q):
    return "semantic", {}
```
{% endfig %}

Captured results for six queries (the function was run directly):

```text
'espresso' -> ('keyword', {})
'why is my shot sour?' -> ('semantic', {})
'espresso grind size notes' -> ('hybrid', {})
'author:Smith tag:coffee' -> ('filter', {'author': 'Smith', 'tag': 'coffee'})
'"water temperature"' -> ('keyword', {})
'example.com' -> ('filter', {'site': 'example.com'})
```

The classifier took 1.8 microseconds per call on a development laptop (measured: 20,000 calls of the question example, timed with `timeit`).

#### B3: embed the query

The question goes to the semantic lane. It computes the query's embedding through a Redis cache keyed `qemb:` plus a hash of the lowercased text, with a one-hour expiry (`content-queue-backend/app/core/embedding_cache.py`). On a miss it makes one OpenAI call. If Redis is down it calls OpenAI directly.

Stored in Redis: the 1,536 numbers for this query, for one hour.

#### B4: one SQL query

The lane compares the query vector with every chunk of the user's articles in Postgres and keeps the best chunk per article; [Chunking and embedding](/retrieval/chunking-and-embedding/) shows the SQL. The route then loads the matching `content_items` rows in one query and also searches highlights by keyword. The response lists articles with a `match_type` and highlights.

Nothing is written to Postgres by a search. The only write is a log line when the user later clicks a result (`POST /search/telemetry` logs and does not store).

The full decision tree, the fusion step and the other lanes are in [Hybrid search](/retrieval/hybrid-search/).

#### B5: what the tools see

Every step above is traced. OpenTelemetry instruments FastAPI, SQLAlchemy and Celery (`content-queue-backend/app/core/observability.py`), so a request shows up as a trace with its SQL queries. Sentry captures exceptions in both the API and the frontend. Braintrust records each embedding and language-model call with cost and user. PostHog records product events (sign-ins, for example). Each tool turns itself off when its key or endpoint is missing from the environment.

## How it is built here

### Repository map

Built from the directory listing of the repository on 2026-10-25. Two local-only directories (`_agents`, `private`) are not tracked by git and are left out.

| Directory | Purpose |
|---|---|
| `content-queue-backend/` | The Python backend: API, workers, models, migrations, tests. |
| `frontend/` | The Next.js app: pages in `app/`, UI in `components/`, shared code in `lib/`, `contexts/` and `hooks/`. |
| `extension/` | The Chrome Manifest V3 extension: popup, content script, service worker. |
| `safari-extension/` | The Safari port, a mirror of `extension/` plus an Xcode project. |
| `cloudflare-worker/` | The CORS proxy Worker and its `wrangler.toml`. |
| `infra/` | Pulumi program for the optional AWS resources (IAM, S3, Bedrock access). |
| `evals/` | Evaluation harnesses and results: retrieval, memory prompts, research brief, HNSW index. |
| `docs/` | Architecture decisions, audits, changelog, design docs, plans, retros, instructions. |
| `scripts/` | Repo-level helpers: pre-commit hook, Alembic head check, HNSW benchmark, image tools. |
| `pdf/` | Sample PDFs for local extraction tests (only its readme is tracked). |
| `guide/` | This study guide: an Eleventy site. |

Inside `content-queue-backend/`:

| Directory | Purpose |
|---|---|
| `alembic/` | Database migrations (`alembic/versions/`). |
| `experiments/` | One-off experiment code (the PDF extraction evaluation). |
| `scripts/` | Operational scripts: post-deploy index build, backfills, memory profiling. |
| `tests/` | pytest suite, including `tests/evals/` and `tests/mcp/`. |

Inside `content-queue-backend/app/`:

| Directory | Purpose |
|---|---|
| `api/` | FastAPI routers, one file per area (auth, content, highlights, lists, drafts, search, memory, research, themes, vinyl, analytics); `api/endpoints/public.py` serves public profiles. |
| `core/` | Cross-cutting machinery: config, database session, auth dependencies, Celery app, LLM gateway, hybrid search and router, entity graph, embedding cache, bounded fetch, task locks, storage, observability. |
| `models/` | SQLAlchemy table definitions. |
| `schemas/` | Pydantic request and response shapes. |
| `services/` | Business logic shared by API and MCP (`services/content.py`: URL normalization, duplicate check, `ingest_url`). |
| `tasks/` | Celery tasks: extraction, embedding, chunking, tagging and analysis, clustering, memory, research, cleanup, Discogs, email. |
| `middleware/` | Rate limiting and CSRF. |
| `mcp/` | The MCP server: tools, OAuth, HTTP transport. |
| `workflows/` | The Prefect version of the ingestion pipeline (off in production). |
| `management/` | One management script (highlight embedding backfill). |

### What runs where

| Service | Platform | How it starts | Notes |
|---|---|---|---|
| Frontend | Vercel | Deploys when `main` updates | Next.js 16 (`frontend/package.json`) |
| API (and MCP) | Railway | `uvicorn app.main:app` from `nixpacks.toml`; migrations run first in `railway.json` `preDeployCommand` | MCP is mounted in the same process at `/mcp-transport` |
| Worker plus beat | Railway | Start command set in the Railway dashboard: `celery ... --pool=solo --beat` | One process runs tasks and the scheduler; `nixpacks.celery.toml` documents the command but Railway never reads it |
| Prefect server | Railway | Stopped | `PREFECT_ENABLED=false` on API and worker; definition kept |
| Postgres, Redis | Railway managed | Always on | No deploys; the dev equivalents are in `docker-compose.yml` |
| CORS proxy | Cloudflare Workers | Deployed with Wrangler | Routes the API's public domain to Railway |
| Local development | Your machine | `make dev` | Runs `make backend`, `make worker`, `make frontend` in parallel |

The facts about production flags, from `ARCHITECTURE.md` section 3 and `docs/audits/2026-10-24-memory-fix-results.md` (not verified against the live Railway project, which this guide does not touch):

- The `prefect` service is stopped and `PREFECT_ENABLED=false` on both the API and the worker, so ingestion runs the Celery chain described in A4 to A7. Stopping the service freed about 222 MB of memory, the audit measured.
- The worker sets `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0`, so the old container stops as the new one starts. The previous overlap produced an 840 MB peak and let two beat schedulers run at once. The API keeps the default overlap for deploys without downtime.

Deploy history that explains the current files: a `Procfile` defining only a `web` process silently overrode `nixpacks.toml` and made the worker service run the web server instead of Celery. It was deleted on 2026-09-27, and the worker's start command was moved into the Railway dashboard (`ARCHITECTURE.md` section 3). [Deploy and operations](/platform/deploy-and-operations/) tells the whole story.

### What is stored where

Row counts are from the development database on 2026-10-25 (measured with `count(*)`): it is one person's data and is not a production figure.

| Store | What it holds | Examples (dev rows) |
|---|---|---|
| Postgres, `content_items` | One row per saved link: metadata, `full_text`, article embedding, keyword index, tags, reading state, `deleted_at` | 369 rows (71 active, 298 soft-deleted), 123 MB with indexes |
| Postgres, `content_chunks` | Passages with their embeddings | 573 rows, 10 MB |
| Postgres, `highlights` | Selections with offsets, note, color, embedding | 77 rows |
| Postgres, `lists`, `content_list_membership`, `drafts` | Lists, which item is in which list, one draft per list | 7 lists, 29 memberships, 2 drafts |
| Postgres, `entities`, `entity_mentions`, `entity_relations` | The entity graph | 981 / 1,719 / 356 rows |
| Postgres, `tag_embeddings`, `reading_clusters` | Embedded tag labels and tag clusters | 7,183 / 11 rows; `tag_embeddings` is 115 MB |
| Postgres, `users`, `refresh_tokens`, `verification_tokens` | Accounts and auth tokens | 5 / 9 / 4 rows |
| Postgres, `user_profiles`, `user_memory_events`, `research_runs`, `research_memory` | Memory profile, research runs and their memory | 1 / 0 / 131 / 0 rows |
| Postgres, `vinyl_records` | The crates collection | 34 rows |
| Redis | Celery queue and task results, `ratelimit:*` windows, `qemb:*` query embeddings (1 hour), `llm_spend:*` daily spend, `task_lock:*` sweep locks, MCP OAuth state | In the dev instance: 36 `llm_spend` keys and the Celery bookkeeping keys |
| S3 (optional) | Original PDF files | Off unless `S3_STORAGE_ENABLED` and a bucket are set |
| Browser | Reading settings, cached item, sort order (localStorage / sessionStorage); auth cookies | Not server data |
| Extension | The access token in extension storage | Not server data |

All vectors are 1,536-number columns using `vector_cosine_ops` HNSW indexes (`m=16`, `ef_construction=64`) on `content_items`, `content_chunks`, `highlights` and `entities`, checked with `pg_indexes` on the development database. See [Data model and Postgres](/platform/data-model/).

### Where each concern is covered

| If you want to understand | Read |
|---|---|
| How a URL is accepted | [Saving a URL](/ingestion/saving-a-url/) |
| How article text is extracted | [Extracting an article](/ingestion/extracting-an-article/), [PDFs](/ingestion/pdfs/) |
| What an embedding is and how it is searched | [RAG from first principles](/retrieval/rag-from-first-principles/) |
| How queries are routed and merged | [Hybrid search](/retrieval/hybrid-search/) |
| Why jobs are retried, deduplicated and swept | [Background work](/platform/background-work/) |
| How OpenAI calls are limited and traced | [The LLM gateway](/llm/gateway/) |
| Why the worker's memory was a problem | [Memory: investigation and fixes](/reliability/memory/) |

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| API returns before extraction runs | Do the work in the request | A slow site or a restart would otherwise fail or stall the save. The cost is the `pending` and `processing` states the UI must show. |
| Celery with Redis as broker | A Postgres-backed queue; a hosted queue; Prefect or Temporal | Redis was already needed for caches and limits, and Celery is the common Python choice. Prefect and Temporal were evaluated; see `docs/decisions/0005-pipeline-orchestration.md`. Opinion: for one worker at this size the plain chain is enough and cheaper to run. |
| One worker process runs tasks and beat | A separate beat service | One fewer service to pay for. The cost: a hung task also stops the scheduler (the known gap in [Background work](/platform/background-work/)). |
| Postgres with pgvector for vectors | A dedicated vector database | One database to back up, filter by `user_id` and `deleted_at` in the same query, and join with metadata. See `docs/decisions/0001-vector-storage.md`. |
| Cloudflare Worker in front of the API | CORS handled only in FastAPI | Railway's edge rejected cross-origin requests that carried an `Origin` header, so the Worker answers preflights and strips the header. It also gives a place for WAF rules (`README.md`). |
| MCP inside the API process | A separate MCP service | Reuses the auth and database code. The cost is that MCP traffic shares the API's memory and CPU. |
| Observability tools that switch off without keys | Always-on required tooling | Local development and tests need no accounts. See `docs/decisions/0002-observability-stack.md`. |

## How it fails

| Failure | What happens | What covers it |
|---|---|---|
| Redis unreachable when a save is committed | The row exists at `pending`; the queue message was never written | `recover_stale_pending_items` re-queues it after 10 minutes |
| Worker killed mid-task | The message may or may not be redelivered; the item can be left without embedding or chunks | Sweeps for missing chunks and highlight embeddings. Inferred from the code (a search of `app/tasks/`): no sweep looks for completed items that have no article embedding, or for items stuck at `processing`, so those depend on the task's own retries |
| A task hangs | Under the solo pool, task time limits are not enforced, so the single worker and beat both stop | Known gap in `ARCHITECTURE.md` section 9; fix needs a different pool or an external watchdog |
| Two worker containers at deploy time | Two beat schedulers run for the overlap period | `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0` on the worker; Redis run locks on sweeps |
| OpenAI down or over budget | Embedding and analysis tasks retry or stop with `budget_exceeded`; saving and reading keep working | The gateway; search falls back to keyword results when the embedding fails |
| Memory pressure | A 20 MB page produced about 1.9 GB of vectors in one task | Bounded fetch and batching; see [Memory: investigation and fixes](/reliability/memory/) |

## What is weak or missing

Ranked by how likely each is to matter.

1. **Single worker, no task time limit.** Everything background runs in one process whose limits are not enforced. One stuck task stalls ingestion, embeddings, sweeps and the nightly jobs together.
2. **Queue depth is not visible from the app.** Redis holds the queue, but the repository code read for this chapter shows no alert on its length. A growing backlog would be noticed from user complaints. The failure-injection work found a 6.5-million-message backlog once (`docs/changelog/2026-07-28-failure-injection-fixes.md`).
3. **Several documents disagree with the code.** The project's own docs describe a different task chain, a different token store and a different tool count than the code. The discrepancy file for this chapter lists them; treat the code as the reference.
4. **The production setup lives in a dashboard.** The worker's start command and several environment variables are set in the Railway UI, not in a file in the repository, so a rebuilt project would not reproduce them from the repo alone.
5. **Search and ingestion share fate with one database.** There is one Postgres; a slow sweep query and a user search compete for the same connections.

{% say %}
sed.i has a Next.js frontend on Vercel, a FastAPI backend on Railway, and a Celery worker with beat on Railway, with a small Cloudflare Worker in front of the API to deal with cross-origin rules. Postgres with pgvector holds all durable data and Redis holds the job queue, caches and locks. When I save a link, the API writes a pending row and queues a job, then answers. The worker fetches the page, extracts the article, embeds it with OpenAI, and then queues tagging and chunking; beat sweeps pick up anything dropped. A search is synchronous: classify the query with rules, embed it if needed with a cached call, run one SQL query in Postgres. Sentry, OpenTelemetry, Braintrust and PostHog watch it. In production the Prefect service is stopped and ingestion uses the plain Celery chain.
{% endsay %}

## Check yourself

{% qa "Redis is unreachable for ten seconds while a user saves a link. What is in Postgres, and how does the item still get processed?" %}
The `content_items` row is committed at status `pending` before the queue message is sent, so the row exists. The message may never have been written. Every five minutes beat runs `recover_stale_pending_items`, which finds items still `pending` after ten minutes and queues them again.
{% endqa %}

{% qa "Why does searching work for a brand-new article by keyword before it works by meaning?" %}
Keyword search uses the `search_vector` column, which a database trigger fills when the article text is written (step A5). Meaning-based search needs the embedding and the chunk embeddings, which are produced later by queued tasks (A6, A7).
{% endqa %}

{% qa "Which processes would you restart if the worker's memory grew, and what else stops while it is down?" %}
Only the worker service. While it is down, no extraction, embedding, tagging or sweep runs, because beat lives in the same process. The API keeps serving reads and writes, and new saves wait at `pending` until the worker returns.
{% endqa %}

{% qa "What does the Cloudflare Worker do that FastAPI's own CORS handling does not?" %}
It answers cross-origin preflight requests itself and removes the `Origin` header before forwarding, because Railway's edge rejected requests carrying it. FastAPI's CORS middleware still exists for direct access, but in production the Worker is the first layer.
{% endqa %}

{% qa "A teammate says production ingestion goes through Prefect. What do you check, and what is the answer?" %}
Check the `PREFECT_ENABLED` setting in `content-queue-backend/app/core/config.py` (default false) and the production flags in `ARCHITECTURE.md` section 3: the Prefect service is stopped and the flag is false on the API and worker, so `extract_metadata` queues the plain Celery task `extract_full_content`.
{% endqa %}

{% qa "Why is MCP not a separate service, and what is the cost of that choice?" %}
It reuses the API's auth, database session and service code, so there is one thing to deploy. The cost is that MCP tool calls, including ones that call language models, run in the API process and compete with ordinary requests for memory and CPU.
{% endqa %}
