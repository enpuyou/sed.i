---
layout: layouts/chapter.njk
title: "Memory: investigation and fixes"
part: 5
order: 2
summary: How the October 2026 memory work found what costs memory and money in sed.i, what was changed, and what the numbers were before and after.
verified: 2026-10-25
permalink: /reliability/memory/
---

## In one minute

In October 2026 the backend was measured to find out what costs memory, because memory is what Railway bills for. The answer was not "the models" or "a leak". The biggest costs were three ordinary mistakes: loading whole rows when only a title was needed, reading a page of any size into memory, and embedding every passage of a huge article in one call. One save of a URL by a user with 2,000 saved items held 1,233 MB in the API process; after the fix it holds about 2 MB.

The work followed one method: state a hypothesis, measure it on the real stack, fix the largest cost, then re-run the identical benchmark on old and new code. A later check against production showed the system is nearly idle there, so two configuration changes (stop an unused service, remove deploy overlap) saved more money today than the code changes did. The code changes matter as libraries grow.

## The problem

The question this chapter answers: **what makes the sed.i backend use memory, which of those costs grow with the user's data, and how do you prove a fix worked?**

The obvious design is to give each service a bigger memory limit and stop thinking about it. That fails in two ways. Cost: Railway bills for memory used, so a service that peaks at 1.5 GB for a few seconds a day costs more than the peak deserves if the instance has to stay large. Safety: a limit only hides a cost that grows with data. A query that loads every row of a library is fine at 100 items and runs the process out of memory at 10,000.

## Mental model

Think of a clerk who must check whether a book is already on the shelf. The cheap way is to look the title up in the card index. The expensive way is to carry every book in the library to the desk and read each cover. Both give the same answer; the second one needs a desk that grows with the library. Memory work is mostly finding places where the code carries the books.

Terms used below:

| Term | Meaning here |
|---|---|
| RSS | Resident set size: the physical memory a process currently holds, as the operating system reports it. |
| Footprint | macOS `phys_footprint`: RSS plus pages the memory compressor has squeezed. On macOS an idle worker with a 231 MB footprint showed 45 MB RSS a minute later, so footprint is used for "settled" values. |
| `tracemalloc` | Python's allocation tracer. It counts memory allocated by Python code, not memory held by C libraries, so it understates a process that uses `lxml` or `numpy`. |
| Peak | The highest value during an action. Sampled at 10-20 times a second here. |
| Settled | The value a fixed time (30 s) after the action ends. The gap between peak and settled is memory the allocator gave back; the gap between settled and the starting value is memory the process kept. |
| Railway metrics | Container memory sampled by the host. The 7-day query used for the production baseline returned 5-minute samples, so a spike shorter than that can fall between two samples. |

{% fig "The two kinds of memory cost" %}
```text
cost grows with the user's data        cost grows with the input size
(API process)                          (worker process)

  library of N items                     page of S megabytes
        |                                      |
  query loads N full rows                download S, parse at ~37x S,
        |                                chunk, embed every chunk at once
  N x 280 KB held at once                       |
                                         ~37 x S plus a vector per chunk

fix: do not load what you do not use   fix: cap S, work in batches
```
{% endfig %}

## Walkthrough: one save of a URL with 2,000 items in the library

This is the single largest finding. A user with 2,000 saved items pastes one new URL. The request must answer "is this URL already saved?" before it creates a row.

### Step 1: the old query

`ingest_url` calls `find_existing_active_item`. At commit `b5f2e10` (the last commit before the fixes) the function did an exact lookup first, then, when nothing matched, a fallback for rows saved before URLs were normalized consistently:

```python
# content-queue-backend/app/services/content.py at commit b5f2e10
active_items = (
    db.query(ContentItem)
    .filter(
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
    )
    .all()
)
for item in active_items:
    if normalize_url(item.original_url) == normalized_url:
        return item
return None
```

Every new URL has no exact match, so every new URL ran the fallback. `.all()` on `db.query(ContentItem)` asks Postgres for all 32 columns of every active row of the user and builds one Python object per row. The code only needed `original_url`, `id` and `is_archived`.

### Step 2: what the ORM does with those rows

`ContentItem` has three large columns. Each row is not 280 KB in the database (Postgres compresses large values out of line), but it is 280 KB once it is a Python object:

| Column | Type in Python | Why it is large |
|---|---|---|
| `full_text` | `str` | The article text. |
| `search_vector` | `str` | Postgres `tsvector`, loaded as its text form: one entry per distinct word with positions. |
| `embedding` | `numpy` array | 1,536 float32 values: 6,144 bytes. |

I checked the types on a scratch row: a 56,632-character `full_text`, a 132,588-character `search_vector` string and a 6,144-byte array. The driver also holds its own copy of the result while the ORM builds objects, so process memory is roughly twice the size of the Python objects (inferred).

### Step 3: the measured cost

The benchmark (described under "Method" below) measured this on the real stack, before the fix, as the peak growth of the API process during five saves:

| Library size | Memory added by one save, before | After the fixes |
|---|---|---|
| 100 items | 79 MB | 2 MB |
| 500 items | 457 MB | 0 MB (noise) |
| 2,000 items | 1,233 MB (range 1,226-1,254) | 2 MB |

These are measured with `scripts/memprof/run_benchmark.py` on macOS, median of three repetitions, from `docs/audits/2026-10-24-memory-fix-results.md`. Time per save fell from 10.9 s to 0.2 s.

The first pass had estimated 142 MB (SQLite, nine columns); the real model on Postgres measured 552 MB, because the imitation had no `search_vector`. Deferring columns one at a time gave:

| Deferred | Peak growth at 2,000 items | Implied cost of the column |
|---|---|---|
| nothing | 552 MB | |
| `search_vector` | 288 MB | 263 MB |
| `full_text` | 354 MB | 198 MB |
| `embedding` | 473 MB | 79 MB |
| all three | 14 MB | the other 29 columns cost 14 MB |

