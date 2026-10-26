---
type: system-design
status: draft
last_updated: 2026-10-24
consumer: human
---

# Redis and Celery in sed.i

Goal: after reading this you can explain what Redis and Celery are, how this project uses each, what is stored where, and what happens when parts fail, to someone who has never used either.

How to read the "data in Redis" examples: no Redis instance was reachable from the environment where this was written, so every key and value shown is **constructed from the code** (key formats, TTLs and value shapes are taken from the source files cited) and not captured from a live instance. Section 10 gives the commands to capture the real thing. Celery's internal key names (section 3.5) come from Celery/kombu behaviour, not from this repo; verify them with section 10 on your instance.

---

## 1. The one-paragraph version

**Redis** is a database that keeps its data in memory and is very fast. It stores simple structures (strings, lists, sets, sorted sets, hashes) under names called keys, and any key can be given an expiry time. **Celery** is a Python library that lets a web request say "do this slow thing later, somewhere else." The API drops a small message into a Redis list; a separate Python process (the Celery worker) takes messages off that list and runs the matching Python function. In this project Redis therefore plays two roles at once: it is **Celery's mailbox** (the queue) and a **shared scratchpad** (rate limits, locks, spend counters, caches, short-lived login tokens). Postgres remains the source of truth for all real data.

```
 Browser ──HTTP──> API process (uvicorn, FastAPI)
                      │  1. write row to Postgres (status=pending)
                      │  2. extract_metadata.delay(item_id)  ──> push JSON message
                      ▼                                          ┌────────────────────┐
                 returns 201                                     │ Redis              │
                                                                 │  list "celery"     │
 Celery worker process (python) <──── pops message ──────────────│  ratelimit:* ...   │
   runs extract_metadata(item_id)                                │  task_lock:* ...   │
   reads/writes Postgres, calls OpenAI                           └────────────────────┘
```

---

## 2. Redis from scratch

### 2.1 What it is

- A server process (`redis-server`) that listens on a TCP port (default 6379). Clients (your Python code) send it commands and get replies.
- Data lives **in RAM**. Reads and writes take well under a millisecond, which is why it is used for things that are checked on every request.
- It is **single-threaded for command execution**: one command runs at a time, start to finish. That is the source of Redis's most useful property: every individual command is **atomic**. Two clients cannot interleave halfway through an `INCR`.
- It is a **key → value** store, where the value has a type.

### 2.2 The data types this project uses

| Type | What it is | Commands | Used here for |
|---|---|---|---|
| String | A blob of bytes (text, number, JSON) | `SET`, `GET`, `SETEX`, `INCRBYFLOAT` | Locks, spend counters, caches, OAuth codes |
| List | Ordered sequence; push at one end, pop at the other | `LPUSH`, `RPUSH`, `BRPOP`, `LLEN` | The Celery queue |
| Sorted set (ZSET) | Set of unique members, each with a numeric score, kept sorted by score | `ZADD`, `ZCARD`, `ZREMRANGEBYSCORE`, `ZRANGE` | Rate limiting (score = timestamp) |
| Hash | A key holding named fields (like a small dict) | `HSET`, `HGET`, `HGETALL` | Celery's list of in-flight messages |
| Set | Unordered unique members | `SADD`, `SMEMBERS` | Celery's queue bindings |

### 2.3 Expiry (TTL)

Any key can have a time-to-live. After it elapses, Redis deletes the key automatically.

```
SETEX qemb:ab12 3600 "[0.1, 0.2]"   # create with 1-hour TTL
TTL qemb:ab12                        # seconds left, -1 = no expiry, -2 = key gone
EXPIRE somekey 60                    # add/refresh a TTL
```

Nearly everything in this project relies on TTLs to clean up after itself. A key without a TTL lives until deleted (or evicted, see 2.6).

### 2.4 Atomic "claim" commands

`SET key value NX EX 600` means: set only if the key does **Not eXist**, and expire in 600 s. It returns OK to exactly one caller even if 50 call at once. That is a lock. The project's overlap guard is this one command ([task_locks.py](../../../content-queue-backend/app/core/task_locks.py)).

`INCRBYFLOAT key 0.0004` adds to a number atomically. That is how spend is accumulated.

