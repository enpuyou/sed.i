---
layout: layouts/chapter.njk
title: Background work
part: 4
order: 2
summary: How Redis and Celery move slow work out of web requests, what a task message looks like on the wire, and why the production worker runs one task at a time.
verified: 2026-10-26
permalink: /platform/background-work/
---

## In one minute

Saving a URL must return in milliseconds, but fetching the page, calling OpenAI and writing embeddings takes seconds to minutes. So the API only writes a row and drops a small message into a queue. Redis, a fast in-memory key-value server, holds that queue. Celery is the Python library on both sides: the API calls `task.delay(...)` to enqueue, and a separate worker process takes messages off the queue and runs the matching function.

The worker also runs Celery beat, a clock that enqueues scheduled tasks. Several of those are sweeps: they look in Postgres for work that should have happened and did not, and redispatch it. Redis is also used for locks that stop sweeps overlapping, rate limits, a per-user daily spend counter and a few caches. In production the worker runs with `--pool=solo`: one process, one task at a time, which keeps memory low and means one slow task delays everything behind it.

## The problem

The question this chapter answers: **how does slow, failure-prone work happen reliably after the HTTP response has already been sent, and what happens when a step of it dies?**

The obvious simpler design is to do the work inside the request, or to start a thread in the API process. Inside the request, a page that takes 20 seconds to fetch holds a connection open and the user stares at a spinner; a deploy that restarts the API kills the work. A thread in the API process shares its memory limit and its lifetime. A queue and a separate worker give the work its own process, its own restarts and its own retry rules.

The price is that "later, somewhere else" can fail silently: the message may never be sent, the worker may die mid-task, and the work may run twice or not at all. Most of this chapter is about which of those can happen here and what repairs them.

## Mental model

Think of a restaurant. The waiter (the API) writes an order slip and puts it on a spike (the queue) and goes back to the customers. The cook (the worker) takes the next slip, cooks, and puts the plate out. A kitchen timer (beat) reminds the cook of routine jobs. A rule on the wall ("only one person cleans the fryer at a time") is a lock. Postgres is the restaurant's ledger: the slips only say "order 123", and the cook looks up the details in the ledger.

| Term | Meaning here |
|---|---|
| Redis | A server that keeps data in memory, as values of a few types under named keys. Each command runs to completion before the next, so single commands are atomic. |
| Key and TTL | A name for a value, and an optional time after which Redis deletes it. |
| String, list, set, sorted set, hash | Redis value types: a blob; an ordered sequence; unique members; unique members each with a numeric score; a small dictionary. |
| Celery | A Python library that turns a decorated function into a task that can be run elsewhere. |
| Broker | The middleman that holds task messages. Here: Redis. |
| Worker | A Python process that imports the app, waits for messages and runs the functions. |
| Message | JSON holding the task name, arguments, a task id and a retry count. Only ids are sent, never article text. |
| Ack | The worker telling the broker "I have this message, forget it". |
| Beat | A scheduler that sends ordinary task messages on a timer. It does not run tasks. |
| Sweep | A scheduled task that finds unfinished work in Postgres and redispatches it. |
| Pool | How a worker runs tasks: child processes (`prefork`), threads, or `solo` (inline, one at a time). |

{% fig "Life of a task" %}
```text
API process                Redis                   Worker process
-----------                -----                   --------------
write row (pending)
task.delay(id) ---------> LPUSH list "celery"
return 201                 [message JSON]
                                    <------------- waits for a message
                           message leaves the list ->  run task(id)
                           (also tracked in             read row from
                            "unacked" while running)    Postgres, do work
                           SET celery-task-meta-<id> <- return value
                           (expires in 1 day)
Beat (inside the worker) every N seconds: send a message like any producer
```
{% endfig %}

## Walkthrough: one task through a real worker

All output in this section was captured by running a real Celery worker (Celery 5.6.2, kombu 5.6.2, redis-py 7.1.1) against a local Redis, logical database 12, using the project's own `celery_app` configuration, then deleting the keys. Four small demo tasks were added in a throwaway module (`add`, two that sleep for 5 seconds, and a `tick`), because the real tasks call external services. The real sweep and the real locks are run in Step 6. The demo module is not in the repository. The database 12 choice avoids clashing with other processes on the shared Redis.

### Step 1: the producer sends a message and no worker is running

```python
res = add.delay(2, 3)
```

Redis now holds one list and one set (captured):

