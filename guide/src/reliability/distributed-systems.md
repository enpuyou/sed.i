---
layout: layouts/chapter.njk
title: Distributed-systems review
part: 5
order: 1
summary: The vocabulary of distributed systems, taught with sed.i as the worked example, and the July 2026 failure-injection tests that found real bugs.
verified: 2026-10-25
permalink: /reliability/distributed-systems/
---

## In one minute

sed.i is a modular monolith with a worker tier. Two programs (the API and a Celery worker) share one codebase, one Postgres and one Redis. When a user saves a URL the API writes a row, answers at once, and hands the slow work (download, parse, embed, tag) to the worker through a queue. The "distributed" part is everything between the API, the queue, the worker and the third-party services it calls.

The design rests on four ideas: the database row is the source of truth and the queue message is only a hint; every background task can be run twice without harm; periodic sweeps look for rows that fell behind and re-dispatch them; and every derived index has a cheaper fallback. This chapter walks the standard vocabulary (timeouts, retries, circuit breakers, delivery guarantees, outbox, sagas, sharding, caching, rate limiting, tenancy, observability, deployments) and says, for each, what this system does and whether the claim holds in the current code. It then retells the July 2026 failure-injection tests, which broke the system on purpose and found ten defects.

## The problem

The question this chapter answers: **what can go wrong when work is split across processes and services, and which of those failures does sed.i handle?**

The obvious simpler design is one process that does everything inside the request: fetch the page, parse it, call the embedding API, write the rows, return. It is easy to reason about. It fails because fetching and embedding are slow (seconds to minutes) and fail often (remote sites time out, providers rate-limit), so a user would wait on every save, and one failed call would fail the whole save.

Splitting the work introduces new failures that a single process does not have. A message can be lost between the API and the worker. A worker can die with a task half done. A task can run twice. A slow service can back work up faster than it drains. Two requests can race on the same row. Each of these has a standard name and a standard remedy; this chapter teaches the names and checks which remedies exist here. The goal is that you can explain the system's guarantees in terms an engineer from another company would recognize, and say where they stop.

## Mental model

A restaurant. The waiter (API) writes the order on a ticket and pins it to the rail (the queue), then tells the guest it is coming. The cook (worker) takes tickets one at a time. If the cook faints mid-dish, the ticket is gone from the rail; a manager who walks the floor every five minutes looking for tables that have waited too long (a sweep) notices and re-writes the ticket. The order book (Postgres) says what every table has ordered; the rail is only a way of telling the cook.

The terms:

| Term | Meaning |
|---|---|
| Synchronous call | The caller waits for the answer. |
| Asynchronous message | The caller leaves a message and continues; someone else handles it later. |
| Idempotent | Doing it twice has the same effect as doing it once. |
| At-most-once / at-least-once / exactly-once | A message is delivered no more than once (may be lost), no fewer than once (may repeat), or exactly once (not achievable end to end across independent systems). |
| Reconciler (sweep) | A periodic job that compares desired state with actual state and repairs the difference. |
| Eventual consistency | Derived data lags the source and converges to it. |

{% fig "The save path and where it can break" %}
```text
 browser --POST /content--> API --commit row 'pending'--> Postgres
                              |
                              +--.delay()--> Redis queue --> worker
                                                              |
        sweep (every 5 min) re-dispatches 'pending'           +--> fetch page
        rows older than 10 min  <-------------+               +--> OpenAI
                                              |               +--> write rows
        failure points:  [commit] [.delay()] [queue] [worker] [provider]
```
{% endfig %}

The system's invariants, in order of importance: a user never sees another user's data; a saved URL is never silently lost (it completes, fails visibly, or is retried); derived data (embeddings, chunks, tags, entities) converges to match the source; and per-user LLM spend is bounded.

## Walkthrough: the failure-injection story

On 2026-07-28 the system was attacked deliberately. The method: read every point where the code touches Redis, Postgres, Celery or OpenAI; for each, compare what the code's own comments say happens on failure with what the code does; pick four failures that are cheap to trigger and different in kind; cause each one; write down what actually happened. Four were run live; six more gaps were found by reading and fixed in the same pass. The full record is in `docs/retros/2026-07-28-failure-injection-plan.md` and `docs/changelog/2026-07-28-failure-injection-fixes.md`.

