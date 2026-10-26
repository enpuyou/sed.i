---
layout: layouts/chapter.njk
title: Gaps and roadmap
part: 5
order: 3
summary: A ranked register of what is weak or missing in sed.i today, each item re-checked against the current code, with the order to fix them.
verified: 2026-10-25
permalink: /reliability/gaps-and-roadmap/
---

## In one minute

This chapter is the list of what is still wrong with sed.i, ranked, and the order to fix it. Every item was re-checked against the code on main on 2026-10-25, not copied from earlier audits. Each has a status (open, fixed on main, partly, or not verified), evidence you can find, the impact, the smallest fix and a rough effort.

The short version: the system has good bones (a status column plus sweeps that recover lost work, per-user spend caps, bounded fetches and batching since the memory work) and a handful of specific holes. The most important are that the worker will fetch any address a user saves, that a worker killed mid-task leaves an item "processing" forever, that the dashboard shows only the newest 50 items, and that two settings default to unsafe values. Most fixes take an hour to a day. Where something could not be checked from the repository (backups, production settings), it says so.

## The problem

The question this chapter answers: **what should be fixed first, and how do you know each item is real?**

The obvious approach is to keep one long list of worries. That fails because a flat list has no order, and because lists go stale: several items in older write-ups were fixed since they were written, and others were never as bad as they sounded. A risk that is not re-verified against the code either wastes a week or hides a hole.

This chapter therefore keeps two rules. Every item names the file or measurement that proves it. Every item is ranked by one rule: (user impact times likelihood) divided by effort, with user-visible and security items ahead of internal tidiness at equal effort.

## Mental model

A building inspector's report: each finding has a location, a severity, a repair and a cost, and the report is sorted so the contractor starts with the leaking roof, not the paint.

| Term | Meaning here |
|---|---|
| Open | The weakness exists in the code on main today. |
| Fixed on main | It was a gap and has been closed; kept in the register so nobody re-opens it by mistake. |
| Partly | Some of the risk is closed and some remains; the entry says which. |
| Not verified | It depends on a live setting (Railway, Cloudflare) that cannot be read from the repository. |
| Effort | Rough engineer time: an hour, half a day (0.5 d), a day. An estimate, not a measurement. |

{% fig "How the register is ordered" %}
```text
tier 1  security and data loss, small fix        do first
tier 2  user-visible defects, recovery holes     next
tier 3  hardening of existing mechanisms         then
tier 4  scale and quality work, decide on growth later
```
{% endfig %}

## Walkthrough: the top three items

Three items head the register. Each is shown with the exact code location, a failing scenario in prose with concrete values (illustrative: the numbers are chosen to show the shape, not measured in production), and the smallest fix as a diff-style excerpt. The fixes are not applied; the repository is unchanged.

### Item 1: the worker fetches whatever address it is given (open)

**Where.** All five download sites in the extraction task go through one function, which is the good part: there is a single choke point. That function makes the request and does not look at where the address points:

{% fig "core/fetch.py: the single place a user URL is downloaded" %}
<!--src content-queue-backend/app/core/fetch.py#83-87-->
```python
def fetch_bounded(
    url: str, *, timeout: float, headers: Mapping[str, str]
) -> FetchedPage:
    resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
    try:
```
{% endfig %}

The module docstring says so itself ("Does NOT: validate the target address (SSRF)"), and a search of `app/` for address-range checks finds none. `requests` also follows redirects by default, so a public address can send the worker somewhere else.

**Failing scenario (illustrative).** A user saves a link whose host resolves to a private address, say 10.0.0.5 on the platform's internal network. The worker, which sits inside that network, requests it with its normal 30-second timeout. Whatever answers, up to the 10 MB cap (`FETCH_HTML_MAX_BYTES`), is run through the article extractor and stored in the item's `full_text`, which the same user can then read in the reader. The service behind that address never expected to be reachable from user input. The same applies to a cloud metadata address, which is why this class of bug (server-side request forgery, SSRF) is ranked first even though no one has reported it.

**Smallest fix.** Check the address before connecting and on every redirect hop, in the one function:

```text
+import ipaddress, socket
+
+def _assert_public(url):
+    host = urlparse(url).hostname
+    for info in socket.getaddrinfo(host, None):
+        ip = ipaddress.ip_address(info[4][0])
+        if (ip.is_private or ip.is_loopback or ip.is_link_local
+                or ip.is_reserved or ip.is_multicast):
+            raise FetchRejectedError("address not allowed")
+
 def fetch_bounded(url, *, timeout, headers):
-    resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
+    for _ in range(5):                      # follow redirects by hand
+        _assert_public(url)
+        resp = requests.get(url, timeout=timeout, headers=headers,
+                            stream=True, allow_redirects=False)
+        if not resp.is_redirect:
+            break
+        url = urljoin(url, resp.headers["location"])
```

Two caveats belong with the fix: resolving the name and then connecting is a small time-of-check gap (pinning the resolved address in the connection closes it), and the scheme must be limited to http and https. Effort: 0.5 d, plus tests for each rejected range and a redirect to one. A dedicated fetch service with egress rules is the long-term answer; see [Distributed-systems review](/reliability/distributed-systems/).

### Item 2: the dashboard shows only the newest 50 items (open, user-visible)

**Where.** The dashboard list fetches once, with no parameters, and keeps the total it is told:

{% fig "components/ContentList.tsx: one request, no further pages" %}
<!--src frontend/components/ContentList.tsx#249-252-->
```typescript
    try {
      const response = await contentAPI.getAll();
      setContents(response.items);
      setTotal(response.total);
```
{% endfig %}

The client helper takes no arguments:

{% fig "lib/api.ts: getAll has no paging parameters" %}
<!--src frontend/lib/api.ts#345-347-->
```typescript
  getAll: async () => {
    return fetchWithAuth(`${API_BASE_URL}/content`);
  },
```
{% endfig %}

and the endpoint defaults to a page of 50, with a maximum of 100:

{% fig "api/content.py: the server pages, the client never asks for page two" %}
<!--src content-queue-backend/app/api/content.py#259-260-->
```python
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
```
{% endfig %}

The add-to-list dialog makes the same single call (`frontend/components/AddContentToListModal.tsx`, one `getAll()` in `fetchAllContent`).

**Failing scenario (illustrative).** A reader has 120 saved items. The server returns the newest 50 and `total: 120`. The header shows "50 / 120 items" (the component prints the visible count and the total), the 70 older items are not in the list, and nothing on the page loads them. They are still found by search, so the data is intact, but the queue looks as if it lost everything older than the 50th save. In the "add to list" dialog the older items cannot be chosen at all.

**Smallest fix.** The list page already pages: the hook that loads a list 50 items at a time (`frontend/hooks/usePagedListContent.ts`) and the scroll sentinel (`frontend/hooks/useInfiniteSentinel.ts`) were built for the memory work. Reuse the same approach for the content list: let `getAll` take `skip` and `limit`, append pages as the sentinel becomes visible, and show `total` from the first response.

```text
-  getAll: async () => {
-    return fetchWithAuth(`${API_BASE_URL}/content`);
+  getAll: async (params?: { skip?: number; limit?: number }) => {
+    const q = new URLSearchParams(
+      Object.entries(params ?? {}).map(([k, v]) => [k, String(v)]));
+    return fetchWithAuth(`${API_BASE_URL}/content?${q}`);
   },
```

The larger part of the work is the component: filters and sorting in the dashboard currently run over the loaded array (`filteredContents`), so they must either move to the server or run over pages loaded so far with a visible "load more". Effort: 1 day with tests. Note the server's recommended-feed endpoint is also paged by `skip` and `limit`, so this is a client gap, not a missing API.

### Item 3: a worker killed mid-task leaves the item "processing" forever (open)

**Where.** The extraction task marks an item `processing` and commits before it fetches anything:

{% fig "tasks/extraction.py: the recovery sweep only looks at pending items" %}
<!--src content-queue-backend/app/tasks/extraction.py#1766-1774-->
```python
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        stale = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.processing_status == "pending",
                ContentItem.created_at < cutoff,
            )
            .all()
        )
```
{% endfig %}

Celery acknowledges a message before running the task by default, and the project sets neither `task_acks_late` nor `task_reject_on_worker_lost` (a search of `app/` finds neither). A worker killed mid-task loses the message, and the only sweep re-dispatches items that are still `pending`.