```text
LLEN celery 1
keys: _kombu.binding.celery [set], celery [list]
headers {"lang": "py", "task": "guide.add", "id": "e44692b1-d986-4100-99be-ac1389ec16be",
         "retries": 0, "eta": null, "expires": null, "argsrepr": "(2, 3)", ...}
properties {"delivery_mode": 2, "delivery_info": {"exchange": "", "routing_key": "celery"},
            "priority": 0, "body_encoding": "base64"}
body (decoded) [[2, 3], {}, {"callbacks": null, "errbacks": null, "chain": null, "chord": null}]
```

The list is named `celery` because the project defines no other queue. The body is base64 of `[args, kwargs, options]`. `.delay()` returned immediately and the function has not run. If Redis were down, `.delay()` would raise at this line.

### Step 2: a worker starts and takes the message

The worker runs `celery -A app.core.celery_app worker --pool=solo`, as production does (production adds `--beat`). Its start banner (captured) shows what it connected to and how it will run tasks:

```text
- ** ---------- .> transport:   redis://localhost:6379/12
- ** ---------- .> results:     redis://localhost:6379/12
- *** --- * --- .> concurrency: 10 (solo)
```

The banner shows 10 (the CPU count) but solo runs one task at a time, which Step 4 confirms. After a moment `LLEN celery` is 0 and the result is stored (captured):

```text
celery-task-meta-e44692b1-d986-4100-99be-ac1389ec16be ->
  {"status": "SUCCESS", "result": 5, "traceback": null, "children": [],
   "date_done": "2026-10-26T06:14:21.790019+00:00", "task_id": "e44692b1-..."}  ttl 86396
```

Celery stores every task's return value for one day (the default `result_expires`). Nothing in the application reads these results, because task status lives in Postgres.

### Step 3: ack timing, observed

Celery's documented default is early ack: the worker acknowledges the message just before it runs the task, so a crash mid-task loses the task (at most once). The alternative, `acks_late=True`, acknowledges after the task ends, so a crash leaves the message to be redelivered (at least once). The project sets neither `task_acks_late` nor `task_reject_on_worker_lost`; the effective value is `False` (captured: `acks_late False`).

The code agrees: `Request.on_accepted` acknowledges when `not self.task.acks_late`, and reading `acknowledged` from inside a running task returned `True`. But Redis told a different story. While a 5-second early-ack task was running, `HLEN unacked` was 1 and the entry held that task. Same for an `acks_late` task (captured):

```text
early running: unacked ['guide.slow_early'] LLEN 0
early finished: unacked []
late running:  unacked ['guide.slow_late'] LLEN 0
late finished: unacked []
```

Then the worker was killed with SIGKILL during each kind of task and a new worker started. The message stayed in `unacked`, and neither task ran again in the 12 seconds observed:

```text
early-ack task: after SIGKILL: unacked 1 LLEN 0 | new worker 12 s later: task received again: False
acks_late task: after SIGKILL: unacked 1 LLEN 0 | new worker 12 s later: task received again: False
```

What this does and does not show. It shows that on this stack (Celery 5.6.2 solo pool with the Redis broker) an early-ack message stays in the `unacked` hash while the task runs, and a killed worker does not trigger quick redelivery in either mode. Kombu's Redis transport restores unacked messages after a visibility timeout, 1 hour by default (from reading its source; not waited out here). So what happens to a task killed mid-run is not "gone forever" but "possibly redelivered an hour later", for both modes. I did not wait an hour to confirm, and I could not explain why the early ack leaves the entry. Treat the "at most once" label as the documented Celery behaviour, not as something verified for this deployment.

### Step 4: one task at a time, and head-of-line blocking

A 5-second task was sent, then a trivial `add(1, 1)` half a second later (captured):

```text
t=2.0s LLEN celery 1 HLEN unacked 1
add state: PENDING
add finished at t=5.0s after submission at t=0.5s
```

The trivial task waited 4.5 seconds because the single worker was busy. This is head-of-line blocking: one slow task delays everything behind it in the single `celery` list. In production the slow tasks are PDF extraction and LLM-heavy analysis, and the fast ones are things like the sweeps and a user's tag edit.

### Step 5: beat

The worker was started with `--beat` and a demo schedule of one entry every 3 seconds (captured log):

```text
[23:14:43] INFO/Beat] beat: Starting...
[23:14:46] INFO/Beat] Scheduler: Sending due task guide-tick (guide.tick)
[23:14:46] INFO/MainProcess] Task guide.tick[e398f043-...] received
[23:14:46] INFO/MainProcess] Task guide.tick[e398f043-...] succeeded in 0.0057s: 'tick'
```

