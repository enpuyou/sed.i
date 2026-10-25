# Memory fixes: before and after

Date: 2026-10-24. Measured locally (macOS) on the real stack: FastAPI, Celery worker, Postgres with pgvector, Redis. Same harness, same seeded data, same machine for both runs.

## Result

Four changes cut API memory per request by 95-98% and worker memory on large pages by 30-69%, with no change to what users get on normal-sized articles.

| What a user does | Memory added, before | Memory added, after | Reduction |
|---|---|---|---|
| Saves a URL, library of 100 items | 79 MB | 3 MB | 96% |
| Saves a URL, library of 500 items | 457 MB | 9 MB | 98% |
| Saves a URL, library of 2,000 items | 1,233 MB | 24 MB | 98% |
| Opens "For You" (recommended), 2,000 items | 558 MB | 14 MB | 98% |
| Opens a list of 2,000 items | 641 MB | 34 MB | 95% |
| Saves a 1 MB article (worker) | 177 MB | 74 MB | 58% |
| Saves a 5 MB article (worker) | 486 MB | 288 MB | 41% |
| Saves a 20 MB page (worker) | 847 MB | 593 MB | 30% |
| Saves a 50 MB page (worker) | 1,997 MB | 626 MB | 69% |
| Link points to a 100 MB zip file (worker) | 1,248 MB | 0 MB | 100% |
| Saves a normal 100 KB article (worker) | 7 MB | 6 MB | within noise |
| 20 normal articles back to back (worker) | 30 MB | 17 MB | within noise (after-run range 5-29 MB) |

"Memory added" is the peak process memory above its settled level before the action. Median of 2-3 repetitions per scenario (1 for the 50 MB and 100 MB cases). The "after" column is from a second full run on the final code; the first full run gave the same picture (within a few percent on the API scenarios). Per-scenario ranges are in `memprof-data/table.md`.

Speed improved as a side effect, because the API no longer loads whole libraries:

| Action | Time before | Time after |
|---|---|---|
| Save a URL, 2,000 items | 10.9 s | 0.6 s |
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
- **Not done:** the stored-URL normalization migration (the duplicate-check fallback still scans a user's rows, but now without the three heavy columns, so it costs 3-24 MB instead of 79-1,233 MB; removing it needs the migration), and frontend pagination of lists (the endpoint supports it, the UI still requests everything).

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