### Test 1: Redis down during a save

**Injected.** Stop Redis (or restart it) while a save is in flight.

**What broke.** The plan expected a failure at the call that queues embedding after extraction. The timing could not be hit reliably, but a restart produced something more severe by accident: Redis reports "healthy" to Docker for about eight seconds while it is still loading its snapshot and refusing commands. A `POST /content` in that window committed the row and then failed at `extract_metadata.delay()`. The item sat at `pending` with no title and no error, indefinitely.

**Principle.** The dual-write problem: the database commit and the queue message are two writes, and a crash between them loses the second. Also: a container's "healthy" is not proof of readiness.

**Fix.** Rather than wrap every `.delay()` in a try block (easy to miss one), a beat task `recover_stale_pending_items` re-dispatches any item still `pending` after 10 minutes. The row is the outbox; the sweep is the relay.

### Test 2: a task that hangs

**Injected.** A scratch task with a soft time limit of 5 s and a hard limit of 8 s, sleeping for 60 s, on the production pool (`--pool=solo`).

**What broke.** Neither limit fired. The task ran the full 60 s and finished normally. Because beat runs in the same process under the solo pool, every periodic job would stall behind a hung task too.

**Principle.** A declared safety mechanism that does not fire is worse than none: it gives false confidence. Timeouts must be enforced by something outside the thing being timed.

**Fix.** Not fixed; deliberately deferred because the cure (a different pool or an external watchdog) changes concurrency for every task. It is item 13 in [Gaps and roadmap](/reliability/gaps-and-roadmap/).

I re-ran a smaller version of this for the guide. A scratch Celery app on a throwaway Redis database defines a task with `soft_time_limit=2` and `time_limit=4` that sleeps 8 s, started under each pool:

{% fig "Solo pool: the limits do nothing (worker log lines)" %}
<!--captured celery -A chaos_demo worker --pool=solo-->
```text
23:13:39 raising: attempt 1
23:13:39 hang: start, limits soft=2s hard=4s, sleeping 8s
23:13:47 hang: slept the full 8s
23:13:47 raising: attempt 2
```
{% endfig %}

{% fig "Prefork pool: the same task is stopped at 2 seconds" %}
<!--captured celery -A chaos_demo worker --pool=prefork-->
```text
23:14:05 raising: attempt 1
23:14:05 hang: start, limits soft=2s hard=4s, sleeping 8s
23:14:07 Soft time limit (2s) exceeded for chaos_demo.hang
23:14:07 hang: SoftTimeLimitExceeded raised
23:14:07 raising: attempt 2
```
{% endfig %}

Under solo the other task's retry ("raising: attempt 2") also waited for the hang to end: eight seconds of head-of-line blocking. Under prefork the limit raised an exception after two seconds and the queue moved on. (Captured on macOS, Celery 5.6.2, one worker process each.)

### Test 3: the LLM provider fails

**Injected.** A mocked rate-limit error from the structured-output call, traced through three real call sites.

**What broke.** The same root cause produced three different outcomes. Tagging caught every exception and returned an empty list, which the caller treated as "no tags": silent data loss. Article analysis caught the exception and returned a "failed" dictionary, so Celery saw a successful task and its `max_retries=3` never fired. Research let the exception escape and relied on a sweep that marks stuck runs partial: the correct pattern.

**Principle.** Failure semantics of a framework: Celery retries only when the task function raises. An exception swallowed inside the task makes retry configuration dead code. The second reproduction in the guide shows it with a counter. Both tasks fail on every call; one returns a dict, the other raises `self.retry`:

{% fig "Same failure, two handlers: how many times each ran" %}
<!--captured python -c redis counters, after the worker run above-->
```text
swallowing ran 1 time(s)
raising ran 4 time(s)
```
{% endfig %}

The task that swallowed the error ran once, and the one that re-raised ran four times (the first attempt plus three retries).

**Fix.** Transient errors (`TRANSIENT_CHAT_ERRORS`: rate limit, timeout, connection) are re-raised, and the task retries with exponential backoff; other errors still end as a terminal "failed". The fix is incomplete on one path: on the OpenAI structured-output route the exception arrives wrapped by `instructor` and is not in that tuple, so it is not retried (see [Tagging and article analysis](/llm/tagging-and-analysis/)).

