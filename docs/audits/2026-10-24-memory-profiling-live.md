# Backend memory profiling on the live local stack, 2026-10-24

Second pass. The first pass (`2026-10-24-memory-measurements.md`) used SQLite and an offline Celery estimate. This pass uses real Postgres 16 + pgvector (docker, localhost:5433, scratch database `memprof`), real Redis 7 (docker, logical db 15), the real FastAPI app (`uvicorn app.main:app`), and the real Celery worker started as in production (`celery -A app.core.celery_app worker --loglevel=info --pool=solo --beat`). Platform: macOS (Darwin 25.4, arm64), Python 3.11.12, backend `.venv`. Working tree included the uncommitted `_MAX_CHUNK_CHARS` change in `app/tasks/chunk_embeddings.py`.

Isolation: every env var was overridden on the command line (`DATABASE_URL` -> memprof, `REDIS_URL` -> db 15, `SECRET_KEY` test string, `OPENAI_API_KEY=dummy`, `OPENAI_BASE_URL` -> local fake server, all third-party keys blank, `PREFECT_ENABLED=false`, `S3_STORAGE_ENABLED=false`). `settings` was printed redacted before the first run to confirm the env overrides `.env`. `.env` was never read. A local fake OpenAI server (embeddings + chat/tool-call responses generated from the request's JSON schema) and a local static page/PDF server stood in for all external services. No traffic left the machine except `pip install` and the `python:3.11-slim` image pull for the Linux check. Everything started was stopped; `memprof` dropped; Redis db 15 flushed; the two docker containers left running.

Scratch scripts and raw CSV/log output: `<scratch>/live/`.

## 1. Executive summary

Ranked by measured peak, with the owning process:

| Rank | What | Peak (measured) | Process | Notes |
|---|---|---|---|---|
| 1 | Large page through the full ingest pipeline (20 MB page) | 1.88-2.14 GB (RSS 1,878-1,996 MB, footprint 1,968-2,141 MB), from a 417-467 MB footprint baseline | Worker parent | Peak is in the **chunk-embedding stage**, not trafilatura. 5 MB page: 738-868 MB. |
| 2 | `/content/recommended` (loads every unread item as a full ORM row) | +1,085 MB for a 2,003-item library | API | Response was 0.03 MB. |
| 3 | `find_existing_active_item` fallback, run on every new-URL `POST /content` | +552 MB (function level), +625 MB (end to end, fresh API process), N=2,003; repeated calls pushed the API to 1.5 GB and it did not shrink | API | `load_only` variant: +5 MB. |
| 4 | `GET /lists/{id}/content` (no pagination, full rows) | +838 MB for a 2,003-item list | API | Response 2.56 MB. |
| 5 | PDF (YOLO subprocess) | child 657 MB and 689 MB RSS, plus parent footprint 667-687 MB, so about 1.34-1.38 GB combined | YOLO child + worker parent | Child exits and releases its memory. |
| 6 | Large-page extraction alone (trafilatura + xml_to_html + bs4) | 20 MB page: +495 MB over baseline in the live worker; 624-642 MB standalone macOS; 497 MB standalone Linux | Worker parent | 5 MB page: +165-195 MB standalone. |
| 7 | Beat/sweep tasks | at most +80 MB (`cleanup_old_deleted_items`, 303 rows); the others +4 to +45 MB | Worker parent | |
| 8 | Process baselines | worker idle footprint 120 MB (RSS 141 MB right after start); API 179 MB after first requests | both | |
| 9 | Redis db 15 | 0.5 MB for about 1,000 keys | Redis | Server-wide `used_memory` is 7.15 GB, none of it in db 15 (see section 3.5). |
| 10 | Postgres container | 322 MiB idle; 419-541 MiB under load (includes page cache) | Postgres | Server memory, separate from app memory. |

Hypotheses from the first pass:

| First-pass hypothesis | Live result |
|---|---|
| H2: `find_existing_active_item` fallback loads full rows | **Confirmed, and the first pass understated it about 3.8x.** SQLite mimic: 142 MB at N=2,000. Real Postgres + real 32-column model: 552 MB at N=2,003. Cause: `search_vector` (tsvector) and `embedding` are loaded along with `full_text`. The same pattern exists in `/content/recommended`, `/lists/{id}/content` and `cleanup_old_deleted_items`. |
| H3: extraction peak is 37-40x page size and is the main large-page cost | **Partly overturned.** Standalone macOS ratio was 31x at 20 MB (624 MB), Linux 25x (497 MB). In the live worker the extraction stage added about 495 MB over baseline, but the **chunk + chunk-embedding stage peaked higher** (RSS 1,878 MB for 20 MB, 866 MB for 5 MB) because 7,634 chunk embeddings (5 MB page: 1,916) are requested in one call and inserted as ORM objects in one session. |
| H4: chunking is a moderate cost | Underestimated as a stage. Chunking plus embedding for the 7,634-chunk page dominates the worker peak. |
| H5: Celery results are small in Redis | **Confirmed.** 424 B for a normal result, 522 B average over 989 keys, 6.2 KB for a failed task carrying an error string. The larger Redis item is the query-embedding cache: 41 KB per `qemb:*` key (TTL 3,600 s). |
| H6: no leak in a loop | Not re-run as a loop. Live runs show **no leak but also no return to baseline** after large pages (section 4). |
| YOLO subprocess | **Confirmed**: 657 and 689 MB child RSS (first pass 631 and 689). The parent also gains about 15-20 MB of footprint on the first PDF (fitz). |
| H1: import baselines | Consistent. Worker idle footprint 120 MB, API 179 MB after the first requests. |

Two measurement caveats that affect how to read the numbers:
- On macOS, the memory compressor shrinks the RSS of idle processes. A worker with a 231 MB footprint showed 45 MB RSS a minute later. "Settled" values below therefore use `footprint` (phys_footprint, includes compressed pages), sampled at 1 Hz. Peaks use RSS at 10-20 Hz and are not affected by compression during activity.
- The seeded `search_vector` values come from a small synthetic vocabulary (about 51 KB per row of text form for 42 KB articles). Real tsvectors may be smaller or larger. This changes how the 552 MB splits between columns, not the order of magnitude.

## 2. Table of peak and settled memory per scenario

All MB. "Settled" = footprint at the end of the settle window unless noted. Worker baseline values are footprint at the start of that scenario.

### API process

| Scenario | Baseline | Peak | After | Notes |
|---|---|---|---|---|
| `POST /content`, N=100 library, fresh API, 1 request | 179 | 207 (+29) | 207 | 0.16 s |
| `POST /content`, N=500 | 180 | 317 (+137) | 317 | 0.41 s |
| `POST /content`, N=2,003 | 180 | 804 (+625) | 780 | 1.42 s |
| 20 sequential `POST /content`, N=2,003, one process | 77 (idle, before any request) | 1,544 | 1,528 after 10 s; about 1,260 later (RSS, partly compressed) | mean latency 1.11 s |
| 20 more in batches of 5 concurrent, same process | 1,527 | 1,545 | 1,336-1,370 | already at the plateau |
| `GET /content?limit=50` | 179 | 207 (+27) | 207 | response 0.06 MB |
| `GET /content?limit=100` | 179 | 234 (+55) | 234 | 0.13 MB |
| `GET /content/recommended?limit=20` | 179 | 1,264 (+1,085) | 1,264 | 0.03 MB, 1.2 s |
| `GET /lists/{id}/content` (2,003 members) | 179 | 1,017 (+838) | 1,017 | 2.56 MB, 1.1 s |
| `GET /search/semantic?limit=50&mode=auto` | 179 | 232 (+52) | 232 | 0.5 s |
| `GET /public/u/{u}/content?limit=100` | 180 | 221 (+42) | 221 | |
| `GET /search/{id}/similar` | 179 | 180 (+1) | 180 | |
| `GET /content/{id}` | 180 | 180 (0) | 180 | |
| `GET /analytics/stats`, `GET /content/tags` | 179-180 | no change | | SQL aggregates |

### Worker (Celery solo + beat), parent process

| Scenario | RSS before | RSS peak | Footprint before | Footprint peak | Footprint settled | Notes |
|---|---|---|---|---|---|---|
| Idle after start | 141 (73 after compression) | | 120 | | 120 | beat runs as a separate 9 MB child |
| (a) 20 x 100 KB pages | 144 | 274 | 120 | 237 | 231 (RSS 45) | whole 20-item pipeline 39 s |
| (b1) 1 MB page, run 1 | 43 | 376 | 231 | 346 | 263 | 18 s |
| (b2) 5 MB page, run 1 | 336 | 868 | 263 | 738 | 467 | 24 s; RSS 723 at end of 60 s |
| (b3) 20 MB page, run 1 | 718 | 1,996 | 467 | 2,141 | 652 | 71 s; 150 s settle |
| (e1) 1 MB, fresh worker, run 2 | 97 | 371 | 166 | 315 | 249 | |
| (e2) 5 MB, run 2 | 294 | 866 | 249 | 738 | 417 | |
| (e3) 20 MB, run 2 | 439 | 1,878 | 417 | 1,968 | 452 | 75 s; RSS 635 at end |
| (d) 20 x 100 KB after the large pages | 49 (compressed) | 546 | 641 | 652 | 652 | no growth |

### PDFs (YOLO subprocess)

| PDF | Parent footprint before / peak / settled | Child peak RSS | Parent + child | Pipeline time |
|---|---|---|---|---|
| `TabPFN_copy.pdf` (1.1 MB, 8 pages) | 652 / 667 / 667 | 657 | about 1,324 | 24.4 s |
| `3d-printed-protein.pdf` (8.8 MB, 6 pages) | 667 / 687 / 641 | 689 | about 1,376 | 24.5 s |

The summed-RSS "combined peak" from the sampler (867 and 810 MB) is lower because the idle parent's RSS was compressed at that moment; parent footprint plus child RSS is the better estimate.

### Standalone extraction (same code, macOS vs Linux)

| Page | macOS peak delta | Linux (python:3.11-slim) peak delta | macOS RSS retained after freeing everything | Linux retained |
|---|---|---|---|---|
| 1 MB | 42 | 28 | 36 | 19 |
| 5 MB | 195 | 132 | 157 | 71 |
| 20 MB | 642 | 497 | 482 | 336 |

### Beat and sweep tasks (separate process, same imports as the worker, 129 MB baseline)

| Task | Wall | Peak delta | Result |
|---|---|---|---|
| `process_all_missing_embeddings` | 0.09 s | +5.8 MB | dispatched 3 |
| `process_all_missing_chunks` | 0.16 s | +4.2 MB | dispatched 100 (LIMIT 100) |
| `recover_stale_pending_items` | 0.27 s | +8.9 MB | 302 recovered (rows without text) |
| `cluster_all_users_task` | 0.10 s | +5.9 MB | dispatched 6 |
| `cluster_user_tags_task` (1 user, 1,000 items, 300 tags) | 0.20 s | +40 MB | 21 clusters |
| `consolidate_all_users_task` | 0.11 s | +4.0 MB | dispatched 6 |
| `consolidate_memory_task` (1 user) | 0.34 s | +33 MB | |
| `embed_new_entities_beat_task` | 1.26 s | +44.5 MB | 300 embedded (100 per user batch) |
| `backfill_missing_entities_task` | 0.10 s | +4.2 MB | queued 50 |
| `deduplicate_entities_task` (3 users, 250 embedded entities each) | 6.41 s | +19 MB | 900 candidate pairs |
| `cleanup_old_deleted_items` | 0.69 s | +80 MB | 303 rows hard-deleted |

### Redis and Postgres

See sections 3.5 and 3.6.

## 3. Method and raw numbers

### 3.0 Setup

- Scratch DB `memprof` via `alembic upgrade heads`. The HNSW indexes are not created by alembic (migration `f4c8b6e21a3d` is a no-op; `scripts/post_deploy.py` builds them), so searches ran as sequential scans until the indexes were created by hand in step 3.6.
- Fake OpenAI server: `/v1/embeddings` returns deterministic 1,536-dim unit vectors (20 cluster centres plus noise, seeded by input hash); `/v1/chat/completions` returns a tool call whose arguments are generated from the request's JSON schema (instructor path) or canned text. First version produced single-word tags that `ArticleAnalysisResponse` rejects (25 failed analyses, each retried 3 times, 6.2 KB failed-result keys in Redis); fixed before steps 4-5. The fake server accepts any number of embedding inputs per call; real OpenAI limits a request to 2,048 inputs.
- Static server: synthetic article pages of 100 KB, 1 MB, 5 MB, 20 MB (nav, 60-item menu, scripts, nested divs, relative and absolute images, sidebar, footer) and the two repo PDFs.
- Seeding (COPY): `u1` 2,000 items (all embedded, 30% with 5 chunks, 20% with highlights), `s100`, `s500`; `u2`-`u4` 1,000 items each (about 54% embedded, 30% of those with chunks, 10% pending, 10% soft-deleted 30 days ago, 300-tag vocabulary with embeddings, 500 entities each with half embedded). Items carry about 42 KB `full_text`, 1,536-dim embeddings, tags, auto_tags, summaries. Row size 35 KB compressed in Postgres; text form of `search_vector` averages 51.5 KB, `embedding` 14.4 KB.
- API: `uvicorn app.main:app --port 8010` (port 8000 was already held by a process outside this task and was not touched). Registration is rate limited (5 per minute per IP), so `u1` was registered through the endpoint and the other users were inserted directly with the same password hash. `POST /content` is limited to 10 per minute; the driver deleted its own `ratelimit:POST:/content*` keys in db 15 before each request.
- Registering a user queues extraction tasks for two real newyorker.com URLs and a Discogs fetch. These were removed from Redis db 15 and from the database before any worker started.
- Sampling: `psutil` RSS of the target pid and its children at 10-20 Hz, plus `footprint <pid>` once per second, written to CSV.

### 3.1 `find_existing_active_item`, real schema (step 1)

Function-level (`h2_live.py`): fresh process per cell, `import app.main`, one warm query, then the real `find_existing_active_item` with a URL that has no match. `maxrss_delta` is `ru_maxrss` growth; tracemalloc measured in separate runs.

| N (active items) | Mode | RSS delta MB | tracemalloc peak MB | Time |
|---|---|---|---|---|
| 100 | full (real function) | 26.1 | 12.2 | 0.07 s |
| 100 | `load_only(id, original_url, is_archived)` | 0.4 | 0.4 | 0.01 s |
| 500 | full | 136.7 | 59.4 | 0.29 s |
| 500 | load_only | 1.3 | 1.3 | 0.01 s |
| 2,003 | full | 551.6 | 236.2 | 1.09 s |
| 2,003 | load_only | 5.0 | 4.7 | 0.02 s |

First-pass SQLite numbers for comparison: 7.2 / 39.6 / 141.9 MB. Real is 3.6-3.9x higher. About 275 KB RSS per item.

Attribution at N=2,003 (columns deferred one at a time):

| Deferred | RSS delta MB | Implied cost of the column |
|---|---|---|
| none | 552 | |
| `search_vector` | 288 | 263 MB |
| `full_text` | 354 | 198 MB |
| `embedding` | 473 | 79 MB |
| all three | 13.9 | remaining 14 MB |

`search_vector` is mapped on the model and loaded as text. It is the largest single column in this dataset (synthetic vocabulary caveat above).

End to end: see the API table in section 2. A single request on a fresh API process costs +29, +137, +625 MB for N=100, 500, 2,003. Matches the function-level numbers.

Repeated calls in one process (`h2_loop.py`, calls the function 8 times with a fresh session each, closing it each time; start RSS 168 MB):

| Call | RSS after (gc on) | RSS after (gc disabled) |
|---|---|---|
| 1 | 713 | 721 |
| 2 | 979 | 987 |
| 3 | 1,177 | 1,254 |
| 4 | 1,498 | 1,520 |
| 5 | 1,504 | 1,786 |
| 6-8 | 1,507-1,517 | 1,645, 561, 870 |

The freed ORM objects are not returned between calls; memory grows by about 265 MB per call for the first four calls, then plateaus near 1.5 GB (about 3x one call's peak). `gc.collect()` at the end did not lower it (1,517 MB). The plateau was also seen in the live API (1,544 MB peak, 1,260-1,528 MB afterwards).

### 3.2 Other full-row loads (step 2)

Grep of `app/` for `query(ContentItem)` in request paths. Measured through the live API against the 2,003-item user, one fresh API process per endpoint, first call's peak (3 calls each; later calls did not exceed the first).

| Endpoint (file) | Loads | Pages/limits? | Peak delta | Response |
|---|---|---|---|---|
| `GET /content/recommended` (`app/api/content.py`, `.all()` on every unread item, scored in Python, paginated after) | all unread, full rows | No (slices after loading) | +1,085 MB | 0.03 MB |
| `GET /lists/{id}/content` (`app/api/lists.py:123`) | all members, full rows | No | +838 MB | 2.56 MB |
| `GET /search/semantic` (`app/api/search.py`, `hybrid_search`) | result ids only, full rows | `limit` <= 50 | +52 MB at limit 50 | 0.07 MB |
| `GET /content` (`app/api/content.py:253`) | page, full rows | `limit` <= 100 | +27 MB (50), +55 MB (100) | 0.06-0.13 MB |
| `GET /public/u/{u}/content` (`app/api/endpoints/public.py:35`) | page, full rows | `limit` <= 100 | +42 MB (100) | 0.13 MB |
| `GET /search/{id}/similar`, `GET /content/{id}`, `/analytics/stats`, `/content/tags` | single row or SQL aggregate | n/a | 0 to +1 MB | |

`GET /content?limit=50` loads full rows (+27 MB) although the response schema returns about 1.2 KB per item; the extra columns are loaded and discarded. MCP tools (`app/mcp/tools/*`) were not run (OAuth required). By inspection: `get_list_content` (`lists.py:85`) applies `.limit()`; `get_content_item` loads one row; `app/mcp/tools/summarize.py` applies `.limit(max_items)`.

Other background-path full-row loads found in the same grep and measured in step 3.4: `recover_stale_pending_items` (`ContentItem` `.all()` of pending items, which have no text, +8.9 MB for 302 rows) and `cleanup_old_deleted_items` (`.all()` of items deleted over 7 days ago, +80 MB for 303 rows).

### 3.3 End-to-end worker pipeline (step 3)

Worker started as in production with its beat schedule file in the scratchpad (not the repo copy). Beat entries start one interval after the worker starts, so no burst at startup. Beat ticks at 5-minute intervals ran during the later scenarios and dispatched up to 100 `generate_chunk_embeddings` backfill tasks for seeded items (about 100 ms each, 14 chunks each); this overlaps with the 5 MB and 20 MB scenarios and adds noise of a few tens of MB at most. Each scenario submitted through `POST /content` and polled the DB for completion (status completed, embedding present) and an empty broker queue.

Stage times from the worker log (run 2, per page size):

| Page | `extract_metadata` | `extract_full_content` (trafilatura + xml_to_html + bs4) | `generate_embedding` | `analyze_article_task` | chunk stage (split + embed + insert) | Chunks |
|---|---|---|---|---|---|---|
| 1 MB | 0.1 s | 1.0 s | 0.12 s | 1.8 s | about 1.8 s | 382 |
| 5 MB | 0.4 s | 6.3 s | 0.2 s | 0.9 s | about 10 s | 1,916 |
| 20 MB | 2.0 s | 49.7 s | 1.0 s | 3.9 s | about 46 s | 7,634 |

Timeline for the 20 MB page (run 2, RSS): baseline 439 MB; 870-935 MB during extraction (t=10-40 s); 1,451-1,651 MB during chunking and embedding (t=74-99 s), peak 1,878 MB at t=71.7 s; 1,075 MB at t=119 s; 723 MB at t=181 s; 638 MB at the end of the window. The 5 MB page peaked at 866 MB at t=18.9 s, which is the chunk stage (extraction finished at t=10 s at about 550 MB).

Why the chunk stage is high (`app/tasks/chunk_embeddings.py:generate_chunk_embeddings`): `split_article_into_chunks` on the full HTML, a list of all prefixed texts, one `llm_client.embed` call carrying every chunk, the response's float lists (one 1,536-float Python list per chunk, about 49 KB each), then one `ContentChunk` ORM object per chunk added to a single session before one commit. For 7,634 chunks that is roughly 370 MB of float lists alone, plus response parsing and ORM state. Real OpenAI would refuse a call with more than 2,048 inputs, so the 5 MB and 20 MB pages would fail there (after the peak, with status `failed`); the fake server does not enforce this. The embed call's behavior against the real limit could not be tested locally.

PDF runs: YOLO model cold-loaded in the child on both runs (about 7 s of the 24 s). The log line "RSS before=...MB" in `extraction_implementations.py` prints bytes labelled as MB on macOS (`ru_maxrss` unit); cosmetic.

Small-page run (a): 20 pages, 4 tasks each (extract_metadata, extract_full_content, generate_embedding, analyze_article_task), plus chunk tasks. Worker footprint went from 120 to 231 MB and stayed there; part of that is first-use imports and caches (trafilatura, lxml, instructor) rather than data.

Drain test (not production traffic): 153 tasks dispatched by the sweeps (100 chunk, 50 analysis, 3 highlight-embedding), worker started afterwards: RSS 164 -> 208 MB, footprint 137 -> 177 -> 169 MB over 38 s.

### 3.4 Beat tasks (step 4)

Each task run once in a fresh process through `task.apply()` after importing `app.core.celery_app` (same imports as the worker), with 3 x 1,000-item users plus `u1`, `s100`, `s500` in the DB (6 users, 4,000-5,000 active items). `.delay()` calls inside the tasks still go to the real broker (db 15), so dispatched work was counted (`queued_by_task`) and then drained or discarded. Run-locks (`task_lock:*`) were cleared before each run. Table in section 2.

Observations:
- The fan-out/dispatch tasks (`process_all_missing_*`, `cluster_all_users_task`, `consolidate_all_users_task`, `backfill_missing_entities_task`) are SQL plus `.delay()` and cost 4-6 MB each.
- `recover_stale_pending_items`, `cleanup_old_deleted_items` and `cluster_user_tags_task` use `.all()`. The first two load whole `ContentItem` rows; `recover_stale_pending_items` is cheap only because pending rows have no `full_text`. `cleanup_old_deleted_items` loaded 303 rows for 80 MB (about 265 KB per row), so after an outage of a few days with many deletions it scales linearly. `cluster_user_tags` already uses `load_only(id, tags)`; its +40 MB comes from the tag-embedding matrix (300 tags here; the similarity matrix is quadratic in unique tag count).
- `embed_new_entities_beat_task` +44.5 MB for 300 embeddings: the response lists plus entity ORM rows; batch size 100 per user bounds it.
- `deduplicate_entities_task` runs one ANN query per embedded entity; memory stays low (+19 MB) but wall time is 6.4 s for 750 entities.
- Side observation: the scanner tasks hold their run-lock for a 10-minute TTL while beat fires every 5 minutes. In the live worker log the 5-minute ticks alternate between running and `skipped: lock_held`, so the effective period is 10 minutes. Not a memory issue.

### 3.5 Redis (step 5)

Redis db 15, measured after the pipeline runs, the sweeps, and a burst of 500 trivial tasks (`recover_orphaned_runs_task.delay()` x 500 with the worker running).

| Key family | Count | Total bytes | Per key (MEMORY USAGE) |
|---|---|---|---|
| `celery-task-meta-*` | 989 | 515,976 | 424 B typical; 522 B average; 6,248 B for failed analyses (error text) |
| `qemb:*` | 1 | 41,024 | 41,024 B (TTL 3,600 s) |
| `ratelimit:*` (zset) | 5 | 1,280 | 216-480 B |
| `task_lock:*` | 1 | 88 | 88 B |
| `llm_spend:*` | 6 | 720 | 120 B |
| `_kombu.binding.*` | 3 | 384 | 96-152 B |
| `unacked`, `unacked_index` | seen only while a task was in flight | | |

db 15 total: 1,004 keys, about 0.57 MB. After step 3 (336 task results) it was 344 keys, 0.30 MB. The largest keys by size were the failed-analysis results; `--bigkeys` equivalent via redis-py showed nothing else above 6.3 KB except the `qemb` key.

Server-wide `INFO memory`: `used_memory` 7.15 GB, `used_memory_rss` 6.30 GB, peak 7.15 GB, `maxmemory` 0, policy `noeviction`; container 6.3 GiB of 7.75 GiB. The value did not change during this run, and db 15 accounts for under 1 MB of it. Keyspace shows only `db0` with 6 keys besides db 15. The 7 GB therefore sits in `db0` (the user's own data) or is non-key memory. Per the task rules db 0 was not inspected. It is worth looking at separately: see section 6.

### 3.6 Postgres (step 6)

`docker stats` of `content_queue_postgres`, which reports cgroup memory including page cache; it is server memory, not app memory.

| Condition | Container memory |
|---|---|
| Idle (after loading 5,600 items, 19,000 chunks) | 322 MiB |
| During two runs of 8 x full 2,003-row loads (`h2_loop.py`) | 322 -> 419 MiB (+97) |
| 30 `/search/semantic?mode=full` calls, no vector index (sequential scan) | 331 -> 446 MiB (+115) |
| `CREATE INDEX ... USING hnsw` on items, chunks (19,246 vectors), highlights | 516 -> 541 MiB; settled 449 MiB |
| 30 searches with HNSW indexes present | 449 -> 472 MiB (+23) |

Settings: `shared_buffers` 128 MB, `work_mem` 4 MB, `max_connections` 100, default `maintenance_work_mem` (the chunk-index build printed "hnsw graph no longer fits into maintenance_work_mem after 9216 tuples"). Index sizes: `content_chunks_embedding_hnsw` 106 MB for 19,246 vectors (about 5.5 KB per vector), `content_items_embedding_hnsw` 4.0 MB, `highlights_embedding_hnsw` 1.8 MB. `content_items` total relation size 208 MB (heap 5.4 MB, the rest TOAST), database 416 MB.

The 97-115 MiB increases are mostly shared buffer and OS cache warming; the Postgres server does not hold the 550 MB that the API holds for the same query, because the result is streamed to the client and buffered in the client library (psycopg2) and then ORM objects.

### 3.7 Linux check (step 7, done)

`h3x.py` (same `xml_to_html` source sliced from `extraction.py`, `trafilatura==2.0.0`, `beautifulsoup4==4.14.3`, `lxml==6.0.2`) run in `python:3.11-slim` (Debian, glibc 2.41, Docker Desktop VM) and natively on macOS with the same script. Table in section 2. Linux peak is about 20-35% lower than macOS at each size, and freed memory is partly returned (1 MB: 19 vs 36 MB retained). At 20 MB the Linux process still held 336 MB more than before the work, with the page and every output freed and `gc.collect()` called. This was the standalone extraction only, not the full API or worker; the live pipeline was not run on Linux.

## 4. Retention and fragmentation findings

1. **No leak, but memory is not returned after large pages.** Worker footprint after each large page, 150 s after the pipeline finished:

   | Sequence (run 1) | Footprint at start | After | Retained |
   |---|---|---|---|
   | idle | 120 | | |
   | 20 x 100 KB | 120 | 231 | +111 (includes first-use imports) |
   | 1 MB | 231 | 263 | +32 |
   | 5 MB | 263 | 467 | +204 |
   | 20 MB | 467 | 652 | +185 |
   | 2 PDFs | 652 | 641-667 | about 0 to +15 |
   | 20 x 100 KB again | 641 | 652 | +11 |

   Run 2 (fresh worker): 166 -> 249 (1 MB) -> 417 (5 MB) -> 452 (20 MB, only +35). Retention after the 20 MB page varied from +35 to +185 MB between runs, so the figure is uncertain; retention after the 5 MB page was consistent (+168 to +204 MB).
2. The 20 x 100 KB run after the large pages did not grow the footprint (652 -> 652). Memory freed by a large page is reused by later small allocations, so the retained amount does not compound with more small pages. It does stay allocated to the worker process.
3. Standalone extraction retention (section 2): macOS 482 MB and Linux 336 MB after a 20 MB page with everything freed. Linux glibc returned more than macOS but not all.
4. API: after repeated full-row loads the API plateaued at about 1.5 GB (3x one call's peak) and stayed at 1.26-1.53 GB. Part of the multiplier is ORM objects that are not freed before the next request starts (gc disabled reached 1.9 GB before a collection dropped it). `gc.collect()` after the loop did not reduce RSS on macOS.
5. `worker_max_tasks_per_child=1000` does nothing under `--pool=solo`; the retained memory in point 1 stays until the worker restarts.
6. RSS is not reliable for "settled" values on macOS because of the memory compressor; footprint is. On Linux the compressor does not exist and RSS is the right metric.

## 5. Recommended fixes, ranked by memory saved per effort

| # | Fix | File / function | Measured saving | Effort |
|---|---|---|---|---|
| 1 | `load_only(ContentItem.id, ContentItem.original_url, ContentItem.is_archived)` in the fallback query (or add an indexed `normalized_url` column and drop the scan) | `content-queue-backend/app/services/content.py`, `find_existing_active_item` | 552 -> 5 MB at N=2,003; removes the 1.5 GB API plateau | one line |
| 2 | Mark `search_vector` as `deferred=True` on the model (nothing in the request paths reads it in Python; confirm with grep) | `content-queue-backend/app/models/content.py` | 263 of 552 MB at N=2,003 for every full-row load | one line, needs a grep for readers |
| 3 | Select only the fields the scorer and the response schema use, or defer `full_text`/`embedding`/`search_vector` | `app/api/content.py`, recommended endpoint (`/content/recommended`) | 1,085 -> well under 100 MB at N=2,003 | small |
| 4 | Add a limit/offset to the list-content endpoint and defer `full_text` for the list response | `app/api/lists.py`, `get_list_content` (line 123) | 838 MB at 2,003 members | small, changes API shape |
| 5 | Defer `full_text`, `embedding`, `search_vector` on list/page queries (`GET /content`, `/public/u/{u}/content`) | `app/api/content.py:253`, `app/api/endpoints/public.py:35` | 27 -> about 1 MB (50 rows); 55 -> about 2 MB (100 rows) | small |
| 6 | Cap chunk count and batch the embed call (at most 100-2,048 inputs) and the `ContentChunk` inserts (commit per batch); compute `html_to_plain` once | `app/tasks/chunk_embeddings.py`, `generate_chunk_embeddings` and `split_article_into_chunks` | worker peak on a 20 MB page was dominated by this stage (1,878 MB RSS peak vs about 935 MB in extraction); batching bounds float lists and ORM objects to one batch; also required to stay under OpenAI's 2,048-input limit | medium |
| 7 | Cap download size before extraction (stream with a byte limit, reject over about 5 MB) | `app/tasks/extraction.py`, `extract_full_content` (`requests.get`, line 599) and `extract_metadata` fetches | a 5 MB cap bounds the worker at the 5 MB measurement (738-868 MB peak) instead of the 20 MB one (1.9-2.1 GB); the capped case itself was not run | small |
| 8 | Delete old soft-deleted rows with a bulk `delete()` or load only ids | `app/tasks/cleanup.py`, `cleanup_old_deleted_items` | 80 MB for 303 rows -> a few MB | small |
| 9 | `load_only(ContentItem.id)` for the stale-pending sweep | `app/tasks/extraction.py`, `recover_stale_pending_items` | 8.9 MB per 302 rows; scales if pending rows carry text | one line |
| 10 | Return memory after large pages: run large-page extraction in a short-lived subprocess (as YOLO already does) or run the worker with prefork concurrency 1 and `--max-memory-per-child` | `app/tasks/extraction.py`; worker start command in Railway | removes the retained +35 to +204 MB after a 5-20 MB page | medium |
| 11 | Shrink the query-embedding cache entry (store float16 or base64 float32 bytes instead of JSON text) or shorten the TTL | `app/core/embedding_cache.py` | 41 KB -> about 6 KB per key; relevant only at high query volume (1,000 distinct queries/hour = 41 MB) | small |
| 12 | `ignore_result=True` on scanner and fire-and-forget tasks; cap error text in failed results | `app/tasks/*` | about 0.4-0.5 KB per task result; 6 KB for failed ones | small; hygiene |
| 13 | Move the `import fitz` out of the parent PDF path (first pass) | `app/tasks/extraction.py:671` | 15-35 MB resident in the parent after the first PDF | small |
| 14 | Cap PDF page count/size before launching YOLO | `app/tasks/extraction_implementations.py`, `extract_with_yolo` | bounds the 657-689 MB child | small |

Item 1 plus item 2 removes most of the API cost. Item 6 plus item 7 bounds the worker peak. Items 3-5 bring other request paths in line.

## 6. What cannot be concluded locally, and what to check in Railway metrics

Not concludable from this run:
- **Railway container limits** and how close the API and worker run to them. The biggest peaks here (1.9-2.1 GB worker on a 20 MB page, 1.3-1.4 GB worker + YOLO on a PDF, 0.8-1.5 GB API at 2,000 items) only matter relative to the plan's limit.
- **Linux allocator behavior for the real API and worker.** Only the standalone extraction was run on Linux (25x page size, 336 MB retained). The macOS plateaus and retention in the API (1.5 GB) and worker may be smaller or larger on Linux; the macOS memory compressor does not exist there.
- **Real page-size distribution** and real HTML structure. Synthetic pages have uniform vocabulary, so the extraction ratio and chunk counts (382 chunks per MB) may differ.
- **Real tsvector sizes.** The 263 MB attributed to `search_vector` used a small synthetic vocabulary; real values depend on the unique-lexeme count.
- **Real OpenAI behavior** for large embedding batches (2,048-input limit, response size, latency); the fake server accepts any size. The 5 MB and 20 MB scenarios would probably fail at the embed call in production.
- **YOLO on Linux** and on PDFs with many pages (the repo only has 6- and 8-page PDFs; ADR 0007 states 1.5 GB).
- **MCP tool paths**, which need OAuth, were only inspected.
- **Redis server memory of 7.15 GB** on the local docker instance is not explained by this run; it lives outside db 15 (db0 has 6 keys). It is not a statement about production.
- Postgres numbers are container memory including cache; Railway's Postgres service limits and settings (`shared_buffers`, `work_mem`) are unknown.
- Run-to-run variance: single runs per scenario, except the 1/5/20 MB worker series, run twice (retention after the 20 MB page differed: +185 vs +35 MB).
- The first 20-page worker run in step 3 used one pass; the two large-page series overlapped with backfill chunk tasks from the beat tick.

Check in Railway:
- API service: memory over 7 days. Look for step-like rises that stay flat afterwards (the signature of the full-row plateau), especially after `POST /content` bursts and `/content/recommended` or list views by the largest user. Correlate with the largest library size.
- Worker service: memory spikes after PDF or large-page ingests, and whether the baseline steps up and stays up after each one. Compare with the footprints above (120 -> 650 MB after large pages).
- OOM-kill / restart events on the worker, and their timestamps against extraction logs for pages over 5 MB.
- Postgres service memory vs `shared_buffers`; vector index size (`content_chunks_embedding_hnsw` was 5.5 KB per vector here).
- Redis service `used_memory`; it should be near 1 MB per few thousand keys unless `qemb:*` volume is high.
- The configured memory limit per service, to compare against: 2.1 GB (worker, 20 MB page), 1.4 GB (worker + YOLO child), 1.5 GB (API after a few 2,000-item loads).
