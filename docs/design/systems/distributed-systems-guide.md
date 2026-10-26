---
type: system-design
status: draft
last_updated: 2026-10-23
consumer: human
---

# sed.i — Distributed Systems Design Guide

Audience: the author, preparing for system-design interviews and for the next scaling steps.
Scope: how the current system maps onto the standard distributed-systems concerns, what is missing, what is immature, and what to build next.

Method and confidence:
- Code read 2026-10-23: `ARCHITECTURE.md`, `celery_app.py`, `task_locks.py`, `database.py`, `rate_limit.py`, `services/content.py`, `tasks/extraction.py`, `main.py`, `railway.json`, `cloudflare-worker/worker.js`, ADR-0002/0005, the 2026-07-28 failure-injection changelog, and `docs/audits/2026-10-12-production-readiness.md`.
- **Confirmed** = seen in code or committed docs. **Inferred** = follows from confirmed facts but not tested. **Unverified** = needs a check against the live Railway/Cloudflare setup.
- Numbers in §9 are assumptions for illustration, not measurements. Replace them with measured values before quoting them.

---

## 1. The system on one page

```
Browser ─┐                                    ┌──────────────────────────────┐
Ext ─────┼─> Vercel (Next.js) ─┐              │ Railway                      │
MCP ─────┘                     ├─> Cloudflare ├─> FastAPI (uvicorn)          │
                               │   Worker     │     │  │                     │
                               │  (CORS proxy)│     │  └─> Redis             │
                               │              │     │      broker + results  │
                               │              │     │      rate limits       │
                               │              │     │      locks, caches     │
                               │              │     └─> Postgres + pgvector  │
                               │              │            ▲                  │
                               │              │  Celery worker + beat         │
                               │              │  (--pool=solo, 1 process)─────┤
                               │              │     └─> OpenAI / Bedrock      │
                               │              │     └─> arbitrary web (fetch) │
                               │              │     └─> YOLO subprocess (PDF) │
                               │              │     └─> S3 (off by default)   │
                               └──────────────┴──────────────────────────────┘
```

Classification: a **modular monolith with an async worker tier**. Two deployables (API, worker) share one codebase, one Postgres, one Redis. There is no service-to-service RPC. The distributed part is the API ↔ queue ↔ worker ↔ third-party-API boundary, and the eventually-consistent derived data it produces.

Workload shape:
- Write path: low volume, bursty, **expensive per request** (fetch + parse + 3–4 LLM/embedding calls per saved URL).
- Read path: cheap relational reads, plus vector/hybrid search (pgvector HNSW + tsvector + RRF).
- Background: 10 beat schedules, a multi-agent research pipeline (Celery chord), nightly LLM memory consolidation.

What "correct" means here (the invariants worth defending):
1. A user never sees another user's data.
2. A saved URL is never silently lost (it either completes, fails visibly, or is retried).
3. Derived data (embeddings, tags, chunks, entities) converges to match source data.
4. LLM spend per user is bounded.

Where each invariant stands:

| Invariant | Mechanism | Maturity |
|---|---|---|
| 1. Tenant isolation | `user_id` predicate in every query; tests for cross-user 404; AST validator for text-to-SQL | Good at app layer; no defence in depth (no Postgres RLS) |
| 2. No lost saves | `pending` sweep every 5 min; status column; retries | Partial: `processing` stuck state not swept; broker loss after enqueue is not covered |
| 3. Convergence | Beat sweeps for embeddings, chunks, entities | Good pattern, uneven coverage; sweeps share a single worker slot |
| 4. Spend bound | Redis counter per user per day; fail-open | Good for abuse; not exact (see §3.7) |

---

## 2. Communication

### 2.1 Sync vs async split

| Interaction | Style | Rationale | Assessment |
|---|---|---|---|
| Browser → API | Sync REST, JSON | Standard | Fine |
| API → worker | Async, Celery over Redis | Fetch + LLM are slow and failable | Right call; this is the system's core design decision |
| Worker → worker | Celery `.delay()` chains, one `chord` (research) | Simple fan-out | Chains are implicit; no durable workflow state except DB status columns |
| Client ← result | **Polling** `processing_status` | Cheaper than websockets | Fine at this scale; see §2.6 for the upgrade path |
| API → OpenAI | Sync inside request (search query embedding, insight) | Latency-sensitive | Risk: request threads blocked on a third party |
| Worker → arbitrary websites | Sync `requests.get` | Unavoidable | Untrusted target; SSRF open (§7, G-01) |
| MCP clients → API | Streamable HTTP, OAuth + PKCE | Protocol-defined | Fine |

Why no gRPC / Kafka: single team, single DB, no service boundary. Adding either would add operational cost with no isolation gain. Say this explicitly in an interview; "I considered it and the boundary doesn't exist" is a stronger answer than listing technologies.

When Kafka-style log semantics would matter here: replayable ingestion events (re-embed everything after a model change), multiple independent consumers of "article saved" (search indexer, recommender, analytics). Today each of those is a beat-driven DB scan. That works until scans get expensive (§4.6).

### 2.2 Timeouts

Timeouts form a budget: each layer's timeout must be shorter than its caller's, or the caller times out while the callee keeps working and wasting resources.

| Hop | Timeout | Source | Comment |
|---|---|---|---|
| Postgres pool checkout | 10 s | `database.py` `pool_timeout` | Chosen deliberately (comment explains) |
| Postgres query | none for app queries; 500 ms for text-to-SQL | `query.py` | Inferred: no global `statement_timeout`. A slow query holds one of 5 connections |
| Redis connect (locks) | 1 s | `task_locks.py` | Good |
| Redis socket (limiter, cache) | **Unverified** | `rate_limit.py` | Check: a hung Redis blocks the request path before fail-open triggers |
| OpenAI | 30 s, SDK retries off | `llm_client.py` | Good; but 30 s inside an API request is long |
| Page fetch | 15 s / 30 s | `extraction.py` | `requests` timeout is per-socket-operation, not total. A slow-drip server can exceed it |
| PDF subprocess | 300 s | ADR-0007 | Good isolation |
| Celery task | 30 min hard / 25 min soft | `celery_app.py` | **Dead under `--pool=solo`** (documented in ARCHITECTURE.md §9) |
| Research run | 300 s budget | `research.py` | Application-level; enforced by recovery sweep at 10 min |

Gap: the only unenforced timeout is the one that matters most. A hung fetch or hung LLM call stalls the only worker slot, and `beat` shares that process. Everything (all sweeps, all ingestion) stops. This is the highest-leverage reliability gap in the worker tier (G-03).

### 2.3 Retries

Confirmed pattern across `embedding.py`, `tagging.py`, `chunk_embeddings.py`, `summarization.py`, `article_analysis.py`, `extraction.py`:

```python
raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))   # 60s, 120s, 240s
```

Assessment:
- Exponential backoff: yes. Cap: implicitly by `max_retries=3`.
- **Jitter: none.** After a provider outage ends, every failed task retries at the same instant (60 s after its own failure, which clusters). Fix: `retry_jitter=True` or `countdown * random.uniform(0.5, 1.5)`.
- **Retry classification: partial.** The 2026-07-28 work introduced `TRANSIENT_CHAT_ERRORS` and fixed two cases where retries were dead config (task caught the exception, so Celery never saw it). The same bug class still exists wherever a task catches broadly and returns a dict. A static check (like `test_llm_call_site_guard.py`) for "task with `max_retries` that never raises" would close the class.
- **Extraction sets `failed`, then retries.** In `extract_metadata`, the `HTTPError` and `Timeout` handlers write `processing_status="failed"` and commit, then call `self.retry`. For the next 1–4 minutes the UI shows a permanent-looking failure for an item that is still being retried. Status should stay `processing` until retries are exhausted.
- **Layered retries multiply.** LLM client: 1 retry on same provider + provider fallback. Task: 3 retries. Worst case per LLM call site is 2 × 4 = 8 attempts, each up to 30 s. Fine for correctness, but this is the retry-amplification pattern that causes cascading overload when the provider is slow rather than down.
- **Budget is not part of the retry decision.** `BudgetExceededError` is correctly non-retryable.