### Test 4: refresh-token double submit

**Injected.** Two threads send the same refresh token at the same instant.

**What broke.** On localhost the race did not trigger in five of five runs (the database round trip is too fast). With the window artificially widened to mimic network delay, both requests returned 200 with different new tokens: the theft-detection check read "not yet revoked" in both before either wrote.

**Principle.** Check-then-act is a race. Moving the check into the write (a conditional update) makes the database serialize it.

**Fix.** A single `UPDATE ... WHERE revoked_at IS NULL` and a row-count check; a short grace window separates a client retrying a lost response from a replay. The MCP flow's Redis `GET` then `DELETE` became one atomic `GETDEL`.

{% fig "api/auth.py: check and revoke in one statement" %}
<!--src content-queue-backend/app/api/auth.py#376-386-->
```python
    rotated = db.execute(
        RefreshToken.__table__.update()
        .where(
            RefreshToken.id == record.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()

    if rotated.rowcount == 0:
```
{% endfig %}

### The accident: 6.5 million messages

While running test 1, the local queue length was 6,523,419. A sample of 5,000 messages had 5,000 distinct task ids and 4,882 of one task type (`embed_new_entities_task`). The cause was one dispatch per analyzed article instead of one per user, on top of weeks of local messages that nothing drained. Nobody had been watching queue depth, so only a curiosity check found it. The per-article dispatch was deleted; the hourly sweep already covers it.

### The findings mapped

| Injected | Broke | Found by | Fixed | Principle |
|---|---|---|---|---|
| Redis restarting during a save | Item stuck `pending` | Live, by accident | Sweep re-dispatches `pending` after 10 min | Dual write; outbox with polling relay |
| Hung task on solo pool | Limits never fired; beat stalls | Live test | Not fixed (infrastructure) | Enforced timeouts; bulkheads |
| Provider rate limit | Silent empty tags; dead `max_retries` | Mocked, traced | Re-raise transient errors; backoff (incomplete on the structured-output path, see chapter 3.2) | Failure semantics; retry classification |
| Concurrent refresh | Two valid token pairs | Live, with widened window | Atomic conditional update; `GETDEL` | Check-then-act race |
| Queue length by accident | 6.5 M redundant messages | Curiosity | Dispatch removed | Fan-out amplification; backpressure |
| Reading the code | 5 beat tasks without overlap lock | Code reading | Locks on three more | Overlap guard |
| Reading the code | Search lanes failed silently | Code reading | Warning logged per lane | Observability of degraded paths |
| Reading the code | Registration in seven commits | Code reading | One commit; dispatch after it | Atomic unit of work; publish after commit |
| Reading the code | 30 s hang on pool exhaustion | Code reading | `pool_timeout=10` | Fail fast |

The retro's own conclusion: every confirmed gap was a place where an existing good pattern (fail open, sweep, lock) was not extended, not a missing capability. Fixing was mostly applying a known pattern more widely.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/celery_app.py` | Celery configuration, beat schedule, task imports |
| `content-queue-backend/app/core/task_locks.py` | Redis overlap guard for beat sweeps |
| `content-queue-backend/app/middleware/rate_limit.py` | Sliding-window rate limiter |
| `content-queue-backend/app/core/llm_client.py` | Timeouts, retry once, provider failover, budget |
| `content-queue-backend/app/core/embedding_cache.py` | Query-embedding cache |
| `content-queue-backend/app/tasks/extraction.py` | Extraction task and the `pending` sweep |
| `content-queue-backend/app/tasks/base.py` | `DatabaseTask`: one session per task object |
| `content-queue-backend/app/core/database.py` | Connection pool |

The global Celery settings are small:

{% fig "core/celery_app.py: what the worker is told" %}
<!--src content-queue-backend/app/core/celery_app.py-->
```python
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max per task
    task_soft_time_limit=25 * 60,  # Soft limit at 25 minutes
    worker_prefetch_multiplier=1,  # Take one task at a time
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
```
{% endfig %}

The two limits and the restart setting are inert under `--pool=solo` (see test 2). There is no `task_acks_late`, no `task_routes`, and no `retry_jitter` anywhere in `app/`. The worker's start command (`--pool=solo --beat`, one process running tasks and the scheduler) is a Railway dashboard setting; the repository documents it in `ARCHITECTURE.md` and `content-queue-backend/nixpacks.celery.toml` and the live value was not checked.