### 2.5 Pipelines

A pipeline sends several commands in one network round trip. It is **not** a transaction: other clients' commands can run between the commands of a pipeline unless it is wrapped in `MULTI/EXEC`. The rate limiter uses two pipelines, which matters for correctness (section 6.3).

### 2.6 Memory limits and eviction

Redis is bounded by RAM. `maxmemory-policy` decides what happens when the limit is reached:
- `noeviction`: new writes fail with an error. Right for a queue, because silently dropping queued tasks would lose work.
- `allkeys-lru`: delete the least recently used keys to make room. Right for a pure cache, **wrong for a queue**.

This project stores queue and cache data in the same Redis instance, so one policy has to serve both. Which policy the production instance uses is not recorded in the repo (check: `CONFIG GET maxmemory-policy`).

### 2.7 Persistence

Redis can write its memory to disk so it survives a restart:
- **RDB**: periodic snapshot (`save 900 1` = snapshot if at least 1 change in 900 s). Can lose the last few minutes.
- **AOF** (append-only file): logs each write; can lose about one second with `appendfsync everysec`.
- **None**: restart gives an empty Redis.

Local dev in this repo runs with no persistence (`redis-server --save "" --appendonly no` in `docker-compose.yml`). Production persistence is not recorded in the repo.

### 2.8 What Redis is not

It is not the system of record and not a queue with strong guarantees by itself. Delivery guarantees come from how the client (Celery) uses it (section 3.6).

---

## 3. Celery from scratch

### 3.1 What it is

Celery is a **Python library** (version 5.6 here). You decorate a function with `@celery_app.task`, and from then on you can call `my_task.delay(args)` to run it elsewhere instead of in-process.

Three roles:
1. **Producer**: any code that calls `.delay()`. Here: the FastAPI process, and other tasks.
2. **Broker**: the middleman that holds messages. Here: Redis.
3. **Worker**: a process that takes messages and executes the function. Here: a Python process started with `celery -A app.core.celery_app worker ...`.

Answers to "is the worker a Python process?": yes. It is an ordinary Python interpreter that imports your app code (`app.core.celery_app` imports every task module), connects to the broker, and loops: wait for a message, run the function, repeat. It shares nothing with the API process except the code base, Postgres and Redis.

### 3.2 Beat

**Celery beat** is a clock. It holds the `beat_schedule` in [celery_app.py](../../../content-queue-backend/app/core/celery_app.py) and, when a schedule's time comes, **sends an ordinary task message** into the same queue. It does not run the task itself. A worker then picks it up like any other message.

Here beat is started inside the worker process (`--beat` on the worker command line, per ADR-0007). Consequences: one process does both jobs; if the worker is blocked by a long task, beat cannot fire; and starting a second worker with `--beat` would fire every schedule twice.

Beat remembers when it last fired in a local file (`celerybeat-schedule`), not in Redis. A fresh container without that file starts fresh.

### 3.3 Pools: how a worker runs tasks

| Pool | What it does | Memory | Time limits |
|---|---|---|---|
| `prefork` (default) | Spawns N child processes, each runs tasks | N × full app memory | Enforced (kills child) |
| `threads` / `gevent` | Many tasks inside one process | ~1× | Soft only / cooperative |
| `solo` (**used here**) | One task at a time in the main process | ~1× | **Not enforced** |

`--concurrency=2` has no effect under `solo`. See ADR-0007 for why `solo` was chosen (memory).

### 3.4 Task lifecycle, step by step

```python
# producer (API)
extract_metadata.delay(str(item.id))
```

1. Celery builds a message: task name, arguments, a fresh task id, retry count, optional ETA. It serializes it as JSON (`task_serializer="json"` in config).
2. Celery pushes it onto the Redis list named `celery` (the default queue; this project defines no other).
3. `.delay()` returns immediately with an `AsyncResult` handle. **The function has not run.** If Redis is unreachable, `.delay()` raises.
4. A worker, blocked waiting on the list, receives the message.
5. The worker **acknowledges** the message (see 3.6), then runs `extract_metadata(item_id)` in its pool.
6. When the function returns, Celery stores the return value in Redis as a "result" (section 3.5).
7. If the function raises and the task calls `self.retry(countdown=60)`, Celery sends a **new message** for the same task id with a later ETA and an incremented retry count.

