# Memory fixes: before and after

Date: 2026-10-24. Measured locally (macOS) on the real stack: FastAPI, Celery worker, Postgres with pgvector, Redis. Same harness, same seeded data, same machine for both runs.

## Result

Four changes cut API memory per request by 95-98% and worker memory on large pages by 30-69%, with no change to what users get on normal-sized articles.

| What a user does | Memory added, before | Memory added, after | Reduction |
|---|---|---|---|
| Saves a URL, library of 100 items | 79 MB | 2 MB | 98% |
| Saves a URL, library of 500 items | 457 MB | 0 MB (noise) | ~100% |
| Saves a URL, library of 2,000 items | 1,233 MB | 2 MB | ~100% |
| Opens "For You" (recommended), 2,000 items | 558 MB | 14 MB | 98% |
| Opens a list of 2,000 items (UI now loads 50 at a time) | 641 MB | 1 MB per page; 34 MB if all 2,000 are requested | 95-99% |
| Saves a 1 MB article (worker) | 177 MB | 74 MB | 58% |
| Saves a 5 MB article (worker) | 486 MB | 288 MB | 41% |
| Saves a 20 MB page (worker) | 847 MB | 593 MB | 30% |
| Saves a 50 MB page (worker) | 1,997 MB | 626 MB | 69% |
| Link points to a 100 MB zip file (worker) | 1,248 MB | 0 MB | 100% |
| Saves a normal 100 KB article (worker) | 7 MB | 6 MB | within noise |
| 20 normal articles back to back (worker) | 30 MB | 17 MB | within noise (after-run range 5-29 MB) |

"Memory added" is the peak process memory above its settled level before the action. Save-a-URL and list-paging rows come from the final code (after the URL migration and the paged list); the other "after" rows are from the earlier full run on the same fixes. Median of 2-3 repetitions per scenario (1 for the 50 MB and 100 MB cases). The "after" column is from a second full run on the final code; the first full run gave the same picture (within a few percent on the API scenarios). Per-scenario ranges are in `memprof-data/table.md`.

Speed improved as a side effect, because the API no longer loads whole libraries:

| Action | Time before | Time after |
|---|---|---|
| Save a URL, 2,000 items | 10.9 s | 0.2 s |
| Open a 2,000-item list | 2.2 s | 0.2 s |
| Open recommended, 2,000 items | 2.1 s | 0.1 s |

## What was wrong

1. **The API loaded every row of a user's library on every save.** Each row carries three heavy columns (`full_text`, `embedding`, `search_vector`), about 280 KB per row. The duplicate-check fallback, the recommended feed and the list view all loaded them although they only need titles and flags. With 2,000 items one save held over a gigabyte.
2. **The worker had no limit on what it downloaded.** A 20 MB page used about 850 MB; a link to a large file was read fully into memory.
3. **The worker embedded all of a large article's chunks in one call.** That held every vector in memory at once, and the real OpenAI API rejects more than 2,048 inputs per request, so very large pages failed outright.

## What changed

| Change | Where |
|---|---|
| Skip the three heavy columns in whole-library queries | `app/services/content.py`, `app/api/content.py`, `app/api/lists.py` |
| Stream downloads through one bounded fetch: HTML truncated at 10 MB (the item still saves), PDFs rejected above 100 MB, non-document types rejected before download, 60 s limit | `app/core/fetch.py`, `app/tasks/extraction.py`, `app/core/config.py` |
| Embed and store chunks 100 at a time, still all-or-nothing | `app/tasks/chunk_embeddings.py` |
| Optional `skip` and `limit` on the list endpoint; no parameters still returns everything | `app/api/lists.py` |
| Migration `a7d3e9c15b42` rewrites stored URLs to normalized form (duplicates soft-deleted), then the duplicate check becomes one indexed lookup with no library scan | `alembic/versions/`, `app/services/content.py` |
| Lists page loads 50 items at a time and pages on scroll, total shown from the new `content_count` on `GET /lists/{id}` | `frontend/app/lists/[id]/page.tsx`, `frontend/hooks/`, `frontend/components/ListLoadMore.tsx` |

## Correctness: same results on normal content

Outputs compared run for run:

| Scenario | Before | After |
|---|---|---|
| 100 KB, 1 MB, 5 MB articles | words, chunk count and embedding identical | identical (35 / 379 / 1,916 chunks) |
| 20 normal articles | 20 of 20 completed with chunks and embeddings | same, 728 chunks both |
| Recommended and list responses | 20 items, 2,000 items | same item counts; list response byte-for-byte the same size (2,984,959). Recommended varies by a few bytes between any two runs (ties in scoring), and a unit test proves identical JSON with and without the change on the same data |
| 20 MB and 50 MB pages | 0 chunks (embedding request rejected), item marked complete | first 10 MB ingested, 4,000 chunks and embedding, flagged "Partial extraction: page exceeded the size limit" |
| 100 MB zip link | 1.25 GB spike, then completed with "Could not extract article text" | same final state, 0 MB: the body is no longer downloaded |

The only differences are intentional: oversized pages now ingest their first 10 MB instead of ending with no chunks, and PDFs above 100 MB are rejected (configurable). Everything else ends in the same state as before.

Tests: 31 new tests; the full backend suite passes (ruff clean). The three column-skipping tests fail when the fix is removed, so they exercise the change. Four more tests compare endpoint responses with the change on and off and require identical JSON.

## What this does not show

- **Dollar savings.** No cost figure can be derived locally. If Railway bills on memory used, the API's large peaks are the part that shrinks. Confirm by watching the Railway memory graph for the API and worker before and after deploying.
- **Production behaviour.** These runs are macOS with synthetic articles and a fake OpenAI server. Railway runs Linux, where the allocator tends to hold freed memory longer; the order of magnitude should carry over, the exact numbers will not.
- **PDFs.** Unchanged. Extraction costs a fixed ~635 MB for the layout model plus ~1.7 MB per page (measured to 200 pages, ~960 MB), so page-range processing would save little for typical documents. See `2026-10-24-pdf-memory-scaling.md`.
- **Remaining worker cost on large pages.** A 5 MB page still adds about 288 MB, and a 20 MB page about 593 MB. The parsing libraries use roughly 37 times the page size; the cap bounds this at about 10 MB of page.
- **Spread between runs.** Worker numbers varied up to about 25% between repetitions in the baseline; the API results were stable (ranges within a few percent).
- **Production data not checked.** The URL migration was verified on the local dev database (71 active rows, 7 to rewrite, 0 collisions) and by tests. `scripts/check_url_normalization.py` reports the same counts read-only; run it against production before deploying.

## Reproduce

```
cd content-queue-backend
.venv/bin/python scripts/memprof/run_benchmark.py --backend-dir <backend dir> --label <before|after> --out <file>.json
.venv/bin/python scripts/memprof/compare.py before.json after.json
```

"Before" is an exact copy of commit `b5f2e10`; "after" is the working tree. Data: `docs/audits/memprof-data/` (`before.json`, `after.json`, `table.md`, raw samples). Harness: `content-queue-backend/scripts/memprof/` (see its README for scenario definitions). Full run takes about 36-45 minutes and needs the local Postgres and Redis containers.

## Related reports

- `2026-10-24-memory-profiling-live.md`: where memory went, before any fix.
- `2026-10-24-memory-measurements.md`: first-pass measurements (superseded where they disagree).
- `2026-10-24-pdf-memory-scaling.md`: PDF memory against page count.

## Follow-up: URL migration and list paging

Measured after the first round, with the same harness (save-a-URL from `memprof-data/after_s1_no_scan.json`; the 50-item list page is scenario S3b in `memprof-data/after.json`):

| Scenario | Before any fix | First round | With migration and paging |
|---|---|---|---|
| Save a URL, 100 items | 79 MB | 3 MB | 2 MB |
| Save a URL, 500 items | 457 MB | 9 MB | 0 MB (within noise) |
| Save a URL, 2,000 items | 1,233 MB | 24 MB | 2 MB |
| List page of 50 items (2,000-item list) | n/a (all 2,000 loaded) | n/a | 1 MB |

Save-a-URL memory no longer depends on library size. Negative deltas in the raw data are the process shrinking during the run, i.e. noise.

Verification beyond the benchmark:
- Migration: 8 tests (legacy rows rewritten, collisions keep the already-normalized row else the newest and soft-delete the rest, different users never collide, soft-deleted rows untouched, re-run is a no-op, the frozen normalizer equals the runtime one, a legacy URL is still a duplicate after migrating).
- Paging: hook tests (skip equals rows loaded, no duplicates across shifted pages, retry after failure, stale response dropped, concurrent calls collapse), footer tests, API query-string tests, and a test through the real lists page (first request asks for 50, header shows the total, scrolling requests skip 50 then 100, footer disappears at the end, deleting lowers the total without a refetch, a failed page shows Retry and keeps rows).
- Backend 786 tests and frontend 163 tests pass; ruff, tsc and eslint are clean.