Beat sent a normal message; the worker ran it like any other. With `--beat` and the solo pool, Celery starts beat as a separate process under the worker. Because tasks run in the worker's own thread, a long task does not stop beat from sending, but the messages it sends wait in the list behind the long task. Earlier, with a 5-second task running, the list held ticks that had not run (observed in a first, discarded run).

### Step 6: the real sweep and the real lock

`recover_stale_pending_items` finds items stuck in `pending` for more than 10 minutes and redispatches extraction. The scratch database had one item inserted 20 minutes in the past. The sweep was run in-process with `.apply()`, so the queued extraction was not executed (captured):

```text
lock: first acquire True second False
  task_lock:demo_sweep  [string, ttl 600s]  1
sweep while lock held: {'recovered': 0, 'status': 'skipped', 'reason': 'lock_held'}
sweep, lock free     : {'recovered': 1}
LLEN celery 1
queued task: app.tasks.extraction.extract_metadata ('<the stuck item id>',)
lock key after sweep: []
```

The lock is one command, `SET task_lock:<name> 1 NX EX 600`: set only if absent, expire in 600 s. Exactly one caller gets `True`. The sweep releases it when it finishes.

### Step 7: the other Redis keys, captured through the real helpers

```text
llm_spend:user-demo:2026-10-26  [string, ttl 90000s]  0.0025
qemb:6f11ad822c258555           [string, ttl 3600s]   [0.1, 0.2, 0.3]
ratelimit:POST:/content:ip:203.0.113.7:minute:60  [zset, ttl 60s]
   [('1793048467.18:bf02bcc3', ...), ('1793048467.29:8ba57171', ...), ('1793048467.32:8ba5...', ...)]
```