### 3.5 What Celery keeps in Redis (internal)

These are Celery/kombu conventions, not defined in this repo. Verify with section 10.

| Key | Type | Meaning |
|---|---|---|
| `celery` | list | The queue: one JSON message per waiting task |
| `unacked` | hash | Messages a worker received but has not acknowledged yet |
| `unacked_index` | sorted set | Same, ordered by delivery time (for redelivery after the visibility timeout) |
| `_kombu.binding.celery` | set | Routing binding for the queue |
| `celery-task-meta-<task_id>` | string | The stored **result** of a finished task (JSON: status, return value or traceback) |

A queued message looks roughly like this (shortened; `body` is base64 of `[args, kwargs, embed]`):

```json
{
  "body": "W1siYTFiMmMzLi4uIl0sIHt9LCB7...}",
  "headers": {
    "task": "app.tasks.extraction.extract_metadata",
    "id": "8f1c0f4e-6a3d-4b6e-9a7e-0d3f7a2b1c55",
    "retries": 0,
    "eta": null
  },
  "properties": { "delivery_tag": "…", "priority": 0 }
}
```

Decoded, the useful part is: *call `extract_metadata` with `item_id = "a1b2c3…"`*. Only the id is sent, never the article. The worker re-reads the row from Postgres. This is why queue messages are tiny and why losing one costs only a re-dispatch.

### 3.6 Acknowledgement: where "at-most-once" comes from

An acknowledgement (ack) tells the broker "I have this message; you can forget it."

- **Early ack (Celery default; used here):** the worker acks right before running the task. If the process dies mid-task, the broker believes the work is done and will not redeliver. A task runs 0 or 1 times: **at-most-once**.
- **Late ack (`task_acks_late=True`):** the worker acks after the task finishes. If the process dies mid-task, the message stays in `unacked`; when the broker notices the worker is gone (connection loss, or after the Redis `visibility_timeout`, default 1 hour), it puts the message back on the queue. A task can run 1 or more times: **at-least-once**. Only safe if running twice does no harm.

This repo sets neither `task_acks_late` nor `task_reject_on_worker_lost`, so every task is early-ack.

### 3.7 Retries and ETA

`self.retry(exc=e, countdown=60 * 2**n)` re-sends the task with `eta = now + countdown`. With the Redis broker, the worker holds messages with a future ETA in its own memory until due. Retries here are 60 s, 120 s, 240 s with `max_retries=3` (none with jitter). A retry only happens if the task function **raises**; a task that catches the exception and returns normally looks successful to Celery (this caused dead retries fixed on 2026-07-28).

### 3.8 Results

By default Celery stores every task's return value in Redis under `celery-task-meta-<id>`, for 1 day (`result_expires` default). This project passes `backend=settings.REDIS_URL` and sets neither `ignore_result` nor `result_expires`. Nothing in the app reads those results (status lives in Postgres), so this is memory spent for no benefit. Offline measurement (`docs/audits/2026-10-24-memory-measurements.md`) puts a result at 121–509 bytes plus a ~53-byte key, so it is a minor contributor; set `ignore_result` for tidiness, not for the memory bill.

---

## 4. How the project wires them

[celery_app.py](../../../content-queue-backend/app/core/celery_app.py):

```python
celery_app = Celery("content_queue", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    task_track_started=True,
    task_time_limit=30*60, task_soft_time_limit=25*60,   # not enforced under solo
    worker_prefetch_multiplier=1,                        # take one message at a time
    worker_max_tasks_per_child=1000,                     # no effect under solo
    beat_schedule={ ... 10 entries ... },
)
```

Processes in production (Railway):

| Service | Command | Talks to |
|---|---|---|
| API | `uvicorn app.main:app` (via nixpacks start) | Postgres, Redis, OpenAI |
| Worker | `celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat` | Postgres, Redis, OpenAI, arbitrary websites |
| Redis | managed instance | |
| Postgres | managed instance | |

One Redis instance, one database number (from `REDIS_URL`), shared by every use below.