### 2.4 Circuit breakers and failover

None exist. What exists instead:
- Provider failover in `LLMClient.chat` (OpenAI ↔ Bedrock). This is failover, not a breaker: it tries the primary on every call, so during a primary outage every call pays the primary's full timeout (30 s) before falling back.
- `embed()` deliberately has **no** fallback, to prevent mixing vector spaces. Correct and worth citing: it shows awareness that availability and data integrity can conflict.

Minimum viable breaker: Redis key `cb:openai:open` with TTL 60 s, set after N consecutive transient failures in a window; `chat()` skips the primary while it exists. 30 lines, no library. Add half-open probing (one request allowed after TTL).

### 2.5 Backpressure and load shedding

| Layer | Mechanism | Verdict |
|---|---|---|
| Ingress | Redis sliding-window rate limit on 4 routes | Good for those routes; `PATCH`, highlights, lists, research have none |
| Queue depth | **None.** Single default queue, unbounded | The 2026-07-28 incident (6.5 M queued messages from per-article dispatch) is the proof this matters |
| Worker | `worker_prefetch_multiplier=1`, concurrency 1 | Prevents hoarding; also caps throughput |
| Per-tenant | Per-user LLM spend cap | Protects cost, not queue fairness |
| Producer side | Sweeps dispatch without checking queue length | A sweep firing into a backed-up queue makes it worse |

Head-of-line blocking (Confirmed: no `task_routes`, no named queues). A 300 s PDF extraction, a 60-user nightly consolidation fan-out and a user's interactive "save URL" all share one FIFO. The save the user is waiting on sits behind whatever was enqueued first.

Fix, in order of value:
1. Two queues: `interactive` (extract, embed, tag for a just-saved item) and `bulk` (sweeps, consolidation, clustering, backfill, dedup). Worker consumes `interactive` first (dedicated process, or `-Q interactive,bulk` with priorities via separate workers).
2. Sweeps check `LLEN` of the target queue and skip if above a threshold.
3. Per-user dispatch cap for bulk import (token bucket on enqueue, not just on the HTTP route).

### 2.6 Delivery of results to clients

Polling is the right default. The upgrade path, if ever needed: SSE endpoint per item (`GET /content/{id}/events`) fed by Redis pub/sub on task completion. SSE over websockets because it is one-way, works through proxies, and reconnects natively. Complication specific to this deployment: the Cloudflare Worker proxy must stream responses without buffering.

---

## 3. Delivery and correctness

### 3.1 Delivery semantics actually provided

Celery default is `acks_early` (ack on receipt, before execution). Confirmed: no `task_acks_late`, no `task_reject_on_worker_lost` in the repo.

| Failure | Result under current config |
|---|---|
| Task raises | Retried per task's `max_retries` (if it raises; see §2.3) |
| Worker process killed mid-task | Message already acked → **lost**. At-most-once for that execution |
| Redis loses queue (restart without persistence) | All queued messages lost |
| API commits row, then `.delay()` fails | Row stays `pending`; recovered by the 5-min sweep (good) |
| API commits row, `.delay()` succeeds, worker crashes mid-`processing` | Row stays `processing` **forever** (audit P1-3, still open) |

So the effective guarantee is: **at-most-once delivery, upgraded to at-least-once by DB-state reconcilers for the states they cover.** That is a legitimate and common design ("at-most-once transport + idempotent reconciliation"), but it only works for states a reconciler scans. Coverage matrix:

| State | Reconciler | Covered |
|---|---|---|
| `pending` > 10 min | `recover_stale_pending_items` | Yes |
| `processing` > 30 min | none | **No** |
| Embedding missing | `process_all_missing_embeddings` (highlights), item embedding via chain | Partial. Verify the item-level sweep exists; ARCHITECTURE.md lists highlight and chunk sweeps |
| Chunks missing | `process_all_missing_chunks` | Yes |
| Entities not analyzed | `backfill_missing_entities_task` (daily, 50/run) | Yes, but 50/day caps recovery rate |
| Entity vectors missing | hourly sweep | Yes |
| Research run stuck | `recover_orphaned_runs` | Yes (marks `partial`) |

Choice to defend: `acks_late=True` + `reject_on_worker_lost=True` for tasks that are idempotent, leaving non-idempotent ones early-ack. Before enabling, check Redis `visibility_timeout` (default 1 h): a task running longer than that is redelivered while still running, giving duplicate concurrent executions. With the 30-min limit this is currently safe; if limits are ever raised, raise `visibility_timeout` above them.

### 3.2 Exactly-once

Not achievable across (DB, Redis, OpenAI). The honest framing is **effectively-once via idempotent effects**. Per task:

| Task | Re-run safe? | Mechanism |
|---|---|---|
| `generate_chunk_embeddings` | Yes | Delete-and-replace chunks |
| `upsert_tag_embeddings` | Yes | Skip existing labels; `label UNIQUE` |
| `cluster_user_tags` | Yes | Replaces user's clusters |
| `upsert_entity` | Yes | Case-insensitive dedupe; `IntegrityError` handled |
| `generate_embedding` | Result-idempotent, **cost-duplicating** | Overwrites vector; pays OpenAI again |
| `analyze_article` | Same | Sets `entities_analyzed_at`; paid LLM call repeated |
| `extract_metadata` | Mostly | Overwrites fields; would clobber user edits to title/description if re-run later (Inferred) |
| `research_subagent` | Yes within a run | `idempotency_key` per search in `searches_run` |
| Refresh token rotation | Yes within 30 s | Grace window distinguishes client retry from theft |
| `send_*_email_task` | **No** | A redelivered task sends a second email. Irrelevant under early-ack; becomes real if `acks_late` is turned on |

The pattern to name: **write-then-mark**. Paid side effect first, then a marker column (`entities_analyzed_at`). A crash between them repeats the paid call. The alternative (mark-then-do) loses work on crash. For paid calls, repeat-on-crash is the right default; the cost is bounded by the per-user budget.

### 3.3 Idempotency keys on the API

Absent. `POST /content` is made idempotent *by content* rather than by key: URL normalization + partial unique index `uq_content_items_user_url_active` (migration `5534fbac2811`). That is a legitimate natural idempotency key and covers the double-click case.

Remaining gap (audit P1-1, half-closed): `ingest_url` does check-then-insert and **does not catch `IntegrityError`** (grep confirms the exception is only handled in `entity_graph.py`). Two concurrent saves now fail at the DB, but the loser gets an unhandled 500 instead of the 409 the client expects, and the extension's structured-409 handling won't fire. Fix: wrap `db.commit()` in `try/except IntegrityError`, `rollback()`, re-run `find_existing_active_item`, raise `DuplicateContentError`.

Other mutating endpoints (list membership add, highlight create) have no key. Highlight creation is the one that matters: a retried POST after a dropped response creates a duplicate highlight (Inferred; highlights have `start_offset/end_offset`, so a unique `(content_item_id, start_offset, end_offset)` constraint would be a natural key).

### 3.4 Deduplication of work