(Measured on the benchmark's synthetic data, `docs/audits/2026-10-24-memory-profiling-live.md`; the synthetic vocabulary is small, so a real `tsvector` may differ.)


### Step 4: reproduce the scale effect

The benchmark takes 35 to 45 minutes, so here is a small version. It uses a scratch Postgres database built with the project's migrations and 2,000 rows for one user: a random 6,000-word `full_text` (about 57 KB), a random 1,536-value embedding, and the `search_vector` the production trigger fills in.

```sql
-- seed (abridged to the generating expressions)
INSERT INTO content_items (id, user_id, original_url, title, full_text, embedding, ...)
SELECT gen_random_uuid(), '<scratch user>', 'https://example.invalid/post/' || g,
       'Sample article ' || g,
       (SELECT string_agg('word' || (random()*20000)::int, ' ')
          FROM generate_series(1, 6000)),
       ('[' || (SELECT string_agg(random()::real::text, ',')
          FROM generate_series(1, 1536)) || ']')::vector, ...
FROM generate_series(1, 2000) AS g;
```

The measuring script loads N rows as ORM objects, with or without the project's own `defer_heavy_columns()` options, and reports either the `tracemalloc` peak or the peak growth of the process's resident memory. It runs once per process so each number starts clean:

```python
import resource, sys, tracemalloc
import psutil

mode, n, meter = sys.argv[1], int(sys.argv[2]), sys.argv[3]  # full|deferred, N, rss|trace

from sqlalchemy.orm import defer
from app.core.database import SessionLocal
from app.models.content import ContentItem
from app.services.content import defer_heavy_columns

db = SessionLocal()
db.query(ContentItem.id).limit(1).all()          # warm the connection and mappers
hwm0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
if meter == "trace":
    tracemalloc.start()

q = db.query(ContentItem).filter(ContentItem.deleted_at.is_(None))
if mode == "deferred":
    q = q.options(*defer_heavy_columns())
elif mode.startswith("no_"):                     # defer one column at a time
    col = {"no_sv": ContentItem.search_vector, "no_ft": ContentItem.full_text,
           "no_emb": ContentItem.embedding}[mode]
    q = q.options(defer(col))
rows = q.order_by(ContentItem.created_at.desc()).limit(n).all()

if meter == "trace":
    _, peak = tracemalloc.get_traced_memory()
    print(f"{mode:8} N={n:5} rows={len(rows):5} tracemalloc_peak={peak/1e6:8.1f} MB")
else:
    hwm1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"{mode:8} N={n:5} rows={len(rows):5} peak_rss_delta={(hwm1-hwm0)/1e6:8.1f} MB")
```

Captured output (run on 2026-10-25 on macOS, Python 3.11, local Postgres 16 with pgvector):

{% fig "Output of the scale script" %}
<!--captured python scale_demo.py-->
```text
== tracemalloc peak
full     N=  100 rows=  100 tracemalloc_peak=    21.7 MB
deferred N=  100 rows=  100 tracemalloc_peak=     0.6 MB
full     N=  500 rows=  500 tracemalloc_peak=   107.0 MB
deferred N=  500 rows=  500 tracemalloc_peak=     2.1 MB
full     N= 2000 rows= 2000 tracemalloc_peak=   426.8 MB
deferred N= 2000 rows= 2000 tracemalloc_peak=     7.7 MB
== peak RSS growth
full     N=  100 rows=  100 peak_rss_delta=    46.7 MB
deferred N=  100 rows=  100 peak_rss_delta=     0.6 MB
full     N=  500 rows=  500 peak_rss_delta=   236.8 MB
deferred N=  500 rows=  500 peak_rss_delta=     2.6 MB
full     N= 2000 rows= 2000 peak_rss_delta=   815.1 MB
deferred N= 2000 rows= 2000 peak_rss_delta=     9.8 MB
== which column costs what, N=2000, peak RSS growth
full     N= 2000 rows= 2000 peak_rss_delta=   939.3 MB
no_sv    N= 2000 rows= 2000 peak_rss_delta=   355.2 MB
no_ft    N= 2000 rows= 2000 peak_rss_delta=   589.0 MB
no_emb   N= 2000 rows= 2000 peak_rss_delta=   859.2 MB
deferred N= 2000 rows= 2000 peak_rss_delta=   9.7 MB
== columns selected
columns selected, no options: 32
columns selected, deferred  : 29
left out: ['embedding', 'full_text', 'search_vector']
```
{% endfig %}

How to read it:

- **Linear in N.** Doubling the library doubles the cost: 21.7, 107.0, 426.8 MB at 100, 500 and 2,000 rows (`tracemalloc`). About 213 KB per row.
- **Deferring removes almost all of it.** At 2,000 rows 427 MB becomes 7.7 MB. The SQL selects 29 columns instead of 32, and the three left out are the three large ones.
- **Run-to-run noise is real.** The 2,000-row RSS cell was 815 MB in one run and 939 MB in the next (two earlier runs gave 829 and 931). Read it as "about 800-950 MB"; the `tracemalloc` figure is steadier.
- **`search_vector` dominates.** Removing it cut about 584 MB of 939; `full_text` about 350 MB; `embedding` about 80 MB (they overlap, so they do not add up). This data is more extreme than the benchmark's (a 20,000-word random vocabulary gives 126 KB of `tsvector` text per row against 52 KB), so read the order of magnitude and the linear growth, not the split.

Deferred columns are postponed, not gone: touching `full_text` on a deferred row issues one extra query, so code that unexpectedly reads one stays correct.

### Step 5: the fix, in two parts

Part one (commit `9389a77`) added `defer_heavy_columns()` and used it in the whole-library queries:

{% fig "services/content.py: skip the three heavy columns" %}
<!--src content-queue-backend/app/services/content.py#107-119-->
```python
def defer_heavy_columns():
    """Query options that skip the three large per-row columns.

    full_text, embedding and search_vector are ~280 KB of a typical row; an
    ORM query that materializes every row of a library with them costs
    hundreds of MB. Deferred columns still load on access, one query per row,
    so a caller that unexpectedly touches one stays correct.
    """
    return (
        defer(ContentItem.full_text),
        defer(ContentItem.embedding),
        defer(ContentItem.search_vector),
    )
```
{% endfig %}

That took 2,000 items from 1,233 MB to 24 MB. Part two (commit `c0bcd2a`) removed the scan altogether. A fallback scan was only needed because old rows might be stored in an un-normalized spelling. Migration `a7d3e9c15b42` rewrote every stored URL to its normalized form once, so the duplicate check became one indexed exact lookup:

{% fig "services/content.py: one indexed lookup, no library scan" %}
<!--src content-queue-backend/app/services/content.py#122-143-->
```python
def find_existing_active_item(
    *,
    db: Session,
    user_id: UUID,
    normalized_url: str,
) -> ContentItem | None:
    """Find an active duplicate by normalized URL.

    Stored URLs are normalized (ingest_url, the MCP save, and migration
    a7d3e9c15b42 for rows saved before normalization was consistent), so this
    is one indexed exact lookup; nothing scans the user's library.
    """
    return (
        db.query(ContentItem)
        .options(*defer_heavy_columns())
        .filter(
            ContentItem.user_id == user_id,
            ContentItem.original_url == normalized_url,
            ContentItem.deleted_at.is_(None),
        )
        .first()
    )
```
{% endfig %}

Memory per save no longer depends on library size: 1.7 to 1.8 MB peak growth at 2,000 items in three repetitions of the benchmark (`docs/audits/memprof-data/after_s1_no_scan.json`, measured). The `defer_heavy_columns()` options stay on the query as insurance.

The migration is the risky half, because it rewrites user data:

{% fig "alembic/versions/a7d3e9c15b42: what happens on a collision" %}
<!--src content-queue-backend/alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py#1-21-->
```python
"""normalize_stored_content_urls

Rewrites every active content item's original_url to its normalized form (the
form ingest_url stores today), so duplicate detection is one indexed lookup and
the O(library) fallback scan in find_existing_active_item can be removed.

Rows saved before normalization was consistent (trailing slash, #fragment,
tracking params, mixed-case host) are rewritten in place. If two active rows of
one user normalize to the same URL, one wins and the others are soft-deleted
(deleted_at set, recoverable) before the rewrite so the partial unique index
uq_content_items_user_url_active is never violated. The winner is the row that
has highlights, then the already-normalized one, then the most recently created
(5534fbac2811 also kept the newest). The winner inherits the losers' list
memberships; a loser's highlights stay attached to its soft-deleted row and are
logged by id (rows are never hard-deleted).

The normalization below is a frozen copy of app.services.content.normalize_url
as of this revision: a migration must not change behaviour when that function
is edited later.

Not reversible: the original spellings are not kept; downgrade is a no-op.
```
{% endfig %}

On the development database the migration rewrote 7 of 71 active rows with no collisions (from the fix report). It was not run against production when the report was written.

## The investigation in order

The order matters: each step either confirmed a hypothesis or changed what the next step measured.

| Step | Hypothesis | Result | Key number |
|---|---|---|---|
| 1. Import baseline | Importing `torch` or `ultralytics` makes the API and worker large | Refuted. They are not imported at start; the PDF layout model runs in a child process | API 164 MB, worker 125 MB after import (measured, macOS); bare `fastapi` plus `sqlalchemy` is 59 MB |
| 2. Whole-row loads | The duplicate check loads whole rows | Confirmed, then understated 3.8 times by the SQLite first pass | 552 MB at 2,003 items on real Postgres |
| 3. Other whole-row loads | The same pattern exists elsewhere | Confirmed in the recommended feed, list view and cleanup task | +1,085 MB (recommended), +838 MB (list), +80 MB for 303 rows (cleanup) |
| 4. Page size | Extraction memory scales with page size | Confirmed, about 37 to 40 times | 1 MB page: 41 MB; 5 MB: 189 MB; 20 MB: 730 MB (standalone) |
| 5. Chunk embedding | Chunking is a moderate cost | Overturned. In the live worker the chunk-embedding stage, not extraction, set the peak | 7,634 chunks from a 20 MB page: RSS peak 1,878 MB |
| 6. PDFs | The layout model is the big cost | Measured, left unchanged | 635 MB floor plus about 1.7 MB per page |
| 7. Redis results | Celery results fill Redis | Refuted | 424 B typical result; 6.2 KB for a failed one |
| 8. Leak | The worker leaks | Refuted | 200 iterations stayed within 158 to 168 MB |
| 9. Production | The bill is driven by peaks | Refuted: production is nearly idle and the bill is baselines | worker 482 MB p50, API 315 MB p50 |

### Import baseline

A fresh interpreter is 17 MB; `import app.main` brings the API to 164 MB and the worker to 125 MB. `torch`, `ultralytics`, `prefect`, `instructor`, `braintrust` and `fitz` (35 to 188 MB each when imported alone) are imported inside functions, and the layout model runs in a child process (ADR 0007). See [PDFs](/ingestion/pdfs/).

### Page size and the 37-times rule

Extraction runs `trafilatura` (a library that pulls the article out of a page), converts its XML to HTML, and parses that with BeautifulSoup. Each stage holds a tree for the whole document. The first pass measured peak growth on synthetic pages: 41 MB for 1 MB (41 times), 189 MB for 5 MB (38 times), 730 MB for 20 MB (37 times).

I re-ran the same pipeline on pages from the benchmark's generator for this chapter (no warm-up, so the first sizes include first-use costs): 0.1 MB gave 58 times, 1.1 MB gave 48 times, 5.2 MB gave 37.9 times (captured, peak growth including the page itself). The ratio settles near 37 to 40 times once the page is large enough. On Linux (a `python:3.11-slim` container) the same code peaked 20 to 35 percent lower and gave back more memory afterwards, which is why the macOS numbers are an upper estimate for Railway (only the standalone extraction was run on Linux; the full pipeline was not).

### Chunk embedding: the finding that overturned the first pass

The first pass guessed extraction was the large-page cost. The live worker said otherwise. For a 20 MB page, extraction peaked near 935 MB, then the chunk stage reached 1,878 MB. `split_article_into_chunks` produced 7,634 chunks, and `generate_chunk_embeddings` asked for all their embeddings in one call. Each returned vector is a Python list of 1,536 floats. I measured that again here: 100 such lists take 5.0 MB (about 49.6 KB each), so 7,634 of them are about 379 MB, before response parsing and one ORM object per chunk in a single session. The real OpenAI API also refuses more than 2,048 inputs per request, so these pages would have ended as failures after the peak, with no chunks. See [Chunking and embedding](/retrieval/chunking-and-embedding/).

### PDFs

Measured: 635 MB in the child for an 8-page PDF, growing about 1.7 MB per page to 963 MB at 200 pages (RSS). Growth follows the size of the HTML produced, because every figure becomes a base64 image. The parent adds about 11 MB per MB of output when it re-parses it. None of this was changed; details are in `docs/audits/2026-10-24-pdf-memory-scaling.md`.

### Redis, and the leak that was not there

Celery results are small: 424 bytes typical, 6,248 for a failed analysis with an error string. The largest Redis item was a query-embedding cache entry, 41 KB. A 200-iteration extraction loop stayed between 158 and 168 MB: no leak. What the live runs did show is that memory is not returned after a large page: the worker's footprint settled 35 to 204 MB higher after a 5 or 20 MB page (the spread between two runs of the 20 MB page shows how uncertain that is). It does not compound: twenty small pages afterwards added nothing.

## Method: the benchmark harness

The fixes were judged by a harness, not by timing a few requests by hand. It lives in `content-queue-backend/scripts/memprof/` and its README is `content-queue-backend/scripts/memprof/README.md`.

- **Black box.** It starts the backend exactly as production does (`uvicorn` for the API; `celery -A app.core.celery_app worker --loglevel=info --pool=solo --beat` for the worker) and drives it over HTTP. The same harness runs unchanged against any checkout, so "before" and "after" are comparable: "before" is an exact copy of commit `b5f2e10`, "after" is the working tree.
- **Isolated.** The environment of every process is built from scratch; the database is `memprof_<label>`, Redis is logical database 15, OpenAI is a local fake server that returns HTTP 400 above 2,048 inputs (the real limit), and an outbound proxy points at a dead port so an accidental external call fails at once. A watchdog kills the process group above 6,000 MB.
- **Fixed data.** Users with 100, 500 and 2,000 items (about 40 KB of text each), one list holding all 2,000, synthetic pages from 100 KB to 50 MB, and a streamed 100 MB zip.
- **Defined metrics.** "Peak delta" is peak RSS above the settled level before the scenario; "settled" is the macOS footprint 30 seconds after the work. Three repetitions where affordable (one for the 50 MB and 100 MB cases). Each API repetition uses a fresh process with an unmeasured warm-up so first-request costs sit in the baseline.
The results of the final comparison, in MB (median of repetitions; before is `b5f2e10`):

| Scenario | Peak delta before | Peak delta after | Change |
|---|---|---|---|
| Save a URL, 100 items | 79 | 2 | -98% |
| Save a URL, 500 items | 457 | 0 (noise) | -100% |
| Save a URL, 2,000 items | 1,233 | 2 | -100% |
| Open recommended feed, 2,000 items | 558 | 14 | -98% |
| Open a 2,000-item list, all at once | 641 | 34 | -95% |
| Same list, first page of 50 | n/a | 1 | n/a |
| Worker, 1 MB article | 177 | 74 | -58% |
| Worker, 5 MB article | 486 | 288 | -41% |
| Worker, 20 MB page | 847 | 593 | -30% |
| Worker, 50 MB page | 1,997 | 626 | -69% |
| Worker, link to a 100 MB zip | 1,248 | 0 | -100% |
| Worker, normal 100 KB article | 7 | 6 | within noise |

(Measured, macOS, from `docs/audits/memprof-data/table.md` and the fix report. The save-a-URL and paged-list rows come from the final code; the others from the earlier full run on the same fixes. Worker numbers varied up to about 25 percent between repetitions; API numbers stayed within a few percent.)

Correctness was compared run for run: normal articles gave identical word counts, chunk counts and embeddings, and the 2,000-item list response was the same size in bytes. The intended differences: a page over 10 MB now ingests its first 10 MB (4,000 chunks, flagged "Partial extraction") instead of ending with zero chunks, and a PDF over 100 MB is rejected.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/services/content.py` | `defer_heavy_columns`, the one-query duplicate check |
| `content-queue-backend/app/core/fetch.py` | `fetch_bounded`: the only place extraction downloads a user URL |
| `content-queue-backend/app/tasks/chunk_embeddings.py` | embeds and inserts chunks 100 at a time |
| `content-queue-backend/app/api/lists.py` | optional `skip` and `limit` on `GET /lists/{id}/content` |
| `content-queue-backend/app/api/content.py` | recommended feed with deferred columns |
| `content-queue-backend/alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py` | one-time URL normalization |
| `frontend/hooks/usePagedListContent.ts`, `frontend/components/ListLoadMore.tsx` | the list page loads 50 items at a time on scroll |
| `content-queue-backend/scripts/memprof/` | the benchmark harness |

### Bounded fetch

Memory per task was proportional to whatever the remote server sent. `fetch_bounded` streams the body and stops at a cap:

{% fig "core/fetch.py: stop reading at the cap" %}
<!--src content-queue-backend/app/core/fetch.py#115-135-->
```python
deadline = _now() + settings.FETCH_MAX_SECONDS
pieces: list[bytes] = []
total = 0
truncated = False
for piece in resp.iter_content(chunk_size=_CHUNK_BYTES):
    if _now() > deadline:
        raise requests.Timeout(
            f"Download exceeded {settings.FETCH_MAX_SECONDS}s"
        )
    if total + len(piece) > limit:
        if is_pdf:
            raise FetchRejectedError(
                f"PDF too large (over {limit // (1024 * 1024)} MB)"
            )
        # Keep only what fits so the cap is never exceeded in memory.
        piece = piece[: limit - total]
        truncated = True
    pieces.append(piece)
    total += len(piece)
    if truncated:
        break
```
{% endfig %}

HTML is truncated at 10 MB and still ingests (a design choice: a partial article beats a failure). PDFs are rejected above 100 MB. Video, audio, image, font and archive types are not downloaded at all, which turned a 1.25 GB spike on a link to a zip into 0 MB.

### Batched chunk embedding

{% fig "tasks/chunk_embeddings.py: 100 chunks at a time" %}
<!--src content-queue-backend/app/tasks/chunk_embeddings.py#191-200-->
```python
db.query(ContentChunk).filter(
    ContentChunk.content_item_id == item.id
).delete()
for start in range(0, total, _EMBED_BATCH_SIZE):
    batch = chunks[start : start + _EMBED_BATCH_SIZE]
    texts_to_embed = [
        contextual_prefix(chunk, title, start + offset, total)
        for offset, chunk in enumerate(batch)
    ]
    result = llm_client.embed(texts_to_embed, user_id=str(item.user_id))
```
{% endfig %}

The delete and the batched inserts are still one transaction, so a failure rolls back to the old chunks; only one batch of vectors is alive at a time, and every request stays under the provider's 2,048-input limit.

### Optional paging for lists

{% fig "api/lists.py: skip and limit are optional" %}
<!--src content-queue-backend/app/api/lists.py-->
```python
        .order_by(ContentItem.created_at.desc(), ContentItem.id)
        .offset(skip)
    )
    if limit is not None:
        query = query.limit(limit)