`worker_prefetch_multiplier=1` matters: by default a worker reserves several messages ahead. Setting 1 means it takes the next only when free, so one slow task does not hold other messages hostage in its local buffer.

---

## 5. Key catalog: everything this project puts in Redis

| Key pattern | Type | TTL | Written by | Read by | If lost |
|---|---|---|---|---|---|
| `celery` | list | none | `.delay()`, beat | worker | Queued tasks lost; sweeps recover most |
| `unacked`, `unacked_index` | hash, zset | none | Celery | Celery | In-flight message bookkeeping lost |
| `celery-task-meta-<id>` | string | 1 day (default) | worker | nobody in app | Nothing |
| `ratelimit:{route}:{who}:{minute\|hour}:{window}` | zset | window length | rate-limit middleware | same | Limits reset (more permissive) |
| `task_lock:{task_name}` | string | per task (10 min – 6 days) | beat-triggered tasks | same | Overlap guard disappears |
| `llm_spend:{user_id}:{YYYY-MM-DD}` | string (float) | 25 h | every LLM/embedding call | budget check | Daily spend counter resets |
| `qemb:{sha256[:16]}` | string (JSON array) | 1 h | search | search | Cache miss, one more embedding call |
| `insight:{highlight_id}:{article_id}` | string | 7 d | insight endpoint | same | Regenerates (LLM call) |
| `mcp:code:{code}` | string (JSON) | 5 min | MCP authorize | MCP token exchange | In-flight MCP login fails once |
| `mcp:refresh:{sha256(token)}` | string (JSON) | `MCP_REFRESH_TOKEN_EXPIRE_DAYS` | MCP token endpoint | MCP token endpoint | MCP clients must log in again |

---

## 6. Each use, with code and data

### 6.1 The task queue

Code ([services/content.py](../../../content-queue-backend/app/services/content.py)):

```python
item = ContentItem(user_id=user.id, original_url=normalized, processing_status="pending")
db.add(item); db.commit()
extract_metadata.delay(str(item.id))     # push message to Redis list "celery"
```

State in Redis, as seen by `redis-cli` (constructed):

```
> LLEN celery
(integer) 3                       # three tasks waiting
> LRANGE celery 0 0               # peek the next message (JSON as above)
> HLEN unacked
(integer) 1                       # one message received by the worker, not yet acked
```

Because there is a single list and a single FIFO order, a long task ahead of yours delays yours. Two queues would be two lists (`celery`, `bulk`) in the same Redis, with workers told which to read first.

### 6.2 Beat sweeps and their locks

Several beat tasks are "sweeps": every N minutes they scan Postgres for work that should have happened and did not, and re-dispatch it. Example ([extraction.py](../../../content-queue-backend/app/tasks/extraction.py), `recover_stale_pending_items`, every 5 min):

```python
if not acquire_run_lock("recover_stale_pending_items", ttl_seconds=600):
    return {"status": "skipped", "reason": "lock_held"}
try:
    stale = db.query(ContentItem).filter(status == "pending", created_at < now - 10min).all()
    for item in stale:
        extract_metadata.delay(str(item.id))
finally:
    release_run_lock("recover_stale_pending_items")
```

The lock exists so that two overlapping runs of the same sweep do not both see the same pending item and dispatch it twice ("beat sweep lock").

```python
# core/task_locks.py
r.set(f"task_lock:{task_name}", "1", nx=True, ex=ttl_seconds)    # acquire
r.delete(f"task_lock:{task_name}")                              # release
```

Data (constructed):

```
> SET task_lock:recover_stale_pending_items 1 NX EX 600
OK                                  # first caller wins
> SET task_lock:recover_stale_pending_items 1 NX EX 600
(nil)                               # second caller loses; the sweep skips this run
> TTL task_lock:recover_stale_pending_items
(integer) 587
```

Locks in use: `cluster_all_users_task` (6 d), `consolidate_all_users_task` (20 h), `process_all_missing_embeddings`, `process_all_missing_chunks`, `recover_stale_pending_items`, `recover_orphaned_runs_task` (10 min each), `embed_new_entities_beat_task` (50 min), `backfill_missing_entities_task` (20 h), `deduplicate_entities_task` (6 d).

Limits of this lock (value is a constant `"1"`; release is an unconditional delete; fails open when Redis is down) are covered in the distributed-systems guide §3.7.