**Failing scenario (illustrative).** A deploy restarts the worker at 14:00:00. A user saved an article at 13:59:40; the worker set it to `processing` at 13:59:42 and was fetching when the container stopped. The message was already acknowledged, so nothing redelivers it. The row stays `processing`. The sweep at 14:05 ignores it (it only selects `pending`). The reader sees a spinner that never ends. With one deploy a week and a handful of saves in flight each time, this is a few stuck items a month, each requiring a manual fix.

**Smallest fix.** Widen the sweep to items stuck in `processing` for longer than any task can legitimately run (the task limit is 30 minutes, though that limit is not enforced; see below), using `updated_at`, which the database refreshes on every write:

```text
-            .filter(
-                ContentItem.processing_status == "pending",
-                ContentItem.created_at < cutoff,
-            )
+            .filter(or_(
+                and_(ContentItem.processing_status == "pending",
+                     ContentItem.created_at < cutoff),
+                and_(ContentItem.processing_status == "processing",
+                     ContentItem.updated_at < processing_cutoff),  # 35 min
+            ))
```

Extraction overwrites fields on each run, so re-running it is safe for items the user has not edited (inferred from the code). Pair it with `acks_late` for the idempotent tasks later. Effort: 0.5 d with a test that leaves an item in `processing` past the cutoff and checks it is re-dispatched.

## The register

Ranked within tiers. "Evidence" is where to look today.

### Tier 1: security and data loss

| # | Gap | Status | Evidence | Impact | Fix | Effort |
|---|---|---|---|---|---|---|
| 1 | Server-side request forgery in URL fetching | Open | `core/fetch.py` (above); no address validation anywhere in `app/` | Worker can be pointed at internal addresses | Validate each hop in `fetch_bounded` | 0.5 d |
| 2 | Unsafe defaults: `DEBUG` is `True`, no `SECRET_KEY` check | Open | `core/config.py` line 23 (`DEBUG: bool = True`); `SECRET_KEY: str` with no validator | If `DEBUG` is missing from a service's variables, the app mounts the dev-only sample-PDF routes, and auth cookies are issued without the `Secure` flag (`core/auth_cookies.py` sets `_COOKIE_SECURE = not settings.DEBUG`). A short key starts without error and makes tokens forgeable | Default `DEBUG` to `False`; reject a key under 32 characters when not debugging | 1 h |
| 3 | Backups and a tested restore | Not verified | No backup procedure in the repository; the Railway side cannot be read from here | A bad migration or deletion loses everything | Enable a backup policy, restore once into a scratch environment, write down the time | 2 h |
| 4 | Account and item deletion leave uploaded PDFs in object storage | Open (only when S3 is enabled) | `api/auth.py` `delete_current_user` calls `db.delete`; `core/storage.py` has `upload_pdf` and `presign_url` and no delete; the 7-day cleanup task hard-deletes rows only. `S3_STORAGE_ENABLED` defaults to `False` | Privacy and storage cost once the flag is on | Collect `s3_key` values before deleting, batch-delete objects | 0.5 d |
| 5 | Trashed items reachable through the MCP query tools | Open | `deleted_at` appears in `app/mcp/tools/lists.py` and `highlights.py` but nowhere in `query.py` or `synthesis.py` | Items a user trashed may be returned by text-to-SQL or synthesis | Add `deleted_at` to the schema prompt and require `deleted_at IS NULL` in the guard | 2 h |
| 5a | Text-to-SQL user-isolation check is weaker than intended | Open | `_enforce_user_isolation` in `mcp/tools/query.py` accepts a statement when an equality with `:user_id` exists anywhere in the parsed tree (chapter 3.5) | Tenant isolation for the MCP query tool depends on what the model writes | Require the predicate as a top-level AND term for every user-scoped table, or query through per-user views or row-level security; add the cases as tests | 0.5 d |
| 5b | Public profile response carries more fields than the page needs | Open | `api/endpoints/public.py` returns `UserResponse` (`schemas/user.py`) to an unauthenticated request; a private profile returns 403 and a missing one 404 (chapter 4.3) | Personal data is returned to anyone who knows a public username, and the status codes reveal which usernames exist | A separate public response schema; the same status for private and missing profiles | 1 h |

### Tier 2: user-visible defects and recovery holes