## The concepts, with this system as the example

Each entry gives the idea, what sed.i does, and a status checked against the current code on 2026-10-25.

### Synchronous and asynchronous

Browser to API is synchronous REST. API to worker is asynchronous (Celery over Redis). The client learns the outcome by polling `processing_status`. Search query embedding and the insight endpoint call OpenAI inside the request, so a slow provider ties up a request thread. Not Kafka or gRPC: one team, one database, no service boundary to cut.

### Timeouts

Each layer's timeout should be shorter than its caller's, otherwise the callee keeps working for a caller that gave up.

| Hop | Timeout | Where | State |
|---|---|---|---|
| Postgres pool checkout | 10 s | `core/database.py` | Set deliberately (was the 30 s default) |
| Postgres query | none for app queries | | No global statement timeout (inferred); the SQL guard for text-to-SQL has its own |
| Redis | connect 1 s, no read timeout | `rate_limit.py`, `task_locks.py` | `socket_connect_timeout=1` only; a connected but silent Redis could block |
| OpenAI | 30 s, SDK retries off | `llm_client.py` | Good, but long inside a request |
| Page download | per-read 30 s, total 60 s | `core/fetch.py` | The total is checked only while data keeps arriving |
| PDF subprocess | 300 s | `extraction_implementations.py` | Enforced by the subprocess call |
| Celery task | 25 min soft, 30 min hard | `celery_app.py` | Not enforced under solo |

### Retries, backoff and jitter

A retry repeats a failed call; backoff waits longer each time; jitter randomizes the wait so many failures do not retry at the same instant.

<!--src content-queue-backend/app/tasks/embedding.py-->
```python
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
```

That is 60, 120, 240 seconds, exponential, capped by `max_retries=3`. There is no jitter. Retries are layered: the gateway retries once on the same provider then fails over, and the task retries three times, so one call site can make up to eight provider attempts (inferred from the code) when a provider is slow rather than down: retry amplification. `BudgetExceededError` is correctly not retried. One flaw remains in extraction: the HTTP-error and timeout handlers set `processing_status="failed"` and commit before calling `self.retry`, so a retrying item looks permanently failed for a few minutes. Status: partly (backoff yes, jitter no, status flicker open).

### Circuit breakers and failover

A breaker stops calling a dependency that is failing so callers fail fast and the dependency can recover. There is none. What exists is failover in `chat`: it tries the primary provider on every call, so during an outage each call pays the primary's timeout before falling back. `embed()` deliberately has no fallback, because mixing two providers' vectors in one index corrupts search: a conscious choice of integrity over availability. Status: open.

### Backpressure and load shedding

Backpressure slows producers when consumers fall behind; load shedding drops work. The worker takes one task at a time (`worker_prefetch_multiplier=1`) and per-user LLM spend is capped, but nothing watches queue depth and sweeps dispatch without checking it. All tasks share one queue, so a 5-minute PDF job or a nightly fan-out delays a user's save (head-of-line blocking). The 6.5-million-message incident is the proof this matters. Status: open.

### Delivery guarantees

Celery acknowledges a message before running it by default, and no setting here changes that. A worker killed mid-task loses that message: at-most-once for that execution. The system upgrades this to at-least-once with reconcilers, for the states they cover:

| State | Reconciler | Covered |
|---|---|---|
| `pending` over 10 min | `recover_stale_pending_items` | Yes |
| `processing` | none | No (open) |
| Highlight embedding missing | `process_all_missing_embeddings` | Yes |
| Item embedding missing | none (the sweep above selects highlights only) | No (open) |
| Chunks missing | `process_all_missing_chunks` (needs an item embedding) | Yes |
| Entities not analyzed, entity vectors missing | daily and hourly sweeps | Yes |
| Research run stuck | `recover_orphaned_runs_task` marks `partial` | Yes |

The development database shows what an uncovered state looks like: one completed item with text and no embedding, which nothing retries; and four items still `pending`, the oldest 284 days (the local worker is rarely running, so the sweep never fires; this is aggregate output of a read-only query). Exactly-once across a database, a broker and a third-party API is not achievable; the honest claim is effectively-once through idempotent effects.

### Idempotency and deduplication