- Content-level: URL normalization (large tracking-param list) + unique index. Good.
- Task-level: **none**. The same item can be enqueued for embedding by the chain, by the extension path, by a sweep, and by a manual action. Each is a paid call. Cheap fix: a Redis `SET NX EX` keyed `task:{name}:{item_id}` at task start (same helper as `acquire_run_lock`).
- Content-body dedup (same article at two URLs, e.g. AMP vs canonical): not handled. `canonical` link or content hash would catch it.

### 3.5 Dead-letter handling

No DLQ. Failure ends as `processing_status='failed'` plus `processing_error` on the row. That works as a **dead-letter table** for extraction: failures are durable, visible to the user (`getIngestIssue` classification), and queryable. What is missing:
- No replay path found (a "retry" endpoint or admin sweep). Unverified; grep for it before claiming absence in an interview.
- Non-extraction failures (embedding, tagging, entity analysis) mostly log and return `{"status": "failed"}` with nothing durable. Those items are retried by sweeps indefinitely, with no attempt counter, so a poison item (e.g. text OpenAI rejects) is retried forever. The `_MAX_CHUNK_CHARS` change in the working tree fixes exactly one poison-input class. A `failure_count` column with a cutoff would fix the class.

### 3.6 Outbox pattern

The classic problem: `db.commit()` then `.delay()` is a dual write. Crash between them loses the dispatch.

This system has an **implicit outbox**: the row's `processing_status='pending'` *is* the outbox record, and `recover_stale_pending_items` is the relay (polling publisher). It is the "transactional outbox with polling relay" pattern, with the table doubling as the outbox. Strengths: no extra table, no extra moving part. Weaknesses: latency (up to 15 min worst case: 10 min staleness + 5 min sweep), and it only covers the first hop. Subsequent hops (extract → embed → tag) rely on chain dispatch inside the task, with the per-stage sweeps as their relays.

When to build a real outbox: when you add a second consumer of the event (e.g. webhook delivery, analytics). Then a `outbox_events(id, topic, payload, created_at, published_at)` table written in the same transaction, relayed by a worker using `SELECT … FOR UPDATE SKIP LOCKED`.

### 3.7 Distributed locks

`task_locks.py` is `SET key 1 NX EX ttl`. Correct for overlap prevention of beat sweeps; known limits to be able to state:
1. **No ownership token.** Value is `"1"`. `release_run_lock` deletes unconditionally. Scenario: run A outlives its TTL; run B acquires; A finishes and deletes B's lock; run C starts while B is still running. Fix: store a random token, release with a Lua compare-and-delete.
2. **No fencing token.** A paused process (GC, SIGSTOP) can resume after TTL expiry and act as if it still holds the lock. For these sweeps the work is idempotent, so harm is duplicate cost only. Do not reuse this helper for anything that needs mutual exclusion on writes.
3. **Fail-open on Redis outage** (deliberate, documented). Correct for availability; it means overlap protection disappears exactly when the system is least healthy.
4. **Single Redis node**, so Redlock-style multi-node arguments don't apply; a failover can lose the lock key. Acceptable given idempotent work.

### 3.8 Budget enforcement accuracy

`check_budget` then `record_spend` is check-then-act on a Redis counter. Under concurrency, N parallel calls can all pass the check before any records. Overshoot is bounded by concurrency × per-call cost (worker concurrency is 1, API concurrency small), so this is acceptable for an abuse backstop. If it ever needs to be exact: reserve estimated cost with `INCRBYFLOAT` first, then reconcile with actual.

### 3.9 Sagas and cross-system workflows

| Workflow | Shape | State | Compensation |
|---|---|---|---|
| Ingestion | Linear pipeline: save → extract → embed → chunk → analyze | Status columns + per-stage sweeps | Not needed; steps are additive and re-runnable |
| Research run | Orchestrated: plan → chord(subagents) → collect → synthesize → verify → memory | `ResearchRun.status` machine, JSONB plan, `searches_run` idempotency keys | Recovery to `partial`; no undo needed since steps are read-mostly |
| Account deletion | DB cascade, **S3 objects not deleted** (audit P1-4) | none | Missing. This is the saga-shaped gap: a multi-system delete with no compensating or retrying step |
| Registration | DB insert + seed content + email dispatch, synchronous seed | none | Seed failure after user insert leaves a half-onboarded user (Inferred) |

The research pipeline is the one genuine orchestrated saga. ADR-0005 names Temporal for it ("survives restarts, built-in compensation") but it is not built; the current design achieves the same durability by persisting the state machine in Postgres and sweeping stalled runs. Interview framing: "I put the saga state in the database I already operate, and traded Temporal's automatic resume for a recovery sweep that degrades to `partial`. I'd move to Temporal when workflows exceed ~10 steps or need human-in-the-loop waits."

Known race (documented in ARCHITECTURE.md §25): `extract_research_memory` is dispatched with `countdown=5` as a heuristic for commit visibility, now backed by `_RunNotReadyError` + retry. This is the standard "publish after commit" problem; the clean fix is dispatching from an `after_commit` hook or the outbox.

---

## 4. Data

### 4.1 Topology

Single Postgres primary on Railway. No read replica, no standby (Unverified: Railway plan details). One Redis, used as broker, result backend, rate-limit store, lock store, cache. These are five different consistency/durability requirements sharing one failure domain.

| Use of Redis | Loss impact | Needs persistence? |
|---|---|---|
| Celery broker | Queued work lost; sweeps recover most | Ideally yes |
| Result backend | Results unused by app logic (Inferred) | No; set `ignore_result=True` to cut load |
| Rate limits | Temporarily permissive | No |
| Locks | Temporary overlap | No |
| Embedding/insight cache | Cache misses, extra cost | No |
| Budget counters | Spend counters reset → budget cap resets | Mildly |
| MCP auth codes (5 min) | Login flow fails; retry | No |

Recommendation: split broker from cache (two Redis instances). A cache eviction policy (`allkeys-lru`) is correct for caches and **catastrophic for a broker**: it would silently evict queued tasks. Today they share one instance, so the eviction policy must be `noeviction`, which makes the cache portion unbounded. Check which policy is set (Unverified).

### 4.2 Partitioning and sharding

Natural shard key: `user_id`. Evidence: every table is user-scoped; every query filters by it; cross-user queries exist only in global lookup (`tag_embeddings`) and admin sweeps.