```
{% endfig %}

With no parameters the endpoint still returns every item, so existing clients are unchanged. The web list page asks for 50 at a time and loads more on scroll: 1 MB per page; asking for all 2,000 costs 34 MB, down from 641 MB.

## Production baseline and the configuration changes

Local numbers say what could happen. Railway's metrics say what does. The 7-day query (5-minute samples, 2026-10-19 to 2026-10-26 as written in the report) gave:

| Service | Memory p50 | p95 | max | Note |
|---|---|---|---|---|
| celery (worker plus beat) | 482 MB | 515 MB | 840 MB | 340 MB after a restart, about 500 MB after a few days |
| API | 315 MB | 319 MB | 434 MB | flat |
| prefect | 222 MB | 224 MB | 228 MB | flat; about 3 MB of network in the week |
| Postgres | 158 MB | 159 MB | 160 MB | |
| Redis | 6 MB | 7 MB | 7 MB | no queue backlog |

All services have an 8 GB limit, average CPU is below 0.02 vCPU everywhere, no out-of-memory kills appear in the logs, and total average memory is about 1.1 GB. At an assumed $10 per GB-month (an assumption; billing was not visible) that is roughly $11 a month.

What the data showed: the 840 MB worker peak was a deploy (493 MB old container plus 339 MB new; the next sample read 339 MB); the climb from 340 to about 500 MB is import warm-up, not a leak (flat at 339 MB for two days of sweeps only); and production is nearly idle, so the code fixes are not visible yet. About 20 percent of memory was the always-on `prefect` service, which the default ingestion path does not use.

Hence the configuration changes made on 2026-10-25 (variables and a service, not code; described in the fix report and in `ARCHITECTURE.md`):

| Change | Effect |
|---|---|
| `PREFECT_ENABLED=false` on the worker and API; the prefect service stopped (definition kept) | Frees about 222 MB around the clock; ingestion uses the default Celery chain, which is the tested path |
| `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0` on the worker only | Deploy peak went from 840 MB to 402 MB and lasts about a minute; also shortens the window in which two beat schedulers could run at once |

The API keeps Railway's default overlap because that gives zero-downtime deploys, and its deploy peak (434 MB) is small. Whether these changes show up in the next week's metrics has not been checked: the report says to re-run the same query after a few days of use and look for a worker steady state near 340 to 400 MB and no 840 MB deploy peak.

## What was learned

- **Lazy imports only delay a cost.** Steady state is the same once the path has run. The upper bound for the API, measured by stubbing the task modules out of `import app.main`, was 164 MB to 145 MB (about 20 MB, 7 percent of production size, about $0.20 a month). Not done.
- **Measure the stage, not the guess.** The first pass blamed extraction; the live timeline showed chunk embedding peaking at twice the extraction stage.
- **A simulation understates the real schema.** 142 MB on SQLite with nine columns; 552 MB on Postgres with 32.
- **Sampling hides spikes.** A five-minute sample can miss a 12-second spike, and the one 840 MB reading was a deploy overlap. On macOS use footprint, not RSS, for settled values.
- **The cheapest saving was a switch.** Stopping an unused service saved more today than all the code changes.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Defer three columns at query time (`defer_heavy_columns()`) | Model-level `deferred=True`; a side table | Scoped to the queries that load many rows, so single-row reads are unchanged. Model-level deferral changes every query at once. |
| Normalize stored URLs once in a migration, then one indexed lookup | Keep the scan with `load_only`; add a `normalized_url` column | The scan was O(library) even when cheap per row. A migration is one-time work and the index already existed. Cost: an irreversible data rewrite (collisions soft-delete the loser; the report records the rules). |
| Truncate HTML at 10 MB, reject PDFs above 100 MB | Reject oversized pages; no cap | A truncated article is still useful. The cap bounds worker memory near the 5 to 10 MB measurements, not the 50 MB one. |
| Batches of 100 chunks, one transaction | Commit per batch | The delete-and-replace stays all-or-nothing, so readers never see half an article. |
| Optional `skip` and `limit` on lists | Required paging; a new endpoint | Existing clients keep working; only the web list page changed. |
| Leave PDFs alone | Page ranges; streaming the HTML | Fixed cost dominates for typical documents. |
| Remove deploy overlap on the worker only | Remove it on both | Worker overlap meant two beat schedulers; API overlap buys zero-downtime deploys. |
| Do not make imports lazy | Lazy imports everywhere | Measured 20 MB at most for the API (see above). |

ADR 0007 (`docs/decisions/0007-celery-memory-ml-tasks.md`) covers the layout-model isolation.

## How it fails

| Failure | What happens | What was done |
|---|---|---|
| Repeated whole-library loads | API climbs about 265 MB per call, then plateaus near 1.5 GB | Deferred columns, indexed lookup |
| 20 MB page | Worker peak 1,878 to 2,141 MB; real OpenAI would refuse the 7,634-input call | 10 MB cap, batches of 100 |
| Link to a 100 MB zip | 1.25 GB spike | Binary types are not downloaded |
| Deploy overlap | Two beat schedulers and an 840 MB peak for a minute | Overlap 0 on the worker |
| Migration collision | The unique index would abort the deploy | Winner by highlights, then normalized, then newest; losers soft-deleted; commit `80587ce` keeps a highlighted duplicate |

## What is weak or missing

Ranked by how likely each is to matter, and checked against the code on 2026-10-25.

1. **Production has not been re-measured after the changes.** The "after" numbers are local, macOS, synthetic articles and a fake OpenAI server; Railway runs Linux, where freed memory tends to be held longer. Not verified.
2. **Several whole-row loads remain.** `GET /content` and the public content listing still load full rows for each page (+27 MB for 50 rows, +55 MB for 100, measured before the fix); `cleanup_old_deleted_items` loads whole rows of every item soft-deleted over seven days ago (+80 MB for 303 rows) and scales linearly after an outage with many deletions; `recover_stale_pending_items` loads whole rows but is cheap because pending rows have no text. A list requested with no parameters still returns everything.
3. **Large pages still cost 37 times their size, and memory is not returned.** A 5 MB page still adds 288 MB to the worker and a 20 MB page 593 MB. The worker keeps tens to hundreds of MB after a large page, and `worker_max_tasks_per_child` has no effect under the solo pool, so only a restart releases it. Running large-page extraction in a short-lived child process, as the PDF path already does, would fix the retention.
4. **PDFs are unchanged.** About 635 MB fixed plus 1.7 MB per page, no page-count cap before the layout model starts.
5. **Smaller items.** `ignore_result` is unset (0.4 to 0.5 KB per result, harmless); the query-embedding cache stores 41 KB per entry as JSON; `fitz` stays resident in the worker after the first PDF (15 to 35 MB).
6. **Only five-minute samples, no alert.** Nothing watches memory trends, so a regression would show up only when someone runs the query. See [Gaps and roadmap](/reliability/gaps-and-roadmap/).

{% say %}
I measured before I fixed. The first pass guessed; the second pass ran the real stack and found that the biggest costs were ordinary: one save of a URL loaded every row of the user's library with three huge columns, taking 1.2 GB at 2,000 items, and a very large page made the worker embed thousands of chunks in one call. I deferred the heavy columns, replaced the library scan with an indexed lookup after a migration that normalized stored URLs, capped downloads at 10 MB, and embedded chunks 100 at a time. A benchmark that runs the old and new code on the same data showed API memory per save going from 1.2 GB to about 2 MB. Then I looked at production and found it nearly idle, so the real saving was turning off an unused service and removing deploy overlap on the worker. What is still open is re-measuring production after the deploy, and the worker still pays 37 times a page's size.
{% endsay %}

## Check yourself

{% qa "Why did the first pass understate the duplicate-check cost by almost four times, and what does that say about simulations?" %}
It used SQLite and a nine-column imitation of the table, so it had no `search_vector` and fewer per-row objects. The real 32-column model on Postgres cost 552 MB against 142 MB. A simulation answers only the question you built into it; the column nobody listed turned out to be the largest.
{% endqa %}

{% qa "Memory per save now stays flat as the library grows. Which two changes make that true, and which is the one that actually removes the dependence on library size?" %}
Deferring the three heavy columns cut the cost per row by about 98 percent but the query still touched every row (1,233 MB to 24 MB at 2,000 items). The migration that normalized stored URLs let the check become one indexed exact lookup, which touches at most one row regardless of library size (to about 2 MB).
{% endqa %}

{% qa "A 20 MB page is truncated to 10 MB and the article still ingests. What was the alternative, and what is the cost of the choice made?" %}
The alternative is to reject oversized pages. Truncating keeps a partial article that is still searchable and readable, at the cost of losing the tail of the page; the item is flagged as a partial extraction so the reader can see it. Worker memory is bounded near the 10 MB measurement, but still about 37 times the page size.
{% endqa %}

{% qa "The production worker read 840 MB once and the report says that was not usage. How do the numbers show it?" %}
840 MB equals 493 MB (old container) plus 339 MB (new container), and the next sample five minutes later read 339 MB. Two containers ran together during the deploy. Setting the overlap to 0 on the worker brought the deploy peak to 402 MB.
{% endqa %}

{% qa "Lazy imports would make the API start smaller. Why was this not done?" %}
They only delay the cost until first use. The measured upper bound for never loading the task modules in the API was about 20 MB (7 percent of production size), at the price of touching most task tests.
{% endqa %}