Behaviour notes: the "+ Add" dialog marks only the loaded rows as already in the list; adding an item that is already present is a server-side no-op, so this is cosmetic. Adding items reloads the list from the first page.

## Production baseline (Railway, 2026-10-19 to 2026-10-26)

Queried from Railway's metrics API (5-minute samples, 7 days; production environment). This is the "before" for PR #58, since none of the PR's code was deployed when these were taken.

| Service | Memory p50 | p95 | max | Notes |
|---|---|---|---|---|
| celery (worker + beat) | 482 MB | 515 MB | 840 MB | 340 MB after a restart, ~500 MB after a few days of use |
| Fast API | 315 MB | 319 MB | 434 MB | flat |
| prefect | 222 MB | 224 MB | 228 MB | flat; average CPU 0.018 vCPU; ~3 MB network in the week |
| pgvector (Postgres) | 158 MB | 159 MB | 160 MB | 242 MB on disk |
| Redis | 6 MB | 7 MB | 7 MB | 73 MB on disk; no queue backlog |

Every service has an 8 GB memory limit; average CPU is below 0.02 vCPU everywhere; no out-of-memory kills appear in the worker or API logs. Total average memory is about 1.1 GB.

What the data shows:
- **The 840 MB worker peak was a deploy, not usage.** At 07:15 UTC on Oct 24 the worker read 840 MB, which is 493 MB (old container) plus 339 MB (new container); the next sample, 07:20, read 339 MB.
- **The climb from 340 to ~500 MB is import warm-up, not a leak.** After the Oct 24 restart the worker stayed flat at 339 MB for two days, and its logs for that window show only beat sweeps, no ingestion. Earlier local measurements put the lazily imported libraries at 35-75 MB each (fitz, prefect, braintrust, instructor, boto3), which accounts for the climb once content is ingested.
- **Production is nearly idle, so the PR's large-library and large-page savings are not visible yet.** They bound the cost as libraries and pages grow; today's bill is dominated by fixed baselines.
- The real cost was the always-on `prefect` service (about 20% of total memory) and the worker's baseline.

Billing was not visible from here; at an assumed ~$10 per GB-month, 1.1 GB is roughly $11 a month (check Railway's usage page).

## Production changes made on 2026-10-25 (configuration only, not part of the PR)

| Change | Where | Effect |
|---|---|---|
| `PREFECT_ENABLED=false` (was `true`) | celery, Fast API | Ingestion uses the default Celery chain (the tested path); prefect is no longer imported by the worker |
| `prefect` service stopped (`railway down`; service definition kept) | Railway | Frees ~222 MB around the clock |
| `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0` | celery only | Deploy peak went from 840 MB to 402 MB and lasted about one minute instead of the old container lingering; also shortens the window in which two beat schedulers could run at once |

The API keeps Railway's default overlap, which gives zero-downtime deploys; its deploy peak (434 MB) is small. Both services redeployed successfully and the API's `/health` returned 200 afterwards.

Rollback: `railway variable set PREFECT_ENABLED=true --service celery` (and for the API), `railway variable delete RAILWAY_DEPLOYMENT_OVERLAP_SECONDS --service celery`, and `railway redeploy --service prefect` to bring the prefect service back.

## Lazy imports: measured, not done

Deferring imports only delays their cost until first use; steady-state memory is the same once the code path has run. The only way to save memory is to never load the module in that process. For the API, the candidate is the task graph (trafilatura, BeautifulSoup, lxml), which the API loads because its routes import task modules. Upper bound, measured by stubbing the task modules out of `import app.main`: 164 MB to 145 MB, about 20 MB (roughly 7% of the API's ~300 MB in production, around $0.2 a month). Realizing it means dispatching tasks by name instead of importing them, which touches most task tests. Not worth it at this size; revisit if the API footprint becomes the cost driver.

## Comparing after PR #58 is deployed

Re-run the same query after a few days of normal use. What to look for: worker steady state near 340-400 MB without the prefect import, no 840 MB deploy peak, API peaks unchanged at today's library sizes, and, as libraries grow, API memory per save staying flat instead of rising.