| Task | Safe to run twice? | Mechanism |
|---|---|---|
| `generate_chunk_embeddings` | Yes | Delete and replace in one transaction |
| `cluster_user_tags` | Yes | Replaces the user's clusters |
| `upsert_entity` | Yes | Case-insensitive match, `IntegrityError` handled |
| `generate_embedding`, `analyze_article` | Same result, repeated paid call | Overwrite; the user budget bounds the cost |
| Refresh-token rotation | Yes | Conditional update plus a grace window |
| Email tasks | No | A redelivered task would send a second email; irrelevant under early acks |

`POST /content` is idempotent by content, not by key: URLs are normalized and a partial unique index (`uq_content_items_user_url_active`) allows one active row per user and URL. A concurrent loser now fails at the database, but `ingest_url` does not catch `IntegrityError`, so it gets a 500 instead of the 409 clients expect: partly fixed. There is no task-level deduplication, so the same item can be queued for embedding by the chain, a sweep and a manual action, each a paid call.

### Dead-letter queues

There is no dead-letter queue. Extraction failures end as `processing_status='failed'` plus an error string, which works as a dead-letter table: durable, visible, queryable. Failures in embedding and tagging mostly log and return, and the sweeps retry them with no attempt counter, so a poison input (text a provider always rejects) would be retried forever; the 4,000-character chunk cap fixed one such class. Status: open for non-extraction tasks.

### The outbox pattern

Committing a row and then calling `.delay()` is a dual write. The classic fix writes an "event" row in the same transaction and has a relay publish it. Here the `pending` row is the event and `recover_stale_pending_items` is the polling relay, so no extra table exists. Worst-case latency is the 10-minute staleness plus up to 5 minutes until the next sweep. It covers only the first hop; later hops (extract, embed, tag) rely on in-task dispatch with per-stage sweeps. A real outbox table makes sense when a second consumer of "article saved" appears.

### Sagas

A saga is a multi-step workflow with compensation for partial failure. Ingestion needs none: steps add data and can be re-run. The research run is the one orchestrated workflow: a state machine in `ResearchRun`, a Celery chord, and a recovery sweep that marks stalled runs `partial`. The registration flow was a saga-shaped gap until the July fix made it one commit. Account deletion is still one: rows cascade, but uploaded objects are never deleted (only relevant when object storage is enabled).

### Partitioning and sharding

Every table is scoped by `user_id`, which makes it the natural shard key. Nothing is partitioned today. The staged plan, to do only when needed: put `user_id` first in composite indexes; partition `content_chunks` and `highlights` by hash of `user_id` when chunks reach roughly ten million (smaller indexes, cheaper cleanup, queries that touch one partition); beyond one node's memory, route users to separate clusters or move vectors to a dedicated store. Vector storage arithmetic: 1,536 values at 4 bytes is 6,144 bytes per chunk (measured above in chapter 2.2 as 6,148 stored), so one million chunks is about 6 GB before index overhead.

### Replication and consistency

There is one Postgres primary (replica configuration on the host was not checked), so reads see their own writes. Derived data is eventually consistent:

| Artifact | Lag | If missing |
|---|---|---|
| Item embedding | seconds to minutes | Not in meaning search; keyword search still works |
| Chunks | after the embedding | Falls back to the item embedding |
| Tags, entities | after analysis, sweeps hourly or daily | Empty tag area |

The pattern to state: every derived index has a cheaper fallback. If a read replica is added, route only search to it and keep a user on the primary briefly after a write, or a just-saved item will not appear in their list.

### Caching and invalidation

| Cache | Key | Life | Invalidation |
|---|---|---|---|
| Query embedding | `qemb:` plus a 16-hex-character hash of the text | 1 h | None; the key does not include the model name, so a model change would serve old vectors for up to an hour |
| Insight | `insight:{highlight}:{article}` | 7 days | None; stale if the highlight is edited. The ownership check runs before the cache read |

There is no HTTP caching, no entity tags, no CDN rules. The caches are safe because they are pure functions of their key or low-stakes text.

### Hot keys and skew

The one real hot spot is the unauthenticated public-profile read path, which goes straight to Postgres with no cache header. A viral profile is unbounded reads. Per-user skew is the other: one user with thousands of items dominates scans and recommendations. The nightly memory-consolidation fan-out starts all users in one burst; a jittered delay per user would smooth it.