| # | Gap | Status | Evidence | Impact | Fix | Effort |
|---|---|---|---|---|---|---|
| 6 | Dashboard shows only the newest 50 items | Open | walkthrough item 2 | Looks like lost data above 50 saves | Page the content list like the list page | 1 d |
| 7 | Items stuck in `processing` | Open | walkthrough item 3 | Permanent spinner after a worker restart | Sweep `processing`; `acks_late` for idempotent tasks | 0.5 d |
| 8 | No sweep for an item that is `completed` but has no embedding | Open | `process_all_missing_embeddings` selects highlights only (`tasks/embedding.py`); the chunk sweep requires an item embedding; development database: 1 completed item with text and no embedding, which nothing will retry | The item is not found by meaning search and never gets chunks | Add an item-level sweep with a lock and a per-run cap | 2 h |
| 9 | Duplicate-save race returns 500 | Partly | The unique index exists (migration `5534fbac2811`). `ingest_url` does a lookup, then inserts, and nothing catches `IntegrityError` outside `core/entity_graph.py` | Two simultaneous saves of one URL: one commits, the other gets a 500 instead of the 409 clients expect. No duplicate is stored | Catch `IntegrityError`, roll back, re-run the lookup, raise `DuplicateContentError` | 1 h |
| 10 | Registration race and email case | Open | `api/auth.py` `register` queries `User.email == user_data.email` with no lowercasing and no `IntegrityError` handler | `A@x.com` and `a@x.com` can be two accounts; a race can 500 | Lowercase email everywhere, unique index on `lower(email)`, catch the error | 0.5 d |
| 11 | Extraction shows `failed` while it is still retrying | Open | In `tasks/extraction.py` the HTTP-error and timeout handlers write `failed`, commit, then call `self.retry`; the next attempt resets to `processing` | For 1 to 4 minutes a retrying item looks permanently failed | Keep `processing` until retries are exhausted | 1 h |
| 12 | No frontend error boundaries | Open | No error, global-error or not-found route files under `frontend/app/` | A render error shows the framework's default page | Add the three route files, report to Sentry | 2 h |
| 12a | Transient-error retries do not reach the OpenAI structured-output path | Open | Chapter 3.2: `instructor` 1.15.1 raises `InstructorRetryException`, which is not in `TRANSIENT_CHAT_ERRORS` (`core/llm_client.py`) | Tagging and analysis end as "failed" or with no tags during a rate-limit window and are not retried | Unwrap the exception to its cause in `structured_chat`, or limit instructor's retries to validation errors; test with a 429 | 2 h |
| 12b | Research memory lookup fails silently on psycopg 3 | Open | `_fetch_research_memory` in `tasks/research.py` (an `interval` placeholder); the error is swallowed (chapter 3.4) | The planner never receives past research | Cast or build the interval in SQL; log the failure at warning level | 1 h |
| 12c | `synthesize_topic` never includes highlights | Open | `mcp/tools/synthesis.py`: a `user_id` key is missing (chapter 2.3) | Synthesis answers lack the user's highlights | Pass the key; add a test | 1 h |
| 12d | Research budgets are not enforced, and `partial` is overwritten by `done` | Open | `max_tokens`, `max_subagents`, `timeout_s` are never checked; chapter 3.4 | Cost and final status are not what the schema implies | Enforce in the lead task; keep the `partial` status | 0.5 d |

### Tier 3: hardening existing mechanisms