### 6.3 Rate limiting

Code ([middleware/rate_limit.py](../../../content-queue-backend/app/middleware/rate_limit.py)): sliding-window log. For each request to a limited route, per window (60 s and 3600 s):

```python
key = f"ratelimit:{identifier}:{window_seconds}"
pipe.zremrangebyscore(key, 0, now - window)    # drop timestamps older than the window
pipe.zcard(key)                                # count what is left
if count >= max_requests: return False         # reject -> HTTP 429
pipe.zadd(key, {f"{now}:{random4bytes}": now}) # record this request
pipe.expire(key, window)                       # self-cleaning
```

Data after three `POST /content` requests from one caller (constructed; `identifier` = `"POST:/content:ip:203.0.113.7:minute"`):

```
> ZRANGE "ratelimit:POST:/content:ip:203.0.113.7:minute:60" 0 -1 WITHSCORES
1) "1761300000.1203:9f2a01bc"   2) "1761300000.1203"
3) "1761300004.8841:03dd7e12"   4) "1761300004.8841"
5) "1761300011.0270:77ab4c90"   6) "1761300011.0270"
> ZCARD ...                       # 3 requests in the window
> TTL ...                         # <= 60
```

Why a sorted set: the score is a timestamp, so "remove everything older than now − 60 s" is one range delete, and the remaining cardinality is exactly "requests in the last 60 s". The window slides continuously instead of resetting on the minute.

Things found while reading this for the doc (code-read, not tested):
1. **The identifier is always the client IP.** The middleware checks `hasattr(request.state, "user")`, but nothing in `app/` ever sets `request.state.user` (the only match is this read). The middleware also runs before FastAPI resolves auth. So `user:{id}` is never used, and the "per user" limits documented in ARCHITECTURE.md §8 are per IP in practice. Behind Cloudflare and Railway, `request.client.host` may be a proxy address shared by all users unless trusted-proxy headers are configured (check: log `request.client.host` in production). In the worst case every visitor shares one bucket per route.
2. **Check and record are two separate pipelines.** Between them another request can pass the check, so limits can be exceeded by the number of concurrent requests. A single Lua script makes it atomic.
3. **A new Redis client and a `ping` per check.** `_get_redis_client()` calls `from_url` and `ping()` every time, so each limited request opens new connections (2 limits × connect + ping + 2 pipelines).
4. **Blocking calls in async code.** `is_allowed` is `async` but uses the synchronous client. Each call blocks the event loop. If Redis is down, each call can block up to `socket_connect_timeout` (1 s) before failing open, and those waits stall every other request on that API process.

### 6.4 LLM daily spend counters

Code ([llm_client.py](../../../content-queue-backend/app/core/llm_client.py)):

```python
def _spend_key(user_id): return f"llm_spend:{user_id}:{utc_date:%Y-%m-%d}"

def check_budget(user_id):                       # before each call
    spent = r.get(_spend_key(user_id))
    if spent is not None and float(spent) >= settings.LLM_DAILY_BUDGET_USD_PER_USER:
        raise BudgetExceededError(...)           # tasks log + skip, never retry

def record_spend(user_id, cost_usd):             # after each call
    r.incrbyfloat(key, cost_usd)
    r.expire(key, 25 * 3600)                     # outlives one UTC day, then vanishes
```

Data (constructed):

```
> GET llm_spend:7c9e6679-7425-40de-944b-e07fc1f90ae7:2026-10-24
"0.0123450000000000"        # dollars spent today by this user
> TTL llm_spend:7c9e...:2026-10-24
(integer) 88231
```

One key per user per day; the date is in the key, so "reset at midnight UTC" needs no cleanup job, and the 25-hour TTL removes stale keys. Check and record are separate commands, so concurrent calls can overshoot slightly (bounded by concurrency × cost per call). If Redis is unreachable the check is skipped.

### 6.5 Query-embedding cache

```python
key = "qemb:" + sha256(query.lower().strip()).hexdigest()[:16]
cached = redis.get(key)
if cached: return json.loads(cached)
embedding = call_embed(query, user_id=user_id)      # paid OpenAI call
redis.setex(key, 3600, json.dumps(embedding))        # keep for 1 hour
```