### Statelessness and load balancing

The API is stateless (token auth, rate limits and locks in Redis). The worker is stateless except for the embedded beat schedule, which is process-local, so two worker replicas would run every schedule twice; the overlap locks cover most sweeps. The `DatabaseTask` base class caches one session on the task object, which is unsafe if a gevent or threads pool ran tasks concurrently, so it must change before the pool does. Railway's edge balances replicas; a Cloudflare Worker proxy sits in front for CORS. `/health` returns a constant, so a replica with a dead database stays in rotation; a readiness route is missing.

### Rate limiting

A sliding-window log in Redis: each request adds a timestamped member to a sorted set, two windows per route, `Retry-After` on rejection, fail-open if Redis is down.

{% fig "middleware/rate_limit.py: check, then add, as two separate steps" %}
<!--src content-queue-backend/app/middleware/rate_limit.py-->
```python
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            _, count = pipe.execute()

            if count >= max_requests:
                return False

            pipe = r.pipeline()
            pipe.zadd(key, {f"{now}:{os.urandom(4).hex()}": now})
            pipe.expire(key, window_seconds)
            pipe.execute()
            return True
```
{% endfig %}

Three limits on the claim. The count and the add are separate round trips, so concurrent requests can overshoot by about the concurrency. Only four routes are limited. And the identifier code reads `request.state.user`, which nothing in `app/` sets, so keys fall through to the client address; behind a proxy that address may be the proxy's. Older notes say "per user"; the code is per address.

### Multi-tenancy