| # | Gap | Status | Evidence | Impact | Fix | Effort |
|---|---|---|---|---|---|---|
| 13 | Worker pool is solo; time limits are not enforced | Open | `ARCHITECTURE.md` documents the start command (`--pool=solo --beat`); a live test of the solo pool is captured in [Distributed-systems review](/reliability/distributed-systems/): a task with a 2 s soft limit ran its full 8 s | A hung fetch or LLM call stalls the only worker slot and the beat schedule that shares it | Move I/O tasks to a gevent or threads pool, PDF work to a prefork worker, beat to its own service; first make `DatabaseTask`'s session per task | 1 to 2 d |
| 14 | Rate limits are keyed by address, not user, and the check is not atomic | Open | `middleware/rate_limit.py`: the code reads `request.state.user`, which no code in `app/` ever sets, so every key falls through to `request.client.host`; the limit check and the insert are two pipeline calls | Behind a proxy all anonymous traffic can share one bucket (inferred; the forwarded-address setting is not visible in the repository); limits do not follow a signed-in user | Key by user id after auth; one Lua script for check-and-add | 0.5 d |
| 15 | `/health` always returns 200 | Open | `main.py` `health_check` returns a constant | A replica with a dead database stays in rotation | Add a readiness route that pings the database and Redis with short timeouts; keep `/health` as liveness | 1 h |
| 16 | One queue for everything | Open | No `task_routes` or named queues in `app/` | A 5-minute PDF job or a nightly fan-out delays a user's save | `interactive` and `bulk` queues; sweeps skip when the queue is deep | 0.5 d |
| 17 | No circuit breaker, no retry jitter | Open | `llm_client.py` `chat` tries the primary every call, retries once after 1 s, then falls back; tasks retry with `60 * 2**n` and no jitter | During a provider outage every call waits out the primary's 30 s timeout; retries cluster | Redis flag that skips the primary for 60 s; `retry_jitter` | 0.5 d |
| 18 | Run locks have no owner token | Open | `core/task_locks.py` stores `"1"` and `release_run_lock` deletes unconditionally | A run that outlives its TTL can release the next run's lock | Random token, compare-and-delete | 1 h |
| 19 | Broker, results, cache, locks and limits share one Redis | Open (policy not verified) | One `REDIS_URL` for all (`celery_app.py`, `task_locks.py`, `rate_limit.py`) | A cache-style eviction policy would silently drop queued tasks; the eviction setting is not visible here | Separate broker from cache | 0.5 d |
| 20 | No row-level security | Open | No policy statements in `alembic/versions/` | Tenant isolation rests on every query carrying `user_id` | `SET LOCAL` per request plus policies | 2 d |
| 21 | Public pages are not cached | Open | No `Cache-Control` header set anywhere in `app/` | A popular public profile goes straight to Postgres | Short `s-maxage` at the CDN | 0.5 d |
| 22 | No dependency scanning; no coverage floor | Open | Workflows have no audit step and there is no Dependabot file; `pyproject.toml` has `--cov-fail-under=0` | Known-vulnerable packages go unnoticed | `pip-audit`, `npm audit`, Dependabot, a floor at current coverage | 2 h |

### Tier 4: scale and quality, decide on growth

| # | Gap | Status | Evidence | Impact | Fix | Effort |
|---|---|---|---|---|---|---|
| 23 | Vector search scaling | Partly | The main semantic lane scans a user's chunks exactly (`core/hybrid_search.py`, `MAX(1 - (cc.embedding <=> ...))`, confirmed with `EXPLAIN` on the development database: 557 rows); the HNSW indexes serve the `ORDER BY ... LIMIT` queries (connections, similar, research); no `ef_search` or iterative-scan setting appears in `app/` | Exact lane is linear in a user's chunks, fine at today's size. Filtered HNSW queries can return too few rows for small users once the table is large | Benchmark at 10x and 100x; set iterative scan; route small users to exact search | 1 d |
| 24 | Chunk parameters never tuned | Open | Constants in `tasks/chunk_embeddings.py`; the retrieval evaluation compares designs, not sizes | Unknown retrieval quality left on the table | Sweep 200/350/600 tokens against the existing evaluation | 1 d |
| 25 | Entity search lane disabled | By decision | `ENTITY_SEARCH_ENABLED` defaults to `False` (`core/config.py`) because the evaluation found it below chunks-only | Feature unused | Re-evaluate after extraction quality improves | open-ended |
| 26 | Thin frontend tests | Open | 13 test files for about 30,000 lines of TypeScript; no end-to-end suite | UI regressions caught by users | Tests for sign-in, save and reader flows; one Playwright smoke test | 1 to 2 d |
| 27 | Whole-row loads remain in three places | Partly | See [Memory](/reliability/memory/): `GET /content`, public listing, and the cleanup task | Linear memory per request or run | `defer_heavy_columns()` on each | 2 h |

### Fixed on main (kept so they are not re-opened)