Data: key `qemb:3fa9c1b27d04e855`, value a JSON array of 1,536 floats (roughly 20–30 KB as text). The cache is a pure function of the text (no user data), so it is safe to share across users. The key does not include the model name, so changing the embedding model would serve old-model vectors for up to an hour.

### 6.6 Insight cache

`insight:{highlight_id}:{article_id}` → the one-sentence LLM insight as a string, TTL 7 days ([api/search.py](../../../content-queue-backend/app/api/search.py)). No invalidation when the highlight or article changes. Failures return `null` and are not cached.

### 6.7 MCP OAuth (login for Claude clients)

```python
r.setex(f"mcp:code:{code}", 300, json.dumps({"email": ..., "code_challenge": ..., "redirect_uri": ..., "client_id": ...}))
```

A one-time authorization code that lives 5 minutes; the token endpoint reads it, checks the PKCE verifier, and issues a JWT. Refresh tokens are stored as `mcp:refresh:{sha256(token)}` (only the hash is in Redis, so a Redis dump does not leak usable tokens) with value `{"email", "client_id"}`.

---

## 7. End to end: saving a URL, with Redis state

1. **Browser → API** `POST /content`. Rate-limit middleware: 2 × `ZREMRANGEBYSCORE` + `ZCARD` + `ZADD` on `ratelimit:POST:/content:...`.
2. **API** inserts the row (`pending`) and commits to Postgres.
3. **API** `extract_metadata.delay(id)` → `LPUSH celery <json>`. Returns 201 to the browser. `LLEN celery` is now 1 higher.
4. **Worker** pops the message. Early ack: the message leaves `unacked`.
5. **Worker** sets `processing`, fetches the page, writes metadata and `full_text`, sets `completed`.
6. **Worker** `generate_embedding.delay(id)` → another message on `celery`. When that runs: `check_budget` → `GET llm_spend:...`; the OpenAI call; `record_spend` → `INCRBYFLOAT`.
7. **Worker** on finish: `SET celery-task-meta-<id> {...}` (result nobody reads), expires in a day.
8. **Every 5 min, beat** (in the same worker process) enqueues `recover_stale_pending_items`; its run does `SET task_lock:... NX EX 600`, scans Postgres, `DEL`s the lock.
9. **Browser** polls `GET /content/{id}`: reads Postgres only. Redis is not involved in reads of content.

---

## 8. Failure scenarios

| Event | What happens | Recovery |
|---|---|---|
| Redis unreachable when API calls `.delay()` | `.delay()` raises; row stays `pending` | `recover_stale_pending_items` re-dispatches after 10 min (if the API swallows or the request fails after commit; the row exists either way) |
| Redis restarts without persistence | `celery` list and counters vanish | `pending` and unembedded items recovered by sweeps; items that were mid-`processing` are **not** recovered |
| Worker killed mid-task (OOM, deploy) | Message was acked early → gone | Row stays `processing` forever until a sweep for `processing` exists |
| Worker blocked by a hung fetch | Time limits not enforced under solo; beat shares the process, so no sweeps run either | Manual restart |
| Redis out of memory with `noeviction` | Writes fail: `.delay()`, rate limiter and cache writes error | Rate limiter and budget fail open; `.delay()` raises |
| Redis out of memory with an LRU policy | Redis may evict queued messages | Silent task loss; sweeps recover some |
| Second worker started with `--beat` | Every schedule fires twice | Locks prevent most duplicate sweeps; others run twice |

---

## 9. Observations for follow-up

| Observation | Evidence | Suggested change |
|---|---|---|
| Rate limit is always per IP, never per user | `request.state.user` is never set | Resolve user in middleware (decode JWT) or move the check into a dependency; read the real client IP via trusted proxy headers |
| Task results stored but unused | no `ignore_result`, no `result_expires` | `task_ignore_result=True` (or a short `result_expires`) |
| Queue, cache, locks and counters share one Redis | one `REDIS_URL` | Split: broker with `noeviction` + persistence; cache with LRU |
| Per-call Redis connections | `from_url` + `ping` in four helper copies (`rate_limit.py`, `task_locks.py`, `llm_client.py`, `hybrid_search`) | One module-level client with a connection pool |
| Sync Redis in async middleware | `async def is_allowed` using `redis.Redis` | Use `redis.asyncio` or run in a threadpool |
| Early ack with no `processing` sweep | no `task_acks_late`; sweep filters `pending` only | `acks_late` for idempotent tasks; sweep `processing` older than the task limit |
| Single queue | no `task_routes` | `interactive` and `bulk` queues |