Tenant isolation is a `user_id` predicate in every query, tested for cross-user 404s; the text-to-SQL path adds a structural validator and a mandatory user predicate (ADR 0006); LLM spend is capped per user per day. There is no Postgres row-level security as a second layer, no fairness in the queue (one user's bulk import occupies the single worker), and no per-user statement limits.

### Metrics, tracing, logging

Traces go to an OpenTelemetry backend (opt-in), errors to Sentry (opt-in), LLM calls to Braintrust with user and cost, product events to PostHog. Logs are unstructured text. No code in `app/` emits metrics, and no alerts are defined in the repository. Tracing without metrics is the wrong way round: traces explain a slow request, metrics say something is wrong now, and alerts need metrics.

### SLOs

None are defined. The most useful single measure for this system is ingestion freshness: the age of the oldest item in `pending` or `processing`. One query measures user pain and also catches broker death, worker death and the stuck states above. On the development database today it returns:

{% fig "Ingestion freshness, development database (read-only aggregate)" %}
<!--captured psql freshness query-->
```text
 processing_status | items | oldest_age_days
-------------------+-------+-----------------
 pending           |     4 |             284
```
{% endfig %}

Candidate targets: 99 percent of saves reach `completed` within 2 minutes and 99.9 percent within 15; API availability 99.5 percent over 30 days. These are proposals, not measurements.

### Deployments

Migrations run in a pre-deploy step before new containers start, so the old code runs against the new schema during rollout: migrations must be additive (expand, then contract in a later release); roll forward, not back. A worker restart kills the in-flight task; with early acknowledgment it is lost until a sweep notices (the `processing` hole). The worker's deploy overlap is set to zero so two beat schedulers do not run together (see [Memory](/reliability/memory/)). Front-end flags are `NEXT_PUBLIC_` variables, which the framework inlines at build time, so changing one needs a rebuild; `ARCHITECTURE.md` says flags toggle "without redeployment", true only for backend flags. There is no canary stage.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Row as source of truth, message as hint | Rely on broker durability; transactional outbox table | One fewer moving part; sweeps cover most states. Cost: recovery latency of up to 15 minutes. |
| Sweeps over `acks_late` | `acks_late` plus reject on worker loss | Covers every task and a lost broker. `acks_late` needs each task proven safe to run twice. |
| Fail open for limits, locks and budgets | Fail closed | A read-later app prefers availability over strictness. Overlap protection disappears exactly when Redis is down. |
| No fallback for `embed()` | Fail over to a second embedding provider | Mixed vector spaces break search; waiting is safer than wrong. |
| State machine in Postgres for research | Temporal or another workflow engine | Already operated; degrades to `partial`. Revisit past about ten steps or long waits. |
| Polling for results | WebSockets, server-sent events | Cheaper; fine at this scale. |
| One Redis for everything | Separate broker and cache | Simple. Needs a no-eviction policy, which makes the cache part unbounded (the live setting was not checked). |

Relevant decision records: ADR 0002 (observability), 0005 (pipeline orchestration), 0006 (text-to-SQL), 0007 (worker memory isolation), in `docs/decisions/`.

## How it fails

| Failure | What happens now |
|---|---|
| Redis down | Saves commit and stay `pending`; the sweep recovers them after Redis returns. Limits, budgets and locks fail open. Queued messages are lost. |
| OpenAI down | Chat falls back to Bedrock; embeddings wait and search degrades to keyword; tasks retry with backoff, without jitter. |
| Worker killed mid-task | Message lost; `processing` item stuck (no sweep). |
| Hung task | Not interrupted under solo; every schedule stalls with it. |
| Postgres connections exhausted | Requests fail after 10 s instead of 30 s. |
| Two simultaneous saves of one URL | One row; the loser gets a 500. |

## What is weak or missing

Ranked; the full register with evidence and effort is in [Gaps and roadmap](/reliability/gaps-and-roadmap/).

1. **One worker slot, limits not enforced, scheduler in the same process.** The highest-leverage reliability gap.
2. **Stuck `processing` items and the missing item-level embedding sweep.** Two recovery holes in the "never silently lost" invariant.
3. **No metrics, alerts or freshness measure.** Failures are discovered by users.
4. **No circuit breaker, no jitter, one queue.** Outages are survivable but slow and spiky.
5. **Check-then-act remains** in the rate limiter, the duplicate-save path and registration.
6. **Redis is one failure domain** for the queue, caches, locks and limits.
7. **Single points not verified here:** backups, replicas, eviction policy.

{% say %}
sed.i splits slow work off the request path: the API commits a row and returns, a Celery worker does the fetching and embedding, and the client polls a status column. The row is the source of truth and the queue message is only a hint, so I recover from lost messages with sweeps that re-dispatch anything that fell behind, and I make tasks safe to run twice instead of promising exactly-once. I tested that on purpose in July: I killed Redis mid-save, hung a task, simulated a provider outage and raced two token refreshes. That found real bugs, like retry settings that never fired because the task swallowed the error, and a time limit that does nothing on the pool we run. What is still weak is a single worker slot, items stuck in processing after a crash, and no metrics or alerts.
{% endsay %}

## Check yourself

{% qa "A save commits a row and then the queue call fails. What happens, and which pattern is this?" %}
The row stays `pending`; after 10 minutes the next sweep (every 5 minutes) re-dispatches it. The row acts as the outbox record and the sweep as a polling relay, so the dual-write loss is repaired without an extra table.
{% endqa %}

{% qa "A task is configured with `max_retries=3` but never retries. Give two causes seen in this codebase or its tests." %}
The task catches the exception and returns a value, so Celery sees success (analysis and tagging before the fix; reproduced with a counter: one run against four). Or the retry configuration is inert because of the pool (time limits under solo).
{% endqa %}

{% qa "Why is exactly-once delivery not claimed, and what is claimed instead?" %}
A database, a broker and a provider API cannot take part in one atomic step. The system claims effectively-once effects: at-most-once transport, upgraded by sweeps to at-least-once, with idempotent tasks so a repeat is harmless (delete-and-replace chunks, unique constraints, conditional updates), and duplicate paid calls accepted as a bounded cost.
{% endqa %}

{% qa "Which items does the current set of sweeps leave unrecovered?" %}
Items stuck in `processing` after a worker dies mid-task, and completed items whose embedding dispatch was lost (the embedding sweep selects highlights only, and the chunk sweep needs an item embedding first).
{% endqa %}

{% qa "Why does the rate limiter not do what older notes claim?" %}
The key is built from `request.state.user`, which nothing sets, so it falls back to the client address, and behind a proxy that may be one shared address. The count and the add are also separate steps, so concurrent requests can exceed the limit slightly.
{% endqa %}

{% qa "Why should a dependency check not go on the liveness probe?" %}
A database blip would then make the platform restart every healthy replica, turning a dependency outage into a full outage. Liveness should say only that the process is up; a separate readiness route checks dependencies and is used for routing.
{% endqa %}