| Gap | Where it was closed |
|---|---|
| Unbounded downloads (a 100 MB zip cost 1.25 GB) | `core/fetch.py`: byte cap, binary types skipped, 60 s wall-clock cap |
| Library scan on every save | Indexed lookup after migration `a7d3e9c15b42` |
| Chunk embeddings held all at once | Batches of 100 in `tasks/chunk_embeddings.py` |
| Items stuck `pending` after a broker hiccup | `recover_stale_pending_items` sweep, every 5 minutes |
| `max_retries` that never fired; silent empty tag list | Transient errors re-raised in `article_analysis.py` and `tagging.py` |
| Refresh-token double-use race | Atomic conditional `UPDATE` in `api/auth.py`; `GETDEL` for the MCP flow |
| Registration in seven commits | One commit, dispatches after it |
| Pool exhaustion as a 30 s hang | `pool_timeout=10` in `core/database.py` |
| Beat tasks without overlap locks | Locks on the fan-out tasks |
| Dispatch storm (6.5 million queued messages) | Per-article dispatch removed |
| Duplicate-save data race | Unique index `uq_content_items_user_url_active` (the 500 response remains: item 9) |

## How it is built here

The gaps cluster in a few files, which is useful when planning the work:

| Area | Files |
|---|---|
| Fetch and extraction | `content-queue-backend/app/core/fetch.py`, `content-queue-backend/app/tasks/extraction.py` |
| Worker configuration | `content-queue-backend/app/core/celery_app.py`, `content-queue-backend/app/tasks/base.py`, `content-queue-backend/nixpacks.celery.toml` (documents the start command; Railway reads the dashboard setting) |
| Config and cookies | `content-queue-backend/app/core/config.py`, `content-queue-backend/app/core/auth_cookies.py`, `content-queue-backend/app/main.py` |
| Rate limits and locks | `content-queue-backend/app/middleware/rate_limit.py`, `content-queue-backend/app/core/task_locks.py` |
| Dashboard | `frontend/components/ContentList.tsx`, `frontend/lib/api.ts`, `frontend/hooks/usePagedListContent.ts` |

The pieces that make the system recoverable are the same ones the register keeps extending: a status column on the row, a beat sweep that re-dispatches whatever is stuck, and idempotent tasks. Items 7, 8 and 11 are all "extend the sweep or the status machine"; items 17 and 18 are "make an existing guard sturdier".

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Rank by (impact times likelihood) over effort, security and user-visible first | Strict severity order; effort order | Strict severity puts a 2-day row-level-security project ahead of a 1-hour config fix. Effort order buries the SSRF fix. |
| Keep "fixed on main" in the register | Delete fixed items | Older write-ups still list them; the register says what changed and where. |
| Mark "not verified" instead of guessing | Assume production matches the defaults | Backups, eviction policy and proxy headers live in dashboards. Guessing in either direction misleads. |
| Fail open for rate limits, locks and budgets (existing policy) | Fail closed | A read-later app prefers availability. The register does not change this; it only asks that the checks be atomic and keyed correctly. |
| Fix the sweep before enabling `acks_late` | `acks_late` first | A sweep covers every task and survives a lost message; `acks_late` needs a check that each task is safe to run twice. |

## How it fails

The register is not hypothetical. Gaps have already caused incidents recorded in the project:

| Incident | Gap behind it | Status |
|---|---|---|
| 6.5 million queued messages found by accident in July 2026 | One dispatch per article, no queue-depth visibility | Dispatch fixed; queue-depth visibility still absent (no metrics are emitted) |
| An item stuck `pending` after Redis restarted | Unguarded dispatch | Fixed by the sweep; the same shape remains for `processing` (item 7) and for embeddings (item 8) |
| A task with `max_retries=3` that never retried | Exception swallowed | Fixed for two tasks; the class is not guarded by a test |
| A 100 MB link costing 1.25 GB | No download cap | Fixed |

What has not happened yet is as informative: there is no recorded SSRF abuse, no data loss from the missing backups, and no report of the 50-item dashboard limit. Absence of reports is not evidence of safety, which is why the register is ordered by likelihood and impact rather than by what has already hurt.

## What is weak or missing

Weaknesses in the register itself, ranked:

1. **It is a snapshot.** Checked on 2026-10-25; items 3, 5, 14 and 19 depend on settings that were not visible. Each should be turned into a one-line check run against the live environment by its owner.
2. **Efforts are estimates.** They come from reading the code, not from doing the work. Items 13 and 20 are the least certain.
3. **Nothing alerts on these conditions.** There are no metrics or alerts, so items 7 and 8 would be found by a user. The ingestion-freshness measure described in [Distributed-systems review](/reliability/distributed-systems/) would find both.
4. **No test guards the classes.** A static test that every task with `max_retries` can raise, or that every beat sweep covers its status, would stop whole categories from returning.