Staged plan (don't do any of it before it is needed):
1. **Now**: single Postgres. Make `user_id` the leading column in every composite index so queries are partition-prunable later.
2. **~10⁷ rows in `content_chunks`**: partition `content_chunks` and `highlights` by hash(`user_id`) (declarative partitioning, 16–64 partitions). Benefits: smaller HNSW indexes per partition (HNSW build time and memory scale superlinearly), cheaper `VACUUM`, and per-user queries touch one partition. Constraint: pgvector HNSW indexes are per-partition; the planner must prune on `user_id` or it scans every partition's index.
3. **Past a single node's RAM for vectors**: shard by user to multiple Postgres clusters via a `user_shard` directory table (user → cluster). Application-level routing in `get_db`. Cross-user features (the global `tag_embeddings` table, global entity dedup) become per-shard or move to a separate service.
4. **Alternative at step 3**: move vectors out to a dedicated vector store (ADR-0001 chose pgvector deliberately; revisit when this step is reached, not before).

Why hash over range for `user_id`: UUIDs are random, so range partitioning gives uniform sizes but no locality benefit, and hash gives a fixed partition count. Time-based partitioning is the right choice for append-only logs (a future events/outbox table), not for these.

### 4.3 Hot keys and skew

| Candidate | Why it could be hot | Present risk |
|---|---|---|
| `tag_embeddings` (global) | Every tagging task upserts into one table with a unique `label`; popular labels ("machine learning") conflict | Low; `IntegrityError` path exists in entity_graph, check tag path |
| Rate-limit ZSET per user | One user hammering one key | Low; per-user key, short window |
| Query embedding cache | Key is a hash of query text; a popular query is a hot read | Low and desirable (that's what a cache is for) |
| Public profile `/public/u/{username}` | A viral profile → unbounded unauthenticated reads straight to Postgres | **Real**, and the only unauthenticated read path. Cache at the CDN (30–60 s `s-maxage`, `stale-while-revalidate`) |
| One power user | 5k-article library → huge `full_text` scans, big recommendation computation | Medium; per-user skew is the dominant skew in a per-user-sharded system |
| `consolidate_all_users_task` fan-out | Synchronized 3 AM fan-out of N tasks in one burst | Medium; add jittered `countdown` per user |

### 4.4 Consistency model

Two regimes:

**Strong (single-primary Postgres, READ COMMITTED default):** everything the user directly writes and reads: content rows, lists, highlights, auth. Read-your-writes holds trivially. With one primary there are no replication-lag anomalies to explain. **If a read replica is added, route only search and recommendations to it, and pin the user to the primary for N seconds after a write** (or read-your-writes via LSN tracking), otherwise "I just saved it and it isn't in my list" appears.

**Eventual (derived data):** embedding, chunks, tags, entities, clusters, memory profile. Convergence lag per artifact:

| Artifact | Lag | User-visible effect |
|---|---|---|
| Item embedding | seconds to minutes (queue-dependent) | Not in semantic results; falls back to keyword |
| Chunks | after embedding + up to 5 min if dispatch dropped | Search falls back to item-level embedding (designed in) |
| Tags | after analysis | Empty tag area |
| Entities | hourly/daily sweeps | Entity lane (disabled by default anyway) |
| Reading themes | weekly | Stale clusters |
| Memory profile | nightly | Stale MCP context |

The designed-in degradation (chunks missing → item embedding; semantic missing → keyword) is the right pattern: **every derived index has a cheaper fallback**. State that in interviews.

Isolation-level hazards worth knowing here (all Inferred, test before claiming):
- `READ COMMITTED` + check-then-insert (register, ingest) → races, as the audit found. Cure is constraints, not higher isolation.
- Delete-and-replace chunks in one transaction: a concurrent search sees either old or new set (fine), but two concurrent chunk tasks for one item can interleave and double-insert unless the delete+insert is serialized. A per-item advisory lock (`pg_advisory_xact_lock(hashtext(item_id))`) closes it.
- Soft-delete + vector index: soft-deleted rows stay in the HNSW graph and are filtered post-search, which reduces recall for the `LIMIT k` (HNSW returns top-`ef_search` candidates, then the filter drops some). Same effect for the `user_id` filter: with a global HNSW index and a per-user predicate, the index returns nearest neighbours **across all users**, then filters to one user. At scale, a user with 50 articles in a table of 10 M chunks gets almost no results from a top-100 candidate set. This applies to the HNSW-ordered queries (connections, similar, research), not to the main search lane, which scans the user's chunks exactly (see §4.5).

### 4.5 Vector search at scale (the real scaling cliff)

Current (verified against the query planner on the dev database, 2026-10-25): HNSW indexes (`m=16, ef_construction=64`) exist on `content_items.embedding`, `highlights.embedding` and `content_chunks.embedding`, but **the main semantic search lane does not use them**. `_semantic_search` computes `MAX(1 - (embedding <=> q))` per article over every chunk of the user (`WHERE user_id = :uid ... GROUP BY content_item_id`), with no `ORDER BY distance LIMIT k` on the chunk table, so the planner scans the user's chunks (btree on `user_id` or a sequential scan) and computes the exact distance for each. That is exact search with cost linear in the user's chunk count (557 chunks in the dev database), and it has no recall problem. Queries of the form `ORDER BY embedding <=> q LIMIT k` do use HNSW: highlight connections and similar items (`api/search.py`), the research agent's chunk retrieval (`tasks/research.py`), and the MCP similar-items tool.

Two separate scaling questions follow:
1. **Exact lane (search):** cost grows with chunks per user. Roughly 10^4 chunks per user is still tens of milliseconds; at 10^5-10^6 chunks per user it needs an ANN path or a pre-filter step.
2. **HNSW lanes (connections, similar, research):** pgvector's HNSW does approximate search first, then applies the `WHERE`. Filtered ANN has three outcomes: the planner picks the HNSW index and post-filters (low recall for selective filters), or picks a `user_id` btree and sorts exactly (correct, fast for small users), or iterative scan (pgvector 0.8 and later, `hnsw.iterative_scan`). Which one runs depends on statistics. At 100 users it does not matter; at 100 k users it does.

Options, ranked by cost:
1. Enable `hnsw.iterative_scan = relaxed_order` and raise `hnsw.ef_search` for filtered queries. Config only.
2. Partition by `hash(user_id)` (§4.2) so each partition's HNSW is smaller and the filter is a pruning step, not a post-filter.
3. Per-user exact search when the user has < ~5 k chunks (btree on `user_id`, brute-force distance). Per-user libraries are small; exact search over 5 k × 1536 floats is ~10 ms. Route by a cached per-user chunk count.
4. Dedicated vector DB with native filtered search.

Storage arithmetic (use in interviews):
- 1536 dims × 4 bytes = **6 KB per vector**. ~20 chunks/article → ~120 KB of vectors per article, before HNSW overhead (graph links add roughly 30–60%).
- 100 k articles → ~12 GB raw vectors; 1 M articles → ~120 GB. HNSW wants the working set in RAM.
- Levers: `text-embedding-3-small` supports a `dimensions` parameter (e.g. 512 → 3× smaller, small recall loss; requires re-embed); `halfvec` (2 bytes/dim) halves it again; fewer chunks via larger chunk size.
- Changing embedding model = re-embed everything. Plan for it: add `embedding_model` and `embedding_version` columns, dual-write to a new column, backfill behind a flag, cut over, drop. The architecture doc notes `EMBED_PROVIDER` change "requires a full re-embed migration"; the column pattern is how to do it without downtime.

### 4.6 Caching and invalidation

| Cache | Key | TTL | Invalidation | Assessment |
|---|---|---|---|---|
| Query embedding | `qemb:{sha256[:16]}` | 1 h | None needed (pure function of text and model) | Correct. Model change must bump the key prefix, otherwise stale vectors from the old model are served for an hour. Include model name in the key. 16 hex chars = 64 bits; collision odds are negligible at this scale |
| Insight | `insight:{highlight_id}:{article_id}` | 7 d | **None** | Stale if the highlight is edited/deleted or the article's content changes. Negative-result caching is not done (`null` on failure is not cached, so failures re-call the LLM) |
| Reader | `sessionStorage` per tab | tab | Overwrite on fetch | Guarded against partial overwrite (documented) |
| Everything else | none | | | No HTTP caching, no ETags, no CDN rules on API or public pages |

Missing, in order of payoff:
1. **CDN caching of public-profile and public-reader responses** (only unauthenticated read path).
2. `ETag`/`If-None-Match` on `GET /content/{id}/full` (large `full_text` payloads, frequently re-fetched).
3. Cache stampede protection on the insight endpoint: concurrent misses each call the LLM. Use `SET NX` single-flight on the key.
4. Dashboard list: `GET /content` hits Postgres every time. Fine until it isn't; the cache-aside key would need invalidation on every write, which is the usual reason not to.

### 4.7 Schema evolution and deploy safety

Deploy order (Confirmed, `railway.json`): `preDeployCommand` runs `alembic upgrade heads` then `post_deploy.py`, then new containers start. The old containers keep serving during migration (Inferred from "pre-deploy"). So **the old code runs against the new schema** for the duration of the rollout and must tolerate it.

Rules this implies (expand/contract):
- Add nullable columns or columns with server defaults, never rename or drop in the same release as the code change.
- Drop a column only one release after the last code reference is gone.
- Concurrent index builds are already handled (`CREATE INDEX CONCURRENTLY` in `post_deploy.py`, correctly outside the migration transaction).
- Backward incompatible migration + rollback: Railway can roll the app back, but Alembic does not roll the schema back automatically. Roll-forward is the realistic strategy; write down which migrations are not reversible.
- Data migrations at scale (backfills over `content_chunks`) must batch by primary key range with sleeps, outside the migration transaction, as a Celery task with a progress cursor.

### 4.8 Backup, restore, durability

Audit P0-3 stands: no documented backup, no tested restore. The Railway MCP tools available in this environment include backup-policy create/list/restore, so this is a one-hour task. Targets to state: RPO (what data you can afford to lose) and RTO (how long to be back). For a personal-scale product, RPO 24 h and RTO 4 h are defensible, and are only meaningful once a restore has been performed and timed.

Redis is not durable by design here; the system is built so that losing it costs time, not data. That claim holds only if the sweeps in §3.1 cover every state, which they currently do not (`processing`).

---

## 5. Scale

### 5.1 Statelessness

| Component | Stateless? | Evidence |
|---|---|---|
| API | Yes | JWT + cookie auth, no server session; rate limit and locks in Redis |
| Worker | Yes, except `--beat` embedded | Beat state is process-local (`celerybeat-schedule` file) |
| Frontend | Yes (Vercel) | |
| Per-process state | In-memory `RateLimiter` fallback class exists but production uses Redis | Dev/test only |

JWT tradeoff to articulate: stateless verification scales horizontally, but revocation needs state. The system handles this with short-lived access tokens + server-side revocable rotating refresh tokens with reuse detection, which is the textbook answer. It also does a DB lookup of the user on every request (`get_current_user` loads the user), so it is "stateless" in the sense of no session store, not in the sense of no database hit.

### 5.2 Horizontal scaling: what breaks at 2 replicas

| Change | Effect |
|---|---|
| API ×2 | Works. Pool is 5 connections per process; total connections = replicas × workers-per-replica × 5. Check against the Postgres connection limit (audit P2-3). Introduce PgBouncer (transaction pooling) before exceeding ~20 backend connections. Note: psycopg3 prepared statements need PgBouncer ≥ 1.21 support or `prepare_threshold=None` |
| Worker ×2 with `--beat` in the start command | **Beat fires twice.** Every schedule runs twice per interval. The `acquire_run_lock` guards cover most sweeps; `cleanup-old-deleted-items` is not listed as guarded (Unverified). Correct fix: beat in its own single-replica service; workers without `--beat` |
| Worker pool | Currently `--pool=solo --concurrency=2`: `--concurrency` is ignored under solo, so concurrency is **1** (Inferred from Celery behaviour; ARCHITECTURE.md confirms solo) |
| Redis | Single node, vertical only; the broker is a single point of failure |

Why `--pool=solo`: the app runs on a small Railway container with ML-capable dependencies, and the PDF path is already isolated in a subprocess (ADR-0007). Solo avoids fork memory duplication. The cost is no concurrency and no enforced time limits.

Better fit for this workload: tasks are I/O-bound (HTTP fetch, OpenAI) except PDF extraction. Use `--pool=gevent --concurrency=50` (or `threads`) for the I/O queue, and a separate `--pool=prefork --concurrency=1` worker with `-Q pdf` for CPU/memory-heavy extraction. This also gives enforced time limits on prefork (SIGKILL on hard limit) and makes `soft_time_limit` real. With gevent, `requests` must be monkey-patched and DB sessions must not be shared across greenlets (the `DatabaseTask._db` instance attribute **is shared across concurrent tasks of the same task object**, which is unsafe under gevent/threads: the base class caches one session on the task instance). Fix that before changing pool type.

### 5.3 Load balancing

Railway edge terminates TLS and balances across replicas (Unverified: algorithm). Cloudflare Worker proxy sits in front for CORS. Points:
- Health-based routing needs a real readiness endpoint (§6.4); today `/health` always returns 200, so a replica with a dead DB stays in rotation.
- The Worker strips `Origin` and forwards to the Railway hostname. If `RAILWAY_ORIGIN_SECRET` (the origin URL, despite the name) is publicly reachable, clients can bypass Cloudflare and any WAF/rate rules placed there. Add a shared-secret header verified by the API, or restrict origin by IP.
- Client IP: the rate limiter falls back to `request.client.host` for unauthenticated requests (login, register). Behind Cloudflare → Railway, that is the proxy's IP unless the app reads `CF-Connecting-IP`/`X-Forwarded-For` through a trusted-proxy configuration. If so, **all anonymous traffic shares one bucket**: 10 logins/min globally, or the limiter is ineffective. Inferred, high value to check (G-07).

### 5.4 Rate limiting

Implementation: sliding-window log in Redis ZSET (`ZREMRANGEBYSCORE` + `ZCARD` + `ZADD`), two windows per route, `Retry-After` on 429, fail-open. Properly cross-instance.

Tradeoffs to articulate:
- Sliding window log vs token bucket: log is exact and memory-heavy (one member per request); fine at 10–300 req/window. Token bucket is O(1) memory and allows bursts. For per-user API limits at higher volume, switch to a Lua-scripted token bucket.
- **Atomicity.** The check (`ZCARD`) and the add (`ZADD`) are separate pipeline executions (Confirmed from the code excerpt: two `pipe.execute()` calls). Two concurrent requests can both see `count = limit-1` and both proceed. Overshoot is bounded by concurrency; fix with a single Lua script.
- Coverage: 4 routes. A user can hammer `PATCH /content`, highlights, lists, MCP `/mcp-transport`, and `POST /research` (the most expensive endpoint after search, since it launches a multi-agent LLM run). Add a coarse global per-user limit as a default, and a tight one on `/research` and `/content/{id}/summary`.
- Placement: app-level middleware means rejected requests still consume an API worker. Moving coarse limits to Cloudflare (rate limiting rules) sheds load before it reaches Railway.
- Fail-open is a stated policy, consistently applied (limiter, budget, locks). It is a defensible availability-over-protection choice for a read-later app; it would not be for a payments API.

### 5.5 Multi-tenant isolation

| Layer | Isolation | Gap |
|---|---|---|
| Data (rows) | `user_id` predicate in app code | Single missed filter = cross-tenant leak. No RLS as defence in depth |
| Data (text-to-SQL) | sqlglot AST allow-list + mandatory `:user_id` equality + 500 ms timeout; ADR-0006 | Strongest piece of isolation engineering in the repo; the P1-6 soft-delete question remains |
| Compute (queue) | none | One user's bulk import occupies the single worker for hours |
| Compute (DB) | none | Pool shared; no per-user statement limits |
| Cost | per-user daily LLM budget | Good |
| Rate | per-user limit on 4 routes | Partial |
| Storage | S3 key prefix by user; presign per object | Orphans on deletion (G-12) |
| Cache | Keys contain ids, not user | Insight cache keyed by highlight+article ids; safe only because ids are UUIDs and endpoints check ownership before reading cache (Verify order: ownership check must precede cache read) |

Postgres RLS recipe: `ALTER TABLE … ENABLE ROW LEVEL SECURITY; CREATE POLICY … USING (user_id = current_setting('app.user_id')::uuid)`, and `SET LOCAL app.user_id = …` at the start of each request transaction in `get_db`. Cost: one statement per transaction, plus care with connection pooling (must be `SET LOCAL`, never `SET`, under PgBouncer transaction mode). It turns "every query is correct" into "the database enforces it" for the app role; keep the migration/admin role `BYPASSRLS`.

Fairness for the queue (the missing isolation): per-user routing key or weighted fair queueing. Cheapest effective version: cap in-flight tasks per user via a Redis counter checked at dispatch (`INCR`/`DECR` around task execution), delaying (`apply_async(countdown=…)`) rather than rejecting.

---

## 6. Operations

### 6.1 What exists

| Signal | Tooling | State |
|---|---|---|
| Traces | OTEL → Grafana Cloud (FastAPI, SQLAlchemy, Celery instrumented) | In place; opt-in |
| Errors | Sentry, `traces_sample_rate=0.1` | In place; opt-in |
| LLM | Braintrust spans with `user_id`, cost, tokens; CI guard enforcing coverage | Best-in-class for this size |
| Product analytics | PostHog | In place |
| Logs | Python logging, f-string messages | Unstructured |
| Metrics | **None defined** | RED/USE metrics not emitted |
| Alerts | **None found in repo** | Unverified for Railway/Grafana UI |
| SLOs | **None** | |
| Runbooks | None; the incident retro/changelog docs are the nearest | |

Tracing without metrics is the wrong way round. Traces answer "why was this request slow"; metrics answer "is something wrong right now", and alerts need metrics.

### 6.2 Minimum metric set

RED for the API: request rate, error rate (5xx), duration histogram (p50/p95/p99) per route template. USE for dependencies: Postgres pool in-use/overflow/wait time, Redis latency, worker utilization.

Queue and pipeline metrics, the ones that matter most for this system:
- `queue_depth{queue}` (Redis `LLEN`), `oldest_message_age` (needs a timestamp header)
- `task_duration{task}`, `task_failures{task,reason}`, `task_retries{task}`
- **Ingestion freshness SLI:** age of the oldest item with `processing_status in ('pending','processing')`. One SQL query, directly measures user pain, covers the stuck-`processing` hole, and doubles as the alert for broker death and worker death.
- **Derived-data lag:** count of items with `embedding IS NULL AND created_at < now() - interval '15 min'`; same for chunks and entities.
- LLM: spend per day, error rate per provider, fallback rate (a rising fallback rate is the breaker's missing input).

### 6.3 Candidate SLOs

| SLI | Target | Window | Notes |
|---|---|---|---|
| API availability (non-5xx on `/content*`, `/auth*`) | 99.5 % | 30 d | ~3.6 h/month budget; honest for a single-instance deployment |
| `GET /content` latency p95 | < 500 ms | 30 d | |
| `/search/semantic` latency p95 | < 1.5 s | 30 d | Dominated by the query-embedding call; cache hit path much faster |
| Ingestion: saved item reaches `completed` | 99 % within 2 min, 99.9 % within 15 min | 30 d | The one that reflects the product |
| Derived data: embedding present | 99 % within 10 min | 30 d | |

Error-budget policy (even informal): if 30-day budget is exhausted, feature work pauses for reliability items from §7. A one-person project still benefits from writing this down, because it turns "should I fix this?" into arithmetic.

### 6.4 Health checks

`/health` returns `{"status": "healthy"}` unconditionally (Confirmed, `main.py`). Split into:
- `/livez`: process is up. No dependencies. Used for restart decisions.
- `/readyz`: `SELECT 1` and `PING` with 200–500 ms timeouts; returns 503 with failing component. Used for routing.

Do not put the dependency check on the liveness probe: a Postgres blip would then restart every healthy API replica, turning a dependency outage into a full outage (a classic self-inflicted cascade).

Worker health: Celery has no HTTP probe. Options: a heartbeat key written by a beat task (`SET worker:heartbeat <ts> EX 120`) and checked by the readiness endpoint or an external monitor. This is the cheapest answer to "the worker silently stopped running Celery" which is what the 2026-09-27 Procfile incident actually was.

### 6.5 Deployments

| Aspect | State |
|---|---|
| API deploy | Railway build → pre-deploy migrations → start new, stop old. Rolling-ish, replica count 1 means a short gap (Unverified) |
| Worker deploy | Restart; in-flight task is killed at SIGTERM+grace. With early-ack and solo pool, the in-flight task is lost until a sweep notices |
| Frontend | Vercel atomic deploys, instant rollback |
| Rollback | App yes; schema roll-forward only |
| Canary / progressive | None |
| Feature flags | Env vars. `NEXT_PUBLIC_*` flags are inlined at **build time** by Next.js, so toggling needs a rebuild/redeploy; `ARCHITECTURE.md` §20 says "toggle without redeployment", which is true only for backend flags (`ENTITY_SEARCH_ENABLED`, `S3_STORAGE_ENABLED`) read at runtime. Correct the doc or add a runtime flags endpoint |
| Config safety | `DEBUG` defaults to `True`, `SECRET_KEY` has no strength check (audit P0-2, **still open**: `config.py:23`) |

Cheap progressive delivery for a solo project: ship behind a backend flag read per-request (`flags` table or env + user allow-list), enable for the owner's account first (dogfood), then 10 %, then all. A user-hash-based percentage rollout is ~10 lines. Canary at the infrastructure level (traffic splitting) isn't available or worthwhile on Railway at this scale.

Worker graceful shutdown: set `terminationGracePeriod` long enough and use `acks_late` + `reject_on_worker_lost` for idempotent tasks so a deploy re-queues the interrupted task instead of dropping it.

### 6.6 Incident response

Strengths worth highlighting:
- Failure-injection testing was run deliberately (Redis down, OpenAI outage, hung task, concurrent token refresh) and produced 10 documented fixes, including finding a 6.5 M-message backlog by accident. This is rare, and is a good interview story: hypothesis → inject → observe → fix → regression test.
- Post-incident documentation (the 2026-09-27 Celery outage root-caused to Procfile precedence, written up in `ARCHITECTURE.md` §3).

Gaps:
- Detection: the 2026-09-27 outage ("background tasks stopped making progress") had no alert; it was found by observation. The ingestion-freshness SLI in §6.2 would have paged within 10 minutes.
- No runbooks: "queue backed up", "worker not consuming", "Postgres connections exhausted", "OpenAI outage", "bad migration". Each is a half-page: symptom, dashboard, safe actions, escalation. The existing retro docs contain most of the content already.
- No on-call: for a solo project, the equivalent is an alert channel with severity tiers (page only on the freshness SLI and `/readyz` failing; everything else is a daily digest).
- No game-days since July. Re-run the injection suite after the worker-tier changes in §7.

---

## 7. Gap register

Priority = (user impact × likelihood) / effort. IDs are referenced above. "Audit" = `docs/audits/2026-10-12-production-readiness.md`.

| ID | Gap | Principle | Evidence status | Effort | Priority |
|---|---|---|---|---|---|
| G-01 | SSRF in all `requests.get` call sites | Security / trust boundary | Confirmed, still open (audit P0-1) | 0.5 d | **P0** |
| G-02 | `DEBUG=True` default; no `SECRET_KEY` validation | Config safety | Confirmed, open (`config.py:23`) | 1 h | **P0** |
| G-03 | Time limits unenforced under `--pool=solo`; single slot shared with beat | Timeouts, isolation | Confirmed (documented) | 1–2 d (pool change + session-per-task) | **P0** |
| G-04 | No backup/restore procedure | Durability | Confirmed absent from docs; live state unverified | 2 h | **P0** |
| G-05 | `/health` doesn't check dependencies; no readiness split | Health, LB | Confirmed (audit P1-5) | 1 h | P1 |
| G-06 | `processing` items never recovered; no `acks_late` | Delivery | Confirmed (audit P1-3) | 0.5 d | P1 |
| G-07 | Anonymous rate limit keyed on proxy IP behind Cloudflare/Railway | Rate limiting | Inferred; check | 1 h to verify | P1 |
| G-08 | `ingest_url` doesn't catch `IntegrityError` → 500 on race | Idempotency | Confirmed by grep | 1 h | P1 |
| G-09 | Single queue; head-of-line blocking; no queue-depth guard | Backpressure | Confirmed | 0.5 d | P1 |
| G-10 | No ingestion-freshness SLI, no alerts, no metrics | Observability | Confirmed (repo); live unverified | 1 d | P1 |
| G-11 | Embedded `--beat` blocks horizontal worker scaling | Scale | Confirmed (start command in docs) | 2 h | P1 |
| G-12 | Account/item deletion leaves S3 objects | Saga / cleanup | Confirmed (audit P1-4) | 0.5 d | P1 |
| G-13 | Retry without jitter; no circuit breaker; `failed` flashed during retry | Retries | Confirmed | 0.5 d | P2 |
| G-14 | Lock release without ownership token | Locks | Confirmed in `task_locks.py` | 1 h | P2 |
| G-15 | No poison-item counter; sweeps retry forever | DLQ | Inferred | 0.5 d | P2 |
| G-16 | Check-then-act in rate limiter and budget | Atomicity | Confirmed (limiter excerpt) | 2 h | P2 |
| G-17 | Redis single instance serving broker + cache + locks + limits | Failure domains | Confirmed | 0.5 d (second instance) | P2 |
| G-18 | No size caps on fetched or inbound content | Resource limits | Confirmed (audit P2-2) | 0.5 d | P2 |
| G-19 | Search lane is an exact scan per user (linear in chunks); HNSW lanes have a filtered-ANN recall risk; no partitioning | Data scale | Verified with EXPLAIN (search lane); HNSW risk inferred | 1 d benchmark | P2 (becomes P0 at scale) |
| G-20 | No RLS; tenant isolation depends on every query | Multi-tenancy | Confirmed | 2 d | P2 |
| G-21 | Public pages uncached | Caching, hot keys | Confirmed | 0.5 d | P2 |
| G-22 | Task-level dedup absent (duplicate paid calls) | Dedup | Confirmed | 0.5 d | P3 |
| G-23 | `DatabaseTask._db` shared per task object; unsafe for concurrent pools | Concurrency | Confirmed from `base.py` | 2 h | prerequisite to G-03 |
| G-24 | Insight cache never invalidated; embedding cache key lacks model name | Cache invalidation | Confirmed | 1 h | P3 |
| G-25 | Doc claims env flags toggle without redeploy; false for `NEXT_PUBLIC_*` | Docs accuracy | Confirmed by Next.js semantics | 10 min | P3 |

Suggested sequence (each step makes the next one safer):
1. G-02, G-05, G-08, G-25 (an afternoon; no design decisions).
2. G-01, G-04 (the two things that hurt most if wrong).
3. G-23 → G-03 → G-11 → G-09 (one worker-tier PR series: session safety, new pool, separate beat, two queues).
4. G-06 + G-10 (stuck-state recovery and the freshness SLI/alert, designed together).
5. G-07, G-13, G-14, G-16 (hardening of existing mechanisms).
6. G-19 benchmark, then decide on G-20/G-17/G-21 based on actual growth.

---

## 8. Target architecture (the "10× then 100×" answer)

### Stage 0 — now (single-digit to hundreds of users)

Current topology plus §7 steps 1–4. Cost: a few engineer-days. Do not add services.

### Stage 1 — ~10 k users

```
Cloudflare (WAF, rate rules, cache public pages)
   └─> API ×N (stateless) ──> PgBouncer ──> Postgres primary + 1 read replica (search/recs)
          │
          └─> Redis-cache (LRU)
Redis-broker (noeviction, AOF)
   ├─> worker-io (gevent ×50)    queues: interactive, bulk
   ├─> worker-pdf (prefork ×1)   queue: pdf
   └─> beat (singleton)
```

Changes from now: split Redis, PgBouncer, replica for search, worker pool split, queue split, CDN for public pages, metrics + SLO alerts, RLS, backups with tested restore.

### Stage 2 — ~100 k+ users or ~10⁷–10⁸ chunks

- Partition `content_chunks`/`highlights` by `hash(user_id)`; `halfvec` or reduced dimensions.
- Introduce a real event log or outbox so independent consumers (indexer, recommender, analytics) replace beat scans.
- Move ingestion to a durable workflow engine only if workflows get longer or need human waits; otherwise keep DB-state + reconcilers.
- Dedicated fetch service with egress controls (SSRF boundary, per-domain rate limiting and politeness, robots.txt, a shared cache of fetched pages across users, since many users save the same URL).
- Search: separate read path (replica or dedicated vector index) with its own SLO.

Cross-user shared fetch cache is an unusual but strong point: URL → extracted content is user-independent, so extraction (and the embedding of the shared content) can be done once per canonical URL and referenced per user. That is a 5–50× cost reduction on popular articles, at the price of a shared table and a privacy review (user-specific extension-extracted HTML must never enter it).

### What not to build (and why, if asked)

- Microservices: one team, one DB, no independent scaling or deploy need that a queue split doesn't already give.
- Kafka: no multiple consumers, no replay requirement yet; Redis streams or a Postgres outbox covers the need.
- Multi-region: single-region read-later app, RPO 24 h; cost and consistency complexity with no user-facing benefit.
- Temporal now: the research saga works with DB state + sweeps; adopt at ~10 steps or when waits become hours.

---

## 9. Capacity model (assumptions, replace with measurements)

Assumptions: 10 000 users; 30 saves/user/month; peak = 10× average; ~20 chunks/article; ~3 k tokens/article.

| Quantity | Calculation | Result |
|---|---|---|
| Saves per month | 10 000 × 30 | 300 000 |
| Average save rate | 300 000 / 2.6 M s | ~0.12 /s |
| Peak save rate | ×10 | ~1.2 /s |
| Tasks per save | extract, embed, chunk, analyze (+ highlight sweeps) | ~4–5 |
| Worker-seconds per save | fetch 2–15 s + 3 × ~2–5 s API | ~10–30 s, mostly waiting on I/O |
| Throughput of 1 solo slot | 1 / 20 s | ~3 saves/min = ~130 k/month, **below the peak of 1.2/s** |
| Gevent ×50 on one container | 50 / 20 s | ~2.5 saves/s: covers peak with margin |
| Vector storage | 300 k articles/month × 120 KB | ~36 GB/month raw (+ index overhead) |
| API read rate | 10 000 users × ~50 requests/day / 86 400 | ~6 req/s average, ~60 req/s peak |
| DB connections | replicas × procs × 5 | Cap at the Postgres limit; PgBouncer past ~20 |

Takeaways to state aloud: the write path is bounded by external I/O and the worker concurrency model, not by Postgres; the storage cliff is vectors, not rows; and the first thing to break at 10× is the single worker slot, which is a configuration change rather than an architecture change.

Cost: embedding and small-model LLM calls per article are fractions of a cent (verify current pricing); the per-user `$5/day` ceiling is a abuse backstop orders of magnitude above realistic use. The cost lever that matters is duplicate work (§3.4) and re-embedding on model change.

---

## 10. Interview playbook

### 10.1 How to present this system (10-minute version)

1. **Problem and shape (1 min).** Read-later with search and AI; write path expensive and failable, read path cheap. Modular monolith + async worker tier.
2. **Core decision (2 min).** Return 201 immediately, do slow work in a queue, expose state via a status column, converge with idempotent tasks and reconcilers. Draw the diagram in §1.
3. **Correctness story (3 min).** At-most-once transport + idempotent effects + polling reconcilers (an implicit outbox). Concrete bug stories: dead `max_retries` (exception swallowed), the 6.5 M-message dispatch storm, the Procfile outage.
4. **Tenant isolation (1 min).** `user_id` everywhere, text-to-SQL AST guard, per-user LLM budget.
5. **What's weak and what's next (3 min).** Single worker slot with unenforced limits, single queue, no SLOs, filtered ANN cliff. Then the staged plan in §8. Naming your own gaps with a sequence is the strongest signal.

### 10.2 Questions and model answers

**"How do you guarantee a saved URL is processed?"**
Not by queue guarantees. The row is committed first with `pending`; the broker message is a hint. A sweep re-dispatches anything pending too long. The remaining hole is `processing` after a worker crash, which I'd close by enabling `acks_late` for idempotent tasks plus a sweep on `processing` older than the task limit.

**"Exactly-once?"**
Impossible across DB, broker, and a third-party API. I make effects idempotent (delete-and-replace chunks, unique constraints, skip-if-present) and accept duplicate paid calls as a bounded cost, capped per user.

**"What happens if Redis dies?"**
Queue and caches are lost; rate limits and budget fail open; locks fail open; API keeps serving reads and saves (rows commit, dispatch fails, rows stay `pending`). Sweeps recover once Redis returns. I verified the dispatch-during-Redis-restart case by failure injection. The hole is anything in `processing` at that moment.

**"What happens if OpenAI is down for an hour?"**
Chat falls back to Bedrock; embeddings deliberately don't (vector space integrity), so new items wait at "no embedding" and search degrades to keyword, which is designed in. Tasks retry with backoff; sweeps pick up the rest. Missing: breaker (so we don't pay 30 s per call to discover the outage), and jitter (so recovery doesn't stampede).

**"How would you scale search to 100 M vectors?"**
Not by turning up HNSW. The problem is filtered ANN: the index finds global neighbours and then the per-user filter removes most of them. Partition by user hash so the filter becomes pruning, use exact search for small users, reduce dimensions/halfvec for memory, and move to a dedicated engine only if one node's RAM is exceeded.

**"Rate limiting design?"**
Sliding-window log in Redis per user and route, two windows, `Retry-After`. Trade-offs: memory vs token bucket, atomicity (move to Lua), fail-open policy, and coarse limits at the edge so rejected traffic never reaches the app.

**"How do you do zero-downtime schema changes?"**
Expand/contract: migrations run pre-deploy while old code still serves, so every migration must be backward compatible with the previous release; concurrent index builds outside the transaction; backfills as batched resumable tasks; roll forward, not back.

**"How do you isolate tenants?"**
Today: application predicate, tested; text-to-SQL validated structurally; per-user budget. Not yet: queue fairness, RLS. I'd add RLS with `SET LOCAL` per transaction as defence in depth, and a per-user in-flight cap on tasks.

**"Where would a distributed lock be wrong here?"**
The Redis `SET NX EX` helper is fine for overlap suppression of idempotent sweeps, wrong for mutual exclusion on writes: no fencing token, no owner check on release, fails open. For writes use a DB constraint or `pg_advisory_xact_lock`.

**"What would you monitor?"**
Ingestion freshness (age of oldest non-terminal item) as the primary SLI, because it measures the user's problem and catches broker death, worker death, and stuck states with one query; then API RED metrics, queue depth and age, pool saturation, provider error and fallback rates, and derived-data lag.

**"Design question: add real-time collaborative lists."**
New requirement changes the consistency model. Options: last-write-wins on list membership (it's a set; use an OR-set/add-wins semantic with per-element tombstones), SSE or websocket fan-out via Redis pub/sub, and presence in Redis with TTL. Ordering within a list needs fractional indexing, not integer positions. Keep the DB as source of truth; pub/sub is a hint, so clients reconcile on reconnect by fetching since a version cursor.

### 10.3 Concept → where it lives in this codebase

| Concept | Location | Use as example of |
|---|---|---|
| Async offload + status column | `POST /content` → `extract_metadata`, `processing_status` | Command/query split, polling |
| Polling reconciler / implicit outbox | `recover_stale_pending_items` | Dual-write problem |
| Overlap guard | `core/task_locks.py` | Distributed lock limits |
| Sliding window limiter | `middleware/rate_limit.py` | Redis data structures, fail-open |
| Budget enforcement | `llm_client.check_budget` | Cost control, check-then-act |
| Provider failover with integrity exception | `LLMClient.embed` vs `chat` | Availability vs correctness |
| Idempotent replace | `generate_chunk_embeddings` | Idempotent effects |
| Retry-dead-config bug | `article_analysis` fix (2026-07-28) | Failure semantics of frameworks |
| Dispatch storm | 6.5 M-message incident | Backpressure, fan-out amplification |
| Saga with DB state machine | `ResearchRun`, chord, recovery | Orchestration vs choreography |
| Process isolation for memory | `_yolo_worker.py`, ADR-0007 | Bulkheads |
| Refresh-token reuse detection + grace window | `api/auth.py` | Distinguishing retry from attack |
| Text-to-SQL guard | `mcp/tools/query.py`, ADR-0006 | Defence in depth for untrusted generated queries |
| Graceful degradation chain | semantic → keyword; chunks → item embedding | Fallback hierarchy |
| Eval-gated CI | `evals-ci.yml` | Regression gates for nondeterministic components |

### 10.4 Vocabulary to use precisely

- **At-least-once + idempotent consumer** (not "exactly-once").
- **Head-of-line blocking**, **bulkhead**, **load shedding**, **retry amplification**, **thundering herd**.
- **Reconciler / control loop** for the sweeps: desired state (every item embedded) versus observed state.
- **Fencing token** for what the lock helper lacks.
- **Expand/contract** for migrations.
- **Filtered ANN** for the vector scaling problem.
- **Error budget** for the SLO policy.

---

## 11. Verification checklist (turn "Inferred" into "Confirmed")

Each is a short check that upgrades a claim in this document before it is quoted as fact.

| Claim | Check |
|---|---|
| Anonymous traffic shares one rate-limit bucket | Send 11 login attempts from two different networks; or log `request.client.host` in prod |
| `--concurrency=2` ignored under solo | `celery inspect stats` on the worker: `pool.max-concurrency` |
| Redis eviction policy | `CONFIG GET maxmemory-policy` on the Railway Redis |
| `cleanup-old-deleted-items` unguarded under double beat | Read `tasks/cleanup.py` for `acquire_run_lock` |
| Duplicate highlights on retry | POST the same highlight twice |
| Planner uses HNSW vs btree for filtered queries | `EXPLAIN (ANALYZE, BUFFERS)` on the chunk search query for a small user and, after seeding 1 M rows, again |
| Replica count and rollout behaviour | Railway service settings; deploy while polling `/health` |
| Failed-then-retry status flicker | Run extraction against a URL returning 503 and watch the row |
| Insight endpoint checks ownership before cache read | Read `api/search.py` insight handler |
| Restore time | Perform a backup restore into a scratch environment and time it |

---

## 12. Related documents

- `ARCHITECTURE.md` §3 (deploy), §8 (rate limit), §9 (pipeline, known worker gap), §24 (observability), §25 (research pipeline)
- `docs/audits/2026-10-12-production-readiness.md`
- `docs/retros/2026-07-28-failure-injection-plan.md`, `docs/changelog/2026-07-28-failure-injection-fixes.md`
- ADRs: 0001 vector storage, 0002 observability, 0005 pipeline orchestration, 0006 text-to-SQL, 0007 Celery memory isolation