---

## 10. Inspecting the real thing

With `redis-cli` (install with `brew install redis`, then use the production URL from Railway; read-only commands only):

```
redis-cli -u "$REDIS_URL" INFO server | grep redis_version
redis-cli -u "$REDIS_URL" INFO memory | egrep "used_memory_human|maxmemory_human|maxmemory_policy"
redis-cli -u "$REDIS_URL" CONFIG GET appendonly        # AOF on?
redis-cli -u "$REDIS_URL" CONFIG GET save              # RDB snapshot rules
redis-cli -u "$REDIS_URL" DBSIZE                       # number of keys
redis-cli -u "$REDIS_URL" LLEN celery                  # queue depth
redis-cli -u "$REDIS_URL" HLEN unacked                 # in-flight messages
redis-cli -u "$REDIS_URL" --scan --pattern 'celery-task-meta-*' | wc -l   # stored results
redis-cli -u "$REDIS_URL" --scan --pattern 'ratelimit:*' | head           # who is being limited, and by what identifier
redis-cli -u "$REDIS_URL" --scan --pattern 'task_lock:*'
redis-cli -u "$REDIS_URL" --bigkeys                    # largest keys by type (samples; use off-peak)
redis-cli -u "$REDIS_URL" MEMORY USAGE some:key        # bytes for one key
redis-cli -u "$REDIS_URL" TTL some:key
```

Avoid `KEYS *` on production (it blocks the single thread); use `--scan`.

From Celery:

```
celery -A app.core.celery_app inspect active       # tasks running now
celery -A app.core.celery_app inspect reserved     # tasks received, not started
celery -A app.core.celery_app inspect scheduled    # ETA/retry tasks waiting
celery -A app.core.celery_app inspect stats        # pool type, max-concurrency, memory
```

Two checks that settle open questions: (1) `ratelimit:*` keys containing `ip:` addresses that are all the same value confirm the shared-bucket problem; (2) `inspect stats` showing `pool.max-concurrency: 1` confirms `--concurrency=2` is ignored.

---

## 11. Teaching summary (explain it to someone new)

**What is Redis?** A very fast key-value database in RAM with a few data structures and automatic expiry. Good for state that is temporary, shared between processes, and checked constantly.

**What is Celery?** A Python library that moves slow work out of web requests. A producer pushes a small message; a worker process pulls it and runs the function.

**How do they fit together?** Celery needs somewhere to put the messages. That is the broker, and here the broker is Redis. Redis is where the messages wait; Celery is the code on each side that puts them in and takes them out.

**What is a message?** JSON: task name, arguments (an item id), task id, retry count.

**What is the worker?** A Python process that imported the app, connects to Redis, and loops running tasks. Here it is started with `--pool=solo`, so it runs one task at a time, and with `--beat`, so the scheduler runs inside it.

**What is beat?** A scheduler that enqueues messages on a timer. It is a producer, not an executor.

**What is a sweep?** A scheduled task that looks in Postgres for things that should be done but are not, and re-dispatches them. It is the repair mechanism for lost messages.

**Why a lock on sweeps?** So two overlapping runs do not both dispatch the same work.

**What is the rate limiter in Redis terms?** One sorted set per caller, route and window; members are request timestamps; count the members in the last N seconds.

**What is the spend counter?** One float per user per day, incremented after each paid call, deleted by TTL after 25 hours.

**What happens if Redis is wiped?** No data is lost (Postgres has it). Queued work, rate limits, locks, caches and spend counters reset. Sweeps rebuild most pending work, except tasks that were mid-run.

**What are the delivery guarantees?** Early ack: a task runs at most once per message. A crash during a task loses it. At-least-once needs `acks_late`, which requires tasks to be safe to run twice.

**Where are the weak spots?** Section 9.