## The next three months

Ordered so each step makes the next safer. Efforts are the estimates above.

| When | Work | Items |
|---|---|---|
| Week 1 | One-hour fixes with no design decisions: safe defaults and key check; catch the duplicate-save error; readiness route; keep `processing` during retries. In parallel, check backups and the Redis eviction setting in the dashboards and write the results down | 2, 9, 15, 11, 3, 19 |
| Weeks 2 to 3 | SSRF validation in `fetch_bounded` with tests; widen the recovery sweep to `processing`; add the item-level embedding sweep | 1, 7, 8 |
| Weeks 3 to 4 | Page the dashboard list and the add-to-list dialog | 6 |
| Month 2 | Worker tier as one series: per-task database session, I/O pool plus a prefork PDF worker, beat as its own service, two queues. Then re-run the failure-injection tests from [Distributed-systems review](/reliability/distributed-systems/) | 13, 16 |
| Month 2 | Rate-limit identifier and atomic check; lock tokens; breaker and jitter | 14, 18, 17 |
| Month 3 | Metrics and alerts (ingestion freshness first); registration fix; error boundaries; dependency scanning; S3 cleanup before the storage flag is turned on | 12, 10, 22, 4 |
| Month 3 | Vector-search benchmark and chunk-size sweep; decide on row-level security and a separate cache Redis from the measured growth | 23, 24, 20, 19 |

The plan stops short of re-architecture on purpose. The measured production baseline (see [Memory](/reliability/memory/)) shows a nearly idle system; none of the tier 4 items is urgent until libraries or traffic grow.

{% say %}
I keep a ranked list of what is wrong, and every item points at code I re-checked. The top of the list is small and specific. The worker will fetch any address a user saves, because the one download function never checks where it points. A worker killed mid-task leaves an item processing forever, because the recovery sweep only looks at pending ones. The dashboard fetches one page of 50 and never asks for more, so a big library looks truncated. And two settings default to unsafe values. Each fix is an hour to a day. After those, the work is hardening the worker: a real pool so time limits work, separate queues, sturdier locks and rate limits, then metrics and alerts. Things I could not see from the repository, like backups, I mark as not verified rather than guess.
{% endsay %}

## Check yourself

{% qa "Why is the SSRF fix placed in `fetch_bounded` and not at each of the five call sites?" %}
All five sites already call `fetch_bounded`, so one check covers every download, including future ones. Per-site checks are easy to miss, and a redirect must be re-validated at each hop, which only the function that makes the request can do.
{% endqa %}

{% qa "An item sits in `processing` for two hours. Why does the existing recovery sweep not help, and what is the smallest change?" %}
The sweep selects only items still `pending` for 10 minutes. This item left `pending` when the task started. Widening the filter to `processing` items whose `updated_at` is older than a cutoff above the task limit re-dispatches it; the task is safe to re-run for items the user has not edited.
{% endqa %}

{% qa "The dashboard shows \"50 / 120 items\". Is any data lost, and why is this not an API bug?" %}
No data is lost; older items are still searchable. The endpoint pages correctly (50 by default, 100 at most) and returns the total. The client calls it once with no parameters and never requests the next page, so the fix is in the client.
{% endqa %}

{% qa "Why is `DEBUG` defaulting to True more than a development convenience?" %}
Auth cookies are marked `Secure` only when `DEBUG` is false, and the dev-only routes mount when it is true. A production service that lacks the variable inherits the unsafe default silently. The fix is to make the safe value the default and the unsafe one explicit.
{% endqa %}

{% qa "Why is vector search scaling marked partly and not open?" %}
The main lane is exact, so it has no recall problem and its cost is linear in one user's chunks, which is small today. The risk is in the queries that rely on the approximate index with a per-user filter, which is not triggered at current size. It needs a benchmark at larger sizes, not an immediate change.
{% endqa %}

{% qa "What would you do in week one, and why those items?" %}
The one-hour fixes (safe defaults, catching the duplicate-save error, a readiness route, keeping `processing` during retries) need no design decisions and remove real defects. Alongside them, check backups and the Redis eviction policy, because those are the cases where being wrong costs the most and they cannot be seen from the repository.
{% endqa %}