The spend key holds a float incremented with `INCRBYFLOAT` after each paid call, with a 25-hour TTL. The query-embedding cache key is `qemb:` plus the first 16 hex characters of the SHA-256 of the lower-cased, trimmed query (the demo patched the embedding call; the vector is fake). The rate-limit key is a sorted set whose members are request timestamps. Both numbers `0.0025` and the timestamps are demo values.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/celery_app.py` | The Celery app, settings, `beat_schedule`, task imports, observability signals |
| `content-queue-backend/app/core/task_locks.py` | `acquire_run_lock` / `release_run_lock` (Redis `SET NX EX`) |
| `content-queue-backend/app/tasks/base.py` | `DatabaseTask`: a SQLAlchemy session per task, closed after return |
| `content-queue-backend/app/middleware/rate_limit.py` | Sliding-window limiter in Redis |
| `content-queue-backend/app/core/llm_client.py` | `check_budget` and `record_spend` |
| `content-queue-backend/app/core/embedding_cache.py` | Query-embedding cache |
| `content-queue-backend/app/mcp/oauth.py` | One-time auth codes and hashed refresh tokens in Redis |
| `content-queue-backend/app/tasks/` | One module per task family |

The configuration that matters:

<!--src content-queue-backend/app/core/celery_app.py-->
```python
task_serializer="json",
accept_content=["json"],
result_serializer="json",
timezone="UTC",
enable_utc=True,
task_track_started=True,
task_time_limit=30 * 60,  # 30 minutes max per task
task_soft_time_limit=25 * 60,  # Soft limit at 25 minutes
worker_prefetch_multiplier=1,  # Take one task at a time
worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
```

There is no `task_routes`, `task_queues`, `task_acks_late` or `ignore_result`, so there is one queue, early-ack semantics, and every result is stored. Under `--pool=solo` the two time limits and `worker_max_tasks_per_child` are not enforced (the ADR and `ARCHITECTURE.md` record this; I did not re-test it).

The beat schedule has ten entries:

| Task | Interval | Lock TTL | Lock released at end |
|---|---|---|---|
| `cleanup_old_deleted_items` | 24 h | none | n/a |
| `process_all_missing_embeddings` (highlights) | 5 min | 10 min | no |
| `process_all_missing_chunks` | 5 min | 10 min | no |
| `recover_stale_pending_items` | 5 min | 10 min | yes |
| `recover_orphaned_runs_task` | 5 min | 10 min | yes |
| `embed_new_entities_beat_task` | 1 h | 50 min | yes |
| `backfill_missing_entities_task` | 24 h | 20 h | yes |
| `consolidate_all_users_task` | 24 h | 20 h | no |
| `deduplicate_entities_task` | 7 d | 6 d | yes |
| `cluster_all_users_task` | 7 d | 6 d | no |

The lock itself:

<!--src content-queue-backend/app/core/task_locks.py-->
```python
key = f"task_lock:{task_name}"
acquired = r.set(key, "1", nx=True, ex=ttl_seconds)
return bool(acquired)
```

Tasks that fan out with `.delay()` and return at once deliberately keep the lock for its whole TTL, because the task cannot know when the dispatched work finishes. The consequence for the two 5-minute sweeps is that the lock outlives the schedule: a 10-minute lock with a 5-minute interval means every second firing is skipped, so these sweeps effectively run about every 10 minutes (inferred from the code; not run over time here).

Retries are written by hand in each task: catch the exception, then `raise self.retry(exc=e, countdown=60 * (2**self.request.retries))`, which waits 60, 120 and 240 seconds with `max_retries=3`. A retry only happens if the task raises. A task that catches an error and returns normally looks successful to Celery.

### Other uses of Redis, verified against the code

| Use | Key | TTL | Notes |
|---|---|---|---|
| Rate limit | `ratelimit:{METHOD}:{path}:{ip:addr}:{minute or hour}:{seconds}` | window length | Sliding window in a sorted set: drop old members, count, add. The identifier is always the client IP: the middleware reads `request.state.user`, which nothing in `app/` sets. Fails open if Redis is unreachable. Covers four routes (see chapter 4.3). |
| Daily LLM spend | `llm_spend:{user_id}:{YYYY-MM-DD}` | 25 h | `check_budget` before a call, `record_spend` after; fails open |
| Query embedding cache | `qemb:{sha256[:16]}` | 1 h | Key excludes the model name |
| Insight cache | `insight:{highlight_id}:{article_id}` | 7 d | Written from `api/search.py`; no invalidation |
| MCP authorization code | `mcp:code:{code}` | 300 s | One-time PKCE code |
| MCP refresh token | `mcp:refresh:{sha256(token)}` | `MCP_REFRESH_TOKEN_EXPIRE_DAYS` (30) | Only the hash is stored; consumed with `GETDEL` so a token works once |

### Production setup

From `ARCHITECTURE.md` (2026-10-25, not re-verified against the live service): the worker service starts `celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat`. The `--concurrency=2` has no effect under solo. `PREFECT_ENABLED=false` on the worker and API, and the Prefect service is stopped; ingestion runs as a Celery chain. The worker sets `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0` so the old container stops as the new one starts: the previous overlap produced an 840 MB peak and let two embedded beat schedulers run at once. The API keeps the default overlap.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Celery with a Redis broker | A Postgres-backed queue; RQ or Dramatiq; cloud queues such as SQS | Redis was already needed for rate limits and caches; Celery gives retries, schedules and routing. Cost: broker semantics are subtle, as Step 3 showed. |
| Send ids, not data | Send the article | Messages stay tiny and the worker reads current state. A lost message costs a redispatch. |
| `--pool=solo` | `prefork` with concurrency 1 or 2; a separate service for heavy work | Lowest memory (one process of roughly 300 MB, per the ADR `docs/decisions/0007-celery-memory-ml-tasks.md`). Cost: no parallelism, no enforced time limits. |
| Beat embedded in the worker | A separate beat service | One service to run. Cost: two worker containers during a deploy overlap mean two schedulers, which is why overlap is set to 0. |
| Sweeps as repair | `acks_late`; a dead-letter queue | Idempotent and simple: redispatch whatever Postgres says is unfinished. Cost: only covers states the sweeps look at (stuck `pending`, missing chunks, missing embeddings). |
| `SET NX EX` locks that expire | Locks with owner tokens and compare-and-delete | One command, no cleanup. Cost: the value is a constant, so a slow run can have its lock expire and another start. |
| Redis for limits, budget and caches | In-process counters; Postgres | Shared across instances and self-expiring. Cost: all of it fails open when Redis is unreachable. |
| One queue | Separate interactive and bulk queues | Nothing to configure. Cost: head-of-line blocking. |

## How it fails

| Failure | What causes it | What happens | What was done |
|---|---|---|---|
| Message never sent | Redis unreachable when the API calls `.delay()` | The call raises; the row stays `pending` | `recover_stale_pending_items` redispatches after 10 minutes |
| Worker killed mid-task | Deploy, out-of-memory kill | The row can stay `processing`; the message sits in `unacked` (Step 3) | No sweep covers `processing`; a timed sweep is the fix |
| Worker blocked | A long or hung task under solo | Everything behind it waits, including beat messages; time limits are not enforced | Restart; a pool change or watchdog would be needed |
| Duplicate schedulers | Two containers during a deploy overlap | Schedules fire twice | Overlap set to 0 for the worker; locks stop most duplicate sweeps |
| Sweep skipped | Lock TTL longer than the interval | The 5-minute sweeps run about every 10 minutes | Not changed |
| Redis wiped | Restart without persistence (the local compose file disables it) | Queue, locks, counters and caches vanish, Postgres is intact | Sweeps rebuild pending work |
| Redis rate-limit outage | `ping` fails | Limits and budget checks are skipped | Deliberate fail-open |
| Backlog explosion | A per-article dispatch of an entity task | A 6.5-million-message backlog found in chaos testing | The dispatch was replaced by an hourly sweep |

## What is weak or missing

Ranked by how likely each is to matter.

1. **Delivery semantics are not what the docs say.** Step 3 contradicts "at most once, killed means gone". Until the one-hour redelivery is tested, nobody can say whether a crashed task runs again. Fix: test it, then decide on `acks_late` for idempotent tasks plus a sweep for `processing` items.
2. **One queue and one task at a time.** A PDF extraction delays every sweep and every user's tag edit. Fix: an interactive queue and a bulk queue, with the worker told which to read first.
3. **Time limits are not enforced.** A hung fetch blocks the only worker, and beat with it. Fix: `prefork` with concurrency 1, or an external watchdog.
4. **Results are stored and never read.** Small (about 100 to 500 bytes each), but pure waste. Fix: `task_ignore_result=True`.
5. **Rate limits are per IP, and the check is two non-atomic pipelines.** Behind a proxy the client address may be shared. See chapter 4.3.
6. **Every helper opens a new Redis connection and pings it,** four near-identical copies. A shared client with a connection pool would remove that and the up-to-1-second block when Redis is down.
7. **Queue, caches, locks and counters share one Redis,** so one eviction policy has to serve a queue (which must not evict) and caches (which may). Not recorded in the repository.

{% say %}
When you save a URL the API writes a row to Postgres and drops a tiny message with the item id into a Redis list, then answers right away. A separate Celery worker process pulls messages off that list and runs the function, looking up details in Postgres. Celery beat, running inside the same worker, sends scheduled messages; the important ones are sweeps that find stuck or missing work in Postgres and redispatch it, protected by a Redis lock so two runs do not overlap. The worker runs with the solo pool, one task at a time, to keep memory low, so a slow task delays everything behind it in the single queue. Redis also holds rate limits, the daily spend counter and caches. The weak spots are the single queue, no enforced time limits, and delivery semantics I measured and could not fully explain: a killed worker's message stays in Redis's unacked set.
{% endsay %}

## Check yourself

{% qa "What is in a task message, and why does that make losing one cheap?" %}
The task name, an id, the retry count and arguments such as an item id, never the article text. The worker reads the row from Postgres, so a lost message costs one redispatch, and the sweeps can regenerate it from database state.
{% endqa %}

{% qa "Why does a 10-minute lock on a task scheduled every 5 minutes matter?" %}
If the task does not release the lock when it ends, the lock is still held at the next firing, which is skipped. The sweep effectively runs every 10 minutes. The two missing-embeddings sweeps behave this way because they dispatch work with `.delay()` and return at once.
{% endqa %}

{% qa "A 5-second task is running and a quick task is queued behind it. What happens under solo, and what are two ways to change it?" %}
The quick task waits for the 5-second one to finish (measured: it finished 4.5 s after it was submitted). Options: separate queues with workers reading the interactive one first, or a pool with more than one process at the cost of more memory.
{% endqa %}

{% qa "What does early ack promise, and what did the measurement show on this stack?" %}
Early ack acknowledges before running, so a crash loses the task (at most once). The code does acknowledge first, yet the message stayed in Redis's `unacked` hash while the task ran and after a SIGKILL, with no quick redelivery. So the promise is not confirmed for this deployment, and redelivery after the visibility timeout was not tested.
{% endqa %}

{% qa "What does beat do, and what happens to its messages if the worker is busy?" %}
Beat only sends task messages on a schedule. If the single worker is busy, those messages queue behind the running task and execute afterwards, so a long task postpones all sweeps.
{% endqa %}

{% qa "Why does the rate limiter treat Redis being down as 'allowed'?" %}
The code treats Redis as already a hard dependency of the queue and prefers not to add a second failure that blocks users. The cost is that during an outage nothing is limited and the LLM budget is not checked.
{% endqa %}
