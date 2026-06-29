---
type: design
status: active
last_updated: 2026-06-25
consumer: human
---

# sed.i — Cross-Component Data Flows

> Companion to `system-wiki.md`. That doc explains each subsystem in isolation
> (what hybrid search does, what the MCP server does). **This doc explains the
> wiring** — for one user action at a time, every hop it takes across frontend,
> API, services, Celery, and the DB, in order, with the state left behind at
> each hop and what happens when a hop fails.
>
> Read this when you need to answer "what exactly happens, step by step, when
> a user does X" — including the async handoffs a component-by-component doc
> glosses over.

---

## Table of contents

1. [Save a URL](#1-save-a-url)
2. [Search query](#2-search-query)
3. [Login / auth](#3-login--auth)
4. [Highlight creation → connections](#4-highlight-creation--connections)
5. [MCP agent call](#5-mcp-agent-call)
6. [PDF upload](#6-pdf-upload)
7. [Recommendation generation](#7-recommendation-generation)

---

## 1. Save a URL

### 1.1 The three paths

There isn't one flow — there are three, branching at the very first step:

| Path | Trigger | Where it forks |
|---|---|---|
| **Normal web save** | User pastes URL in `AddContentForm` | Full pipeline below, no shortcuts |
| **Extension save** | Chrome/Safari extension scrapes the page itself | Skips Phase 1's HTTP fetch — `full_text` arrives pre-populated |
| **PDF** | URL resolves to a PDF content-type | Forks inside Phase 1 into YOLO layout extraction, never reaches Phase 2 |

This chapter covers the normal web save in full, then calls out exactly where the other two diverge.

### 1.2 Sequence diagram — normal web save

```
Browser                FastAPI              Postgres         Redis        Celery: extract_metadata   Celery: extract_full_content   Celery: generate_embedding
  │ POST /content          │                    │                │                 │                            │                             │
  │ ──────────────────────>│                    │                │                 │                            │                             │
  │                        │ ingest_url()       │                │                 │                            │                             │
  │                        │  normalize_url()   │                │                 │                            │                             │
  │                        │  find_existing()──>│                │                 │                            │                             │
  │                        │<───────────────────│ (dup check)    │                 │                            │                             │
  │                        │ INSERT pending ───>│                │                 │                            │                             │
  │                        │ .delay(item_id) ──────────────────> │                 │                            │                             │
  │ 201 {pending} ─────────│<───────────────────│                │ ──task msg────> │                            │                             │
  │ (item appears in UI,   │                    │                │                 │ fetch URL (requests)       │                             │
  │  greyed out / skeleton)│                    │                │                 │ parse OG tags (BS4)        │                             │
  │                        │                    │<───────────────────────────────── UPDATE processing,         │                             │
  │                        │                    │                │                 │   title/thumbnail/author   │                             │
  │                        │                    │                │ <─────────────────.delay(item_id)──────────>│                             │
  │                        │                    │                │                 │                            │ fetch URL again             │
  │                        │                    │                │                 │                            │ trafilatura.extract()       │
  │                        │                    │                │                 │                            │ xml_to_html()                │
  │                        │                    │<──────────────────────────────────────────────────────────────  UPDATE completed,            │
  │                        │                    │                │                 │                            │   full_text/word_count       │
  │                        │                    │                │ <──────────────────────────────────────────────.delay(item_id)────────────> │
  │                        │                    │                │                 │                            │                              │ OpenAI embed (1536-dim)
  │                        │                    │<──────────────────────────────────────────────────────────────────────────────────────────────  UPDATE embedding
  │                        │                    │                │                 │                            │                              │
  │ GET /content/:id  (useProcessingPolling, every 5s, only while status is pending/processing)
  │ ──────────────────────>│ ──────────────────>│                │                 │                            │                              │
  │ <──────────────────────│<───────────────────│                │                 │                            │                              │
  │ (status flips to       │                    │                │                 │                            │                              │
  │  'completed' → UI      │                    │                │                 │                            │                              │
  │  swaps skeleton for    │                    │                │                 │                            │                              │
  │  real content)         │                    │                │                 │                            │                              │
```

Three separate Celery tasks, chained by each task calling `.delay()` on the
next one — not a single task, and not (by default) a Celery `chain()`. Each
hop is its own message on the Redis broker, picked up independently by
whatever worker is free. This matters operationally: if the worker pool is
saturated, Phase 1 can finish for item A while Phase 2 for item B is still
queued behind other work — there's no ordering guarantee across items, only
within one item's chain.

### 1.3 Pseudocode trace, with state after each step

```python
# ── Step 0: Frontend ────────────────────────────────────────────
# AddContentForm.tsx
async function handleSubmit(url):
    try:
        item = await contentAPI.create({ url })   # POST /content
        prependToList(item)                       # optimistic render, status='pending'
    except 409:
        show("Already saved") pointing at err.body.existing_id

# ── Step 1: API endpoint ────────────────────────────────────────
# app/api/content.py: create_content_item
@router.post("/content")
def create_content_item(payload, user, db):
    item = ingest_url(url=payload.url, user=user, db=db)   # may raise DuplicateContentError → 409
    return item   # 201, processing_status='pending'

# ── Step 2: ingest_url ───────────────────────────────────────────
# app/services/content.py:145
def ingest_url(url, user, db, submitted_via='web', dispatch_extraction=True):
    normalized = normalize_url(url)              # strips utm_*, fbclid, etc.
    existing = find_existing_active_item(db, user.id, normalized)  # full table scan
    if existing:
        raise DuplicateContentError(existing_id=existing.id)

    item = ContentItem(user_id=user.id, original_url=normalized,
                        submitted_via=submitted_via, processing_status='pending')
    db.add(item); db.commit(); db.refresh(item)

    if dispatch_extraction:
        extract_metadata.delay(str(item.id))     # ① fire-and-forget into Redis
    return item
# STATE: row exists, status='pending', no title/text/embedding yet.
# The HTTP response has already gone back to the browser at this point —
# everything below happens with no open connection to the client.

# ── Step 3: extract_metadata (Celery worker, Phase 1) ───────────
# app/tasks/extraction.py:321
@celery_app.task(bind=True, base=DatabaseTask)
def extract_metadata(self, item_id):
    item = db.get(ContentItem, item_id)
    item.processing_status = 'processing'; db.commit()

    resp = requests.get(item.original_url, timeout=30, headers={...spoofed UA...})
    resp.raise_for_status()                       # → except blocks, see 1.4

    content_type = _detect_content_type(url, resp.headers)   # 'pdf' | 'article' | 'video' | 'social'
    if content_type == 'pdf':
        _process_pdf(item, resp.content, url)     # forks away — see §1.6
        generate_embedding.delay(item.id)
        return

    soup = BeautifulSoup(resp.content)
    item.title, item.description, item.thumbnail_url, item.author = extract_og_tags(soup)
    # status deliberately STAYS 'processing' — full text not extracted yet
    db.commit()

    if settings.PREFECT_ENABLED:
        try:
            run_deployment("ingest-content/default", {"item_id": item_id})  # ②a
        except Exception:
            extract_full_content.delay(item_id)    # ②b fallback — Prefect deployment missing
    else:
        extract_full_content.delay(item_id)        # ②b default path today
# STATE: title/thumbnail/author/description written. status='processing'.
# The user's queue card can now render a real thumbnail+title even though
# the full article body doesn't exist yet — this is why Phase 1 and 2 are
# split rather than one task: time-to-first-useful-render is much shorter.

# ── Step 4: extract_full_content (Celery worker, Phase 2) ───────
# app/tasks/extraction.py:566
@celery_app.task(bind=True, base=DatabaseTask, max_retries=2)
def extract_full_content(self, item_id):
    item = db.get(ContentItem, item_id)
    resp = requests.get(item.original_url, timeout=30, headers={...})   # fetched AGAIN — see note below

    xml = trafilatura.extract(resp.content, output_format='xml', include_images=True, include_links=True)
    html = xml_to_html(xml, original_html=resp.content) if xml else None
    if not html or len(html) < 100:
        html = text_to_html_paragraphs(trafilatura.extract(resp.content))  # plain-text fallback

    if html and len(html) > 100:
        item.full_text = html
        item.word_count = count_words(html)
        item.reading_time_minutes = max(1, round(word_count / 200))
        item.processing_error = detect_limited_extraction_reason(resp.content, html)  # paywall heuristics
        item.processing_status = 'completed'
        db.commit()
        generate_embedding.delay(item_id)          # ③
    else:
        item.processing_error = "Could not extract article text"
        item.processing_status = 'completed'        # NOTE: still 'completed', not 'failed' — see 1.4
        db.commit()
# STATE: full_text/word_count/reading_time written, status='completed'.

# ── Step 5: generate_embedding ───────────────────────────────────
# app/tasks/embedding.py
@celery_app.task
def generate_embedding(content_item_id):
    item = db.get(ContentItem, content_item_id)
    text = "\n\n".join(filter(None, [item.title, item.description, html_to_plain(item.full_text)]))
    text = truncate_to_tokens(text, 8000)          # tiktoken cl100k_base
    item.embedding = openai_embed(text)            # 1536-dim, text-embedding-3-small
    db.commit()
# STATE: embedding written. Item is now findable by semantic search.
# Nothing pings the frontend — search just starts working on the next query.

# ── Step 6: Frontend polling ─────────────────────────────────────
# frontend/hooks/useProcessingPolling.ts:12
useEffect(() => {
    items_to_watch = items.filter(i => i.processing_status in ('pending','processing'))
    if items_to_watch.is_empty(): return  # stop polling, nothing in flight

    interval = setInterval(every=5000ms, fn=() => {
        for item in items_to_watch:
            fresh = GET /content/{item.id}
            if fresh.processing_status in ('completed', 'failed'):
                onUpdate(fresh)    # ContentList re-renders this one card
    })
    return () => clearInterval(interval)
}, [items])
```

**Why fetch the URL twice (Step 3 and Step 4)?** Phase 1 only needs the
`<head>` OG tags; Phase 2 needs the full body for trafilatura. They're split
into separate tasks specifically so Phase 1 can finish and update the UI
(title + thumbnail) without waiting on the slower full-text extraction — but
the cost is two HTTP round-trips to the source site. If the site is slow or
rate-limits aggressively, Phase 2 can fail independently of Phase 1 having
already succeeded.

### 1.4 What can go wrong here

This is the part a "talk like you built it" answer needs cold — not "it has
error handling" but the exact behavior per failure mode.

| Failure | Where | Resulting state | Retry? | User sees |
|---|---|---|---|---|
| HTTP 401 from source | `extract_metadata` | `status='failed'`, `processing_error` set | No — permanent | Failed state in UI (if rendered — see below) |
| HTTP 403 from source | `extract_metadata` | `status='failed'`, "Site blocks bots" | No — permanent | Same |
| Other HTTP error (5xx etc.) | `extract_metadata` | `status='failed'` written, **then** `self.retry()` raised | Yes — exponential backoff `60 * 2^retries` | Stuck on a stale `failed` row until a retry succeeds and flips it back |
| Timeout | `extract_metadata` | `status='failed'`, "Request timed out" | Yes — same backoff | Same as above |
| Any other exception | `extract_metadata` | `status='failed'`, exception message stored | **No** — falls into bare `except Exception`, no `self.retry()` | Permanent failed state |
| Timeout / RequestException | `extract_full_content` | `status='completed'` (!), `processing_error` set | No | **Looks successful** — card shows title/thumbnail from Phase 1, just no reader body |
| trafilatura returns nothing usable | `extract_full_content` | `status='completed'`, `processing_error="Could not extract article text"` | No | Same — graceful degradation, not a failure state |
| Prefect deployment missing/down | `extract_metadata` (Step 3, path ②a) | Caught, falls back to `extract_full_content.delay()` | N/A — fallback IS the recovery | Invisible to user; only visible in worker logs |

Two things worth being able to explain unprompted:

1. **`extract_metadata` failures are loud (`status='failed'`), `extract_full_content` failures are quiet (`status='completed'` with an error string riding along).** This is intentional, not an oversight: by the time Phase 2 runs, Phase 1 has already given the user a title and thumbnail. Treating a Phase-2 failure as a hard `failed` would regress a card that already has useful content back to an error state. The tradeoff: the frontend has to know to check `processing_error` even on `completed` items if it wants to show "limited extraction" messaging — `status` alone doesn't tell the whole story past Phase 1.
2. **The retry path has a bug-shaped subtlety**: `extract_metadata`'s HTTP-error branches write `status='failed'` to the DB *before* calling `self.retry()`. So between the commit and the retry actually firing (up to `60 * 2^n` seconds later), the row sits at `failed` even though a retry is coming. A poll that lands in that window sees `failed` and may render a permanent-looking error for something that's about to self-heal.

### 1.5 Where the Prefect/Celery decision matters (ADR-0005)

The `if settings.PREFECT_ENABLED` branch in Step 3 isn't incidental — it's
[ADR-0005](../../decisions/0005-pipeline-orchestration.md)'s seam. Celery
chains give sequencing but no per-step visibility and chain-wide (not
per-step) retries; Prefect gives a UI with per-step timing and retries at the
cost of an extra service. The codebase already has both wired:
`PREFECT_ENABLED=false` today, Celery `.delay()` chaining is the live path,
and the plain functions (`generate_embedding_for_item`, etc.) are factored out
specifically so Prefect can call them directly without re-triggering the
Celery `.delay()` cascade when it's eventually turned on. The migration
trigger (ADR-0005) is: Prefect server deployed + 10 verified production runs
+ failure alerting wired. None of those are true yet, which is why
`extract_metadata` always falls back to `extract_full_content.delay()` in
practice today.

### 1.6 Where the extension and PDF paths diverge

**Extension save** (`app/tasks/extraction.py:335` onward): if `item.full_text`
already has >100 chars when `extract_metadata` picks up the task, it skips the
entire fetch-and-extract flow. It still does a lightweight metadata-only fetch
*if* `thumbnail_url`/`description` are missing, but `processing_status` is
never set to `processing` — it stays at whatever the API handler already set
(`completed`, set synchronously in `create_content_item` before the task is
even dispatched). This is also why extension saves don't show the "extracting…"
skeleton state — by the time the row exists, it's already `completed`.

**PDF save**: forks inside `extract_metadata` (Step 3) right after content-type
detection. `_process_pdf()` runs YOLO layout detection synchronously inside
that same task, uploads raw bytes to S3 (`upload_pdf`, no-op if
`AWS_S3_BUCKET` unset), sets `status='completed'` directly, and calls
`generate_embedding.delay()` — **`extract_full_content` is never invoked for
PDFs.** There is no Phase 2 for PDFs; everything happens in Phase 1.

### 1.7 Things that would surprise you reading only `system-wiki.md`

- Polling, not push. There's no websocket/SSE — `useProcessingPolling` is a
  plain `setInterval` that only runs while *something* in the visible list is
  `pending`/`processing`, and stops itself otherwise.
- The duplicate check (`find_existing_active_item`) loads **all** of the
  user's active items into Python and normalizes each one to compare —  not a
  DB-level query on a normalized-URL column. Fine at current scale, a known
  O(n) cost if a user's library gets large.
- `processing_status='completed'` is not a reliable signal that the reader has
  a body — check `processing_error` too.

---

## 2. Search query

### 2.1 The two frontend entry points

There are two search surfaces, and they don't behave the same way:

| Component | Trigger | Endpoint call | Result shape |
|---|---|---|---|
| `SearchBar` (navbar) | Every keystroke, 300ms debounce | `searchAPI.semantic(q, {limit: 5})` | Inline 5-result dropdown |
| `SearchModal` (full search) | Enter / "See all" from SearchBar, or opened directly | `searchAPI.semantic(q, {limit: 11, offset, mode: 'full', after, before})` | Paginated, 10/page, articles + highlights, date-filter UI |

Both hit the same backend route, `POST /search/semantic`
([search.py:283](../../../content-queue-backend/app/api/search.py)) — the
difference is entirely in the `mode` param. `SearchBar` uses `mode="auto"`
(cheapest path, classifier decides). `SearchModal` uses `mode="full"`
(maximum recall — always runs every engine, no shortcuts). This is a
deliberate UX tradeoff: the navbar wants instant, cheap results as you type;
the modal is the "I'm committing to actually searching" surface where you pay
for full recall.

### 2.2 Sequence diagram — `mode="auto"` (the common case)

```
Browser (SearchBar)        FastAPI /search/semantic       search_router.classify_query()      hybrid_search engines           Postgres
      │  GET ?q=...&mode=auto      │                              │                                    │                          │
      │ ──────────────────────────>│                              │                                    │                          │
      │                            │  classify_query(query) ────> │                                    │                          │
      │                            │ <──────────────────────────  │  returns one of:                  │                          │
      │                            │     filter|keyword|semantic|hybrid                                 │                          │
      │                            │                              │                                    │                          │
      │                            │  dispatch to ONE OR TWO engines based on classification ─────────> │                          │
      │                            │                              │     (keyword: tsvector query;      │ ───────────────────────> │
      │                            │                              │      semantic: pgvector <=> query; │ <─────────────────────── │
      │                            │                              │      hybrid: both + RRF fuse)      │                          │
      │                            │  fallback: if keyword found 0 results → retry as semantic          │                          │
      │                            │            if semantic found 0 results → retry as keyword          │                          │
      │  200 {articles, highlights}│ <─────────────────────────── │                                    │                          │
      │ <───────────────────────── │                              │                                    │                          │
```

Everything here is **fully synchronous, request/response** — no Celery
involved anywhere in search. The entire round trip happens inside one HTTP
request, with FastAPI's default timeout as the only ceiling.

### 2.3 Pseudocode trace, with state after each step

```python
# ── Step 0: Frontend (SearchBar, the auto path) ─────────────────
# frontend/components/SearchBar.tsx:24
onKeystroke(debounced 300ms):
    results = searchAPI.semantic(query, { limit: 5 })   # GET /search/semantic
    render inline dropdown(results.articles[:5])

# ── Step 1: API endpoint ─────────────────────────────────────────
# app/api/search.py:283
@router.post("/search/semantic")
def semantic_search(query, limit=10, offset=0, mode="auto", db, user):
    item_dicts = hybrid_search(query, user, db, limit, offset, mode)
    articles = [fetch_full_ContentItem(d["id"]) for d in item_dicts]  # re-fetch full rows by id
    highlights = _search_highlights(query, user, db)                  # separate, always runs
    return SearchResponse(articles, highlights)

# ── Step 2: classifier ───────────────────────────────────────────
# app/core/search_router.py:61  classify_query(query, user_authors)
def classify_query(query, user_authors):
    if has_operator(query, "author:|tag:|site:|is:|before:|after:"):
        return "filter", meta
    if '"exact phrase"' in query:
        return "keyword", meta
    if matches_domain_pattern(query):           # e.g. "nytimes.com"
        return "filter", {"site": query}
    if query.lower() in user_authors:           # loaded once per request from user's library
        return "filter", {"author": query}
    if starts_with_question_word(query) or query.endswith("?"):
        return "semantic", meta
    if len(query.split()) <= 3:
        return "keyword", meta                  # short queries assumed to be exact lookups
    return "hybrid", meta                       # 4+ words, no signal → run both, fuse

# ── Step 3a: keyword engine ──────────────────────────────────────
# app/core/hybrid_search.py:114  keyword_search()
SELECT id, ts_rank_cd(search_vector, tsquery, 32) AS rank
FROM content_items
WHERE user_id = :uid AND deleted_at IS NULL
  AND title IS NOT NULL AND title != ''        # untitled items excluded — pre-filter, not post
  AND search_vector @@ tsquery
ORDER BY rank DESC LIMIT :lim
# single alphanumeric token → to_tsquery('simple', word:*)  (prefix match)
# multi-word/punctuation     → websearch_to_tsquery('simple', query)

# ── Step 3b: semantic engine ─────────────────────────────────────
# app/core/hybrid_search.py:282  _semantic_search()
query_embedding = get_or_create_query_embedding(query)   # see 2.4, cache layer
# chunk-level rows preferred (MAX similarity per item); falls back to item-level
# embedding only for items with no chunks
WITH chunk_scores AS (
    SELECT content_item_id AS id, MAX(1 - (embedding <=> :q)) AS similarity
    FROM content_chunks WHERE user_id = :uid AND embedding IS NOT NULL
    GROUP BY content_item_id
),
item_scores AS (
    SELECT id, (1 - (embedding <=> :q)) AS similarity
    FROM content_items
    WHERE user_id = :uid AND NOT EXISTS (SELECT 1 FROM content_chunks WHERE content_item_id = id)
)
SELECT * FROM chunk_scores UNION ALL SELECT * FROM item_scores
ORDER BY similarity DESC LIMIT :lim
# no score threshold — top-N by similarity is returned even if similarity is low

# ── Step 3c: hybrid fusion (only when classifier says "hybrid", or mode="full") ──
# app/core/hybrid_search.py:195  rrf_fuse(list_a, list_b, k=60)
def rrf_fuse(list_a, list_b, k=60, limit=None):
    scores = {}
    for rank, id in enumerate(list_a, start=1):   # 1-indexed
        scores[id] = scores.get(id, 0) + 1 / (k + rank)
    for rank, id in enumerate(list_b, start=1):
        scores[id] = scores.get(id, 0) + 1 / (k + rank)
    fused = sorted(scores, key=scores.get, reverse=True)
    return fused[:limit] if limit else fused
# k=60 is the standard RRF-paper constant — items ranked #1 in either list
# dominate; items absent from a list simply don't get that list's term.

# ── Step 4: fallback rules (classifier's guess can be wrong) ────
if search_type == "keyword" and len(results) == 0:
    results = semantic_search(...)              # re-run as semantic, tag match_type="semantic_fallback"
if search_type == "semantic" and len(results) == 0:
    results = keyword_search(...)               # re-run as keyword

# ── Step 5: date filtering — post-filter, after fusion ───────────
# app/core/hybrid_search.py applies _apply_date_filter() AFTER scoring/fusion,
# not pushed into the SQL WHERE clause for keyword/semantic paths
# (the "filter" search_type — author:/site:/tag: — DOES push into SQL directly)
```

### 2.4 The embedding cache

Every semantic search re-embeds the query text via OpenAI unless it's been
asked before recently:

- **Cache key**: `"qemb:" + sha256(normalized_query)[:16]`
- **Store**: Redis, `setex(key, 3600, json.dumps(embedding))` — 1 hour TTL
- **Miss behavior**: calls OpenAI directly, then populates the cache
- **Redis-down behavior**: falls back to calling OpenAI directly every time — degrades gracefully, doesn't break search

This means the *first* person to search "best agentic coding tools" pays an
OpenAI embedding call; anyone who searches the identical string again within
an hour reuses the cached vector. It's a query-text cache, not a
results cache — two different users searching the same text share the
embedding but still each run their own per-user-filtered SQL query.

### 2.5 `mode="full"` — what SearchModal actually does differently

`mode="full"` skips the classifier's branching and always runs all three
paths — filter, keyword, semantic — then RRF-fuses keyword+semantic first,
and fuses that combined list against the filter-match list:

```python
kw_sem_fused = rrf_fuse(kw_ids, sem_ids, k=60)
all_fused    = rrf_fuse(filter_ids, kw_sem_fused, k=60, limit=fetch)
```

This is the only place RRF is applied twice in sequence. The cost is three
queries (filter/keyword/semantic) and an extra fusion pass every time the
modal is used — acceptable because it's a deliberate, infrequent "give me
everything" action, not a per-keystroke one.

### 2.6 What can go wrong / what's a known sharp edge

- **No SQL timeout configured on the search endpoint** — relies on FastAPI's
  process-level default. A pathological tsquery or a cold pgvector index on a
  very large library has no query-level circuit breaker today.
- **The classifier can misclassify** — e.g., a 3-word query that's actually a
  semantic question gets routed to keyword first. The empty-result fallback
  catches the *zero-result* case but not the *bad-but-nonzero-result* case —
  if keyword search returns a handful of weak matches, the fallback never
  fires and semantic search is never tried.
- **Untitled items are invisible to search**, full stop — both keyword and
  semantic SQL have `title IS NOT NULL AND title != ''` baked into the WHERE
  clause. An item stuck in `processing` with no title yet (see §1) will not
  show up in search results even if its `full_text` or description already
  has content.

---

## 3. Login / auth

### 3.1 Sequence diagram — register → verify → login → authenticated request → expiry

```
Browser              FastAPI /auth          Postgres            Celery (email)         Resend API
  │ POST /register        │                     │                      │                    │
  │ ──────────────────────>│ hash password (bcrypt)                    │                    │
  │                        │ INSERT user, is_verified=false ──────────>│                    │
  │                        │ INSERT VerificationToken (24h expiry) ───>│                    │
  │                        │ seed onboarding content (welcome article, │                    │
  │                        │   2 example articles, 1 vinyl record) ───>│                    │
  │                        │ send_verification_email_task.delay() ────────────────────────> │
  │                        │                     │                      │ ─────────────────>│
  │ 201 ───────────────────│                     │                      │                    │
  │                                                                                            │
  │ (user clicks link in email: /verify-email?token=...)                                      │
  │ GET /verify-email?token=...    │                     │                                    │
  │ ──────────────────────>│ look up token, check not used + not expired                       │
  │                        │ UPDATE is_verified=true ──>│                                     │
  │ 200 ───────────────────│                     │                                            │
  │                                                                                            │
  │ POST /auth/login (email, password)                  │                                     │
  │ ──────────────────────>│ verify password hash                                              │
  │                        │ create_access_token({sub: email}, exp=now+24h)  [JWT, HS256]      │
  │                        │ create + hash refresh_token, store hash, exp=now+90d ──>│         │
  │ 200 {access_token, refresh_token} │                  │                                     │
  │ <──────────────────────│                                                                    │
  │ store access_token in localStorage["token"]                                                │
  │                                                                                              │
  │ GET /content  Authorization: Bearer <token>           │                                     │
  │ ──────────────────────>│ get_current_user(): jwt.decode(token, SECRET_KEY, HS256)           │
  │                        │   → sub claim → SELECT user WHERE email=sub                        │
  │ 200 [...] ─────────────│                                                                    │
  │                                                                                              │
  │ (24h later: token expired)                                                                  │
  │ GET /content  Authorization: Bearer <expired-token>   │                                     │
  │ ──────────────────────>│ jwt.decode() raises JWTError → 401, WWW-Authenticate: Bearer        │
  │ 401 ───────────────────│                                                                    │
  │ fetchWithAuth sees 401 → clears localStorage → redirect to /login                           │
```

### 3.2 Pseudocode trace, with state after each step

```python
# ── Step 0: Frontend login form ──────────────────────────────────
# frontend/app/login/page.tsx
async function handleSubmit(email, password):
    resp = await authAPI.login(email, password)   # POST /auth/login, form-encoded
    localStorage.setItem("token", resp.access_token)
    # refresh_token also returned in body — frontend holds it for the /auth/refresh call

# ── Step 1: registration ─────────────────────────────────────────
# app/api/auth.py: register()
def register(email, username, password, db):
    assert email/username not already taken
    user = User(email, username, hashed_password=bcrypt(password), is_verified=False)
    db.add(user); db.commit()

    token = secrets.token_urlsafe(32)
    db.add(VerificationToken(user_id=user.id, token=token,
                              token_type="email_verification",
                              expires_at=now() + timedelta(hours=24)))

    seed_onboarding_content(user)   # welcome article + 2 example articles + 1 vinyl record,
                                     # plus pre-generated highlights on the welcome article —
                                     # so a brand-new account isn't an empty library
    db.commit()

    send_verification_email_task.delay(email, token)   # Celery → Resend API
    return 201
# STATE: user row exists but is_verified=False; cannot meaningfully use the
# product yet (most routes don't gate on is_verified today, but the email
# is sitting unconfirmed). Onboarding content already exists, pre-seeded.

# ── Step 2: email verification ───────────────────────────────────
# app/api/auth.py: verify_email(token)
def verify_email(token, db):
    vt = db.query(VerificationToken).filter_by(token=token).first()
    assert vt and not vt.is_used and now() <= vt.expires_at   # else 4xx
    vt.user.is_verified = True
    vt.is_used = True
    db.commit()
# STATE: is_verified=True. Token burned — replaying the same link 4xxs.

# ── Step 3: login → JWT issuance ─────────────────────────────────
# app/api/auth.py: login(email, password)
def login(email, password, db):
    user = db.query(User).filter_by(email=email).first()
    assert user and bcrypt_verify(password, user.hashed_password)   # else 401

    access_token = create_access_token({"sub": user.email}, expires_delta=timedelta(minutes=1440))
    # JWT claims: {"sub": user.email, "exp": <now+24h>}, signed HS256 with settings.SECRET_KEY

    raw_refresh = secrets.token_urlsafe(32)
    db.add(RefreshToken(user_id=user.id, token_hash=sha256(raw_refresh),
                         expires_at=now() + timedelta(days=90)))
    db.commit()
    return {access_token, refresh_token: raw_refresh, token_type: "bearer"}
# STATE: nothing server-side changes except a new RefreshToken row. The
# access token itself is stateless — the server never stores it, never
# checks a revocation list for it. Revoking access before its 24h expiry
# is not possible; only the refresh token can be revoked.

# ── Step 4: every authenticated request ──────────────────────────
# app/core/deps.py:14  get_current_user()
def get_current_user(token: str = Depends(oauth2_scheme), db):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        if email is None: raise 401
    except JWTError:
        raise HTTPException(401, "Could not validate credentials",
                             headers={"WWW-Authenticate": "Bearer"})
    user = db.query(User).filter_by(email=email).first()
    if user is None: raise 401
    return user
# Note: no expiry-specific branch — jwt.decode() raises JWTError for BOTH
# "expired" and "malformed/tampered" tokens, and both map to the same 401
# with the same generic message. The client cannot distinguish "your
# session expired, please log in again" from "this token is garbage"
# from the response alone.

# ── Step 5: frontend 401 handling ────────────────────────────────
# frontend/lib/api.ts: fetchWithAuth()
async function fetchWithAuth(url, opts):
    token = localStorage.getItem("token")
    resp = await fetch(url, { ...opts, headers: { Authorization: `Bearer ${token}` }})
    if resp.status == 401:
        localStorage.removeItem("token")
        redirect("/login")
        # NOTE: no attempt to use the refresh_token here — a 401 on any
        # request is treated as "log in again," not "silently refresh."
    return resp

# ── Step 6: explicit refresh (only called where the frontend chooses to) ──
# app/api/auth.py: refresh(refresh_token)
def refresh(raw_refresh_token, db):
    rt = db.query(RefreshToken).filter_by(token_hash=sha256(raw_refresh_token)).first()
    if rt.revoked_at is not None:
        # token reuse after revocation = theft signal
        revoke_all_active_tokens_for(rt.user_id)
        raise 401
    assert not_expired(rt) and rt.user.is_active

    rt.revoked_at = now()   # rotate: old refresh token burned on use
    new_access = create_access_token({"sub": rt.user.email}, timedelta(minutes=1440))
    new_refresh = issue_new_refresh_token(rt.user_id)
    db.commit()
    return {access_token: new_access, refresh_token: new_refresh}
```

### 3.3 Password reset (separate token type, same `VerificationToken` table)

```python
# forgot_password(email): always returns success message, even if email
# doesn't exist — prevents account enumeration via response timing/shape.
# Invalidates any existing unused reset tokens for that user first, then
# issues a new one: token_type="password_reset", expires_at=now()+1h.

# reset_password(token, new_password): same used/expired checks as email
# verification, then hashed_password = bcrypt(new_password), token.is_used=True.
# Does NOT revoke existing access/refresh tokens — a stolen-but-not-yet-expired
# session survives a password reset. (Worth knowing if asked "does resetting
# your password log out other devices" — currently, no.)
```

### 3.4 What can go wrong / sharp edges

| Scenario | Behavior |
|---|---|
| Token expired | `jwt.decode()` → `JWTError` → 401, generic message — indistinguishable from a tampered token |
| Token tampered/invalid signature | Same 401, same message |
| Refresh token reused after rotation | Treated as theft — **all** of that user's active refresh tokens are revoked, not just the one reused |
| Password reset while another session is active | Other session's access token keeps working until its own 24h expiry; refresh token also untouched |
| `is_active=False` (e.g. admin-disabled account) | Passes JWT validation (token itself is still cryptographically valid), then `get_current_active_user` rejects with 403 — a different status code than the 401 auth failures |
| Redis/DB down during onboarding seeding | Not separately try/excepted from the registration transaction as written — a failure here would roll back the whole `db.commit()`, including the user row itself, since seeding happens before the commit |

The single fact most worth having cold: **the access token is stateless and
unrevocable for its full 24-hour life.** There's no server-side check against
a blocklist on every request — only signature and expiry. Revocation power
exists only at the refresh-token layer. If someone asks "how do you force-log-out
a compromised account right now," the honest answer is: revoke their refresh
tokens, but any already-issued access token remains valid until it naturally
expires.

---

## 4. Highlight creation → connections

### 4.1 The two-phase nature of this flow

This flow has a much longer async gap than URL ingestion — minutes, not
seconds — and the "connection" feature is computed live, not precomputed.
Two separable things happen:

1. **Creation**: instant, synchronous, no embedding yet.
2. **Embedding**: happens on a periodic Celery beat sweep, not triggered by
   creation at all.
3. **Connections**: a live pgvector query run only when the user opens the
   connections panel — never cached, never precomputed, recomputed every open.

### 4.2 Sequence diagram

```
Browser (Reader)        FastAPI /content/{id}/highlights      Postgres        Celery beat (every 5 min)      OpenAI
  │ select text → HighlightToolbar              │                  │                    │                     │
  │ POST {text, start_offset, end_offset, color} │                  │                    │                     │
  │ ─────────────────────────────────────────────>│ INSERT highlight │                    │                     │
  │                                               │  (embedding=NULL)│                    │                     │
  │ 201 {id, ...} ────────────────────────────────│                  │                    │                     │
  │ (highlight renders immediately, no embedding) │                  │                    │                     │
  │                                                                  │                    │                     │
  │                                              ... up to 5 minutes pass ...              │                     │
  │                                                                  │  scan: embedding IS NULL                  │
  │                                                                  │ <──────────────────│                     │
  │                                                                  │  generate_highlight_embeddings_batch(uid) │
  │                                                                  │  batch embed ALL of this user's          │
  │                                                                  │  un-embedded highlights in one API call ─────────────────────>│
  │                                                                  │  UPDATE highlights SET embedding=... ────│ <───────────────────│
  │                                                                  │                    │                     │
  │ (user opens ConnectionsPanel, Mode 1: single highlight)          │                    │                     │
  │ GET /search/connections/{highlight_id}        │                  │                    │                     │
  │ ─────────────────────────────────────────────>│ pgvector query: cosine similarity vs   │                    │                     │
  │                                               │  ALL other highlights (cross-article)  │                    │                     │
  │ 200 [HighlightArticleConnection, ...] ────────│                  │                    │                     │
  │ <─────────────────────────────────────────────│                  │                    │                     │
  │ (lazy, per-card) GET insight for one connection card             │                    │                     │
  │ ─────────────────────────────────────────────>│ Redis cache check (key: insight:{h}:{a})│                    │                     │
  │                                               │  miss → gpt-4o-mini generates insight ────────────────────────────────────────────>│
  │                                               │  Redis SETEX 604800 (7 days) ─────────│                    │ <───────────────────│
  │ 200 {insight: "..."} ─────────────────────────│                  │                    │                     │
```

### 4.3 Pseudocode trace, with state after each step

```python
# ── Step 0: Frontend — text selection + toolbar ──────────────────
# frontend/components/ReaderArticle.tsx: handleSelection()  (fires on selectionchange/mouseup)
on_selection_change():
    offsets = getTextOffsets(selection)   # byte positions within the rendered article
    show HighlightToolbar at selection bounds

# frontend/components/HighlightToolbar.tsx: handleHighlight(color)
on_color_click(color):
    highlightsAPI.create(content_id, { text, start_offset, end_offset, color })

# ── Step 1: backend create — synchronous, no embedding ───────────
# app/api/highlights.py: create_highlight()
def create_highlight(content_id, body, user, db):
    h = Highlight(content_item_id=content_id, user_id=user.id,
                   text=body.text, start_offset=body.start_offset,
                   end_offset=body.end_offset, color=body.color)
                   # embedding column: NULL — nothing dispatches a Celery task here
    db.add(h); db.commit(); db.refresh(h)
    return h   # 201, immediately
# STATE: highlight exists, fully usable for display/notes, but NOT yet
# discoverable via "connections" — embedding is NULL until the next beat tick.

# ── Step 2: periodic embedding sweep (NOT triggered by creation) ──
# app/core/celery_app.py — beat schedule entry, every 300s
# app/tasks/embedding.py: process_all_missing_embeddings()  [beat-triggered]
def process_all_missing_embeddings():
    user_ids = db.query(Highlight.user_id).filter(Highlight.embedding.is_(None)).distinct()
    for uid in user_ids:
        generate_highlight_embeddings_batch.delay(uid)   # one task per user, not per highlight

# app/tasks/embedding.py: generate_highlight_embeddings_batch(user_id)
def generate_highlight_embeddings_batch(user_id, db):
    highlights = db.query(Highlight).filter_by(user_id=user_id, embedding=None).all()
    texts = [h.text for h in highlights]
    embeddings = llm_client.embed(texts)        # ONE batched OpenAI call for all of them
    for h, emb in zip(highlights, embeddings):
        h.embedding = emb
    db.commit()
# STATE: this user's highlights all have embeddings now. Worst case latency
# from creation to embedded: just under 5 minutes (if created right after a
# beat tick fired) to effectively immediate (if created right before one).

# ── Step 3: connections — live query, Mode 1 (single highlight) ──
# app/api/search.py: GET /search/connections/{highlight_id}
def get_highlight_connections(highlight_id, threshold=settings.SIMILARITY_THRESHOLD_CONNECTIONS, db):
    h = db.get(Highlight, highlight_id)
    if h.embedding is None:
        raise 400   # still waiting on the beat sweep — no graceful "pending" state, just an error
    return _connections_for_highlight(h, threshold, db)

# app/api/search.py: _connections_for_highlight()
def _connections_for_highlight(highlight, threshold, db, max_fetch=100):
    # SQL (real, not paraphrased):
    """
    SELECT h.id, h.text, h.content_item_id, ci.title, ci.author, ci.original_url,
           (1 - (h.embedding <=> :source_embedding)) AS similarity
    FROM highlights h JOIN content_items ci ON h.content_item_id = ci.id
    WHERE h.user_id = :user_id
      AND h.content_item_id != :source_article_id   -- never connect to your own article
      AND h.embedding IS NOT NULL
      AND ci.deleted_at IS NULL
      AND LENGTH(h.text) >= 20                       -- short fragments excluded
      AND (1 - (h.embedding <=> :source_embedding)) >= :threshold
    ORDER BY h.embedding <=> :source_embedding
    LIMIT :max_fetch
    """
    rows = db.execute(query, {...})
    return group_by_article(rows, max_passages_per_article=2)   # at most 2 best passages/article

# ── Step 3b: Mode 2 — all highlights in current article ──────────
# app/api/search.py: GET /search/connections/article/{content_id}/highlights
def get_article_highlight_connections(content_id, db):
    highlights = db.query(Highlight).filter_by(content_item_id=content_id).filter(Highlight.embedding.isnot(None)).all()
    return [_connections_for_highlight(h, threshold, db) for h in highlights]
    # same underlying function, called once per highlight in the article —
    # N highlights in this article = N separate pgvector queries, every panel open

# ── Step 4: lazy per-card "insight" generation ────────────────────
# app/api/search.py: get_connection_insight(highlight_id, article_id)
def get_connection_insight(highlight_id, article_id, db):
    cache_key = f"insight:{highlight_id}:{article_id}"
    cached = redis.get(cache_key)
    if cached: return cached
    insight = gpt_4o_mini_generate(f"Explain the connection between {highlight_text} and {article_excerpt}")
    redis.setex(cache_key, 604800, insight)   # 7-day TTL
    return insight
```

### 4.4 What can go wrong / sharp edges

- **No precompute, no cache on the connections query itself** — only the
  per-card *insight text* is cached (7 days, keyed by highlight+article
  pair). The actual similarity search re-runs in full, against potentially
  every highlight the user has ever made, on every single panel open. For a
  user with thousands of highlights this is the most expensive read-time
  query in the connections feature, and it's the one with no cache.
- **Mode 2 is N separate queries, not one** — opening the panel for an
  article with 8 highlights issues 8 independent pgvector lookups
  server-side in immediate succession. There's no batched/`UNION`-based
  version of this.
- **The 5-minute embedding gap is a real UX dead zone** — a highlight made
  right after a beat tick fires waits up to ~5 minutes before Mode 1's
  `GET /search/connections/{highlight_id}` even works; until then it 400s
  rather than returning an empty/pending result. If asked "why don't brand
  new highlights show connections immediately," this is the precise reason —
  not a bug, a consequence of batching embeddings per-user instead of
  embedding per-highlight on creation (the tradeoff being far fewer, larger
  OpenAI calls instead of one tiny call per highlight).
- **Short highlights (`LENGTH(h.text) < 20`) can never appear as a *target*
  of a connection** — they're filtered out of the candidate pool entirely,
  even though they can still *have* an embedding and *be the source* of a
  Mode 1 lookup.

---

## 5. MCP agent call

### 5.1 Two transports, one tool layer

There are two completely different ways an LLM agent reaches this app, but
they converge on the exact same Python functions:

| Transport | Used by | Auth mechanism | Entry point |
|---|---|---|---|
| **stdio** (Phase 1) | Claude Desktop, local | `SEDI_TOKEN` env var holding a sed.i JWT | `python -m app.mcp.server`, spawned as a subprocess |
| **Hosted HTTP** (Phase 2) | Any remote MCP client | OAuth 2.1 + PKCE → sed.i JWT | FastAPI mounts an ASGI sub-app at `/mcp-transport` |

Both transports call the same 14 `@mcp.tool()`-decorated functions in
`app/mcp/server.py` / `app/mcp/tools/`. The auth middleware differs entirely;
the business logic underneath does not.

### 5.2 Sequence diagram — hosted HTTP, OAuth handshake + one tool call

```
LLM Agent              FastAPI /mcp-transport          Redis              Postgres
   │ GET /.well-known/oauth-authorization-server    │                      │
   │ ───────────────────────────────────────────────>│ (static metadata)   │
   │ <─────────────────────────────────────────────── │                      │
   │                                                  │                      │
   │ browser redirect: GET /authorize?code_challenge=S256(...)&state=...    │
   │ ───────────────────────────────────────────────>│ render login HTML   │
   │ user submits email+password                     │                      │
   │ POST /authorize ──────────────────────────────────────────────────────>│ verify credentials
   │                                                  │ store auth code ──> │ (Redis: mcp:code:<code>,
   │                                                  │   TTL 5 min)        │  {email, code_challenge, redirect_uri})
   │ <── 302 redirect with ?code=... ─────────────────│                      │
   │                                                  │                      │
   │ POST /token  {code, code_verifier, redirect_uri} │                      │
   │ ───────────────────────────────────────────────>│ look up code in Redis│
   │                                                  │ verify sha256(code_verifier) == stored code_challenge (PKCE)
   │                                                  │ delete code (single-use) │
   │                                                  │ issue sed.i JWT + refresh token (hashed, stored in Redis)
   │ <── {access_token, refresh_token} ───────────────│                      │
   │                                                  │                      │
   │ POST /mcp-transport/mcp  Authorization: Bearer <jwt>                    │
   │   {"jsonrpc":"2.0","method":"tools/call","params":{"name":"add_content","arguments":{"url":"..."}}}
   │ ───────────────────────────────────────────────>│ MCPAuthMiddleware: decode JWT, look up user by sub
   │                                                  │ dispatch to add_content(user, db) ─────────────>│
   │                                                  │   ingest_url(...) — SAME service function as web save (§1)
   │                                                  │   extract_metadata.delay(item.id) ──────────────> (Celery, see §1)
   │ <── {"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\"item_id\":...,\"status\":\"queued\"}"}]}} │
```

### 5.3 Pseudocode trace, with state after each step

```python
# ── Step 1: OAuth authorize ──────────────────────────────────────
# app/mcp/oauth.py: authorize_post()
def authorize_post(email, password, client_id, redirect_uri, code_challenge, state, db):
    user = verify_credentials(email, password, db)   # else re-render login form with error
    code = secrets.token_urlsafe(32)
    redis.setex(f"mcp:code:{code}", 300, json.dumps({   # 5-minute TTL
        "email": user.email, "code_challenge": code_challenge,
        "redirect_uri": redirect_uri, "client_id": client_id,
    }))
    return redirect(f"{redirect_uri}?code={code}&state={state}")
# STATE: nothing in Postgres changes — the auth code lives only in Redis,
# and only for 5 minutes. If the agent doesn't exchange it in time, the
# whole flow has to restart from /authorize.

# ── Step 2: token exchange — PKCE verification ───────────────────
# app/mcp/oauth.py: token()
def token(grant_type, code, code_verifier, redirect_uri, client_id, db):
    stored = redis.get(f"mcp:code:{code}")
    assert stored and stored.redirect_uri == redirect_uri   # else 400
    assert sha256_b64url(code_verifier) == stored.code_challenge   # the actual PKCE check
    redis.delete(f"mcp:code:{code}")   # single use — replay fails from here on

    user = db.query(User).filter_by(email=stored.email).first()
    access_token = create_access_token({"sub": user.email}, ...)   # same JWT shape as web login
    raw_refresh = secrets.token_urlsafe(32)
    redis.setex(f"mcp:refresh:{sha256(raw_refresh)}", MCP_REFRESH_TOKEN_EXPIRE_DAYS * 86400, user.email)
    return {access_token, refresh_token: raw_refresh, token_type: "bearer"}
# STATE: agent now holds a sed.i JWT — structurally identical to one issued
# by the regular web /auth/login (§3). From this point on, MCP auth and web
# auth are indistinguishable to the rest of the app.

# ── Step 3: every tool call — auth middleware ────────────────────
# app/mcp/http_server.py: MCPAuthMiddleware
def __call__(request):
    token = extract_bearer(request.headers["Authorization"])
    user = resolve_user_from_bearer(token, db)   # jwt.decode + SELECT user, same as deps.py (§3)
    set_contextvar(_request_user_var, user)      # makes `user` available to the tool fn without
                                                   # threading it through every call signature
    return call_next(request)

# ── Step 4: the tool itself — reuses the web service layer ───────
# app/mcp/tools/write.py: add_content(url)
def add_content(url):
    user = _current_user()        # pulled from contextvar set in Step 3
    db = get_db()
    item = ingest_url(url=url, user=user, db=db, submitted_via="mcp")
    # ^ THE EXACT SAME FUNCTION as app/services/content.py:145, called by
    #   POST /content in the web flow (§1.2 Step 2). Same duplicate check,
    #   same normalize_url, same extract_metadata.delay() dispatch.
    return {"item_id": item.id, "status": "queued"}
# STATE: identical to a web-submitted URL at this point — same pending row,
# same Celery chain about to run (§1). There is no MCP-specific extraction
# path; an agent-saved URL goes through Phase 1 → Phase 2 → embedding
# exactly like a browser-saved one, just tagged submitted_via="mcp".

# ── Step 5: response reshaping back to MCP wire format ───────────
# FastMCP wraps whatever the tool function returns into:
{"jsonrpc": "2.0", "id": <matches request>,
 "result": {"content": [{"type": "text", "text": json.dumps(tool_return_value)}]}}
```

### 5.4 stdio path — what's actually different

```python
# app/mcp/auth.py: get_user_from_env()
def get_user_from_env(db):
    token = os.environ["SEDI_TOKEN"]      # set once, when the user configures
                                            # the MCP server in Claude Desktop's config
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    return db.query(User).filter_by(email=payload["sub"]).first()
# No OAuth dance at all — the user manually generates a long-lived JWT once
# (outside the scope of this trace) and pastes it into a local config file.
# Every stdio tool call re-derives the user from that same static env var.
# stdio communicates over stdin/stdout as raw JSON-RPC 2.0; all logging is
# redirected to stderr so it doesn't corrupt the protocol stream.
```

### 5.5 The 14 tools

Read-only: `list_lists`, `get_list_content`, `get_content_item`,
`search_content` (routes into the same `hybrid_search` from §2),
`find_similar`, `get_highlights`, `get_draft`, `get_reading_stats`,
`summarize_list` (gpt-4o-mini, cached), `query_library` (NL question → SQL → answer).

Write: `add_content` (traced above), `create_list`, `add_to_list`, `update_draft`.

Every read and write tool takes `(user, db)` derived from the auth layer, not
from explicit arguments — which is also why the same function bodies are
directly unit-testable independent of either transport.

### 5.6 What can go wrong / sharp edges

- **Data isolation is enforced per-query, not per-transport** — every tool's
  DB query filters by `user_id=user.id`/`owner_id=user.id` explicitly. There
  is no separate "MCP scope" or permission tier; an agent with a valid token
  has exactly the same access as the human logging in via browser. If asked
  "can an MCP agent see another user's library," the answer is structurally
  no — but it's the same per-row filter doing the work, not a dedicated
  authorization layer for agents.
- **stdio auth has no expiry enforcement loop** — the JWT itself still has
  its normal `exp` claim and will eventually fail `jwt.decode()`, but there's
  no refresh flow for stdio the way OAuth has one for HTTP. A user has to
  manually regenerate and re-paste the token when it expires.
- **`query_library`'s NL→SQL path** is the one tool worth flagging as
  architecturally different from the rest — it doesn't just filter by
  `user_id` on a known table, it's generating a query from a question, so
  its safety depends entirely on how tightly that generation is scoped
  (worth re-reading `app/mcp/server.py:348-375` directly before asserting
  anything stronger about its safety boundary in a live conversation).

---

## 6. PDF upload

### 6.1 There is no separate upload endpoint

A PDF isn't submitted differently from any other URL — the user pastes a
link, the same `POST /content` → `ingest_url()` → `extract_metadata.delay()`
path from §1 runs. The fork happens *inside* `extract_metadata`, right after
the response comes back from `requests.get()`, based on `Content-Type`
detection. There is no Phase 2 (`extract_full_content`) for PDFs at all —
everything happens inside Phase 1, synchronously, before that task returns.

### 6.2 Sequence diagram — the part that's different from §1

```
extract_metadata (Celery task)         _process_pdf()        subprocess (_yolo_worker.py)        S3
       │ content_type == 'pdf' ─────────>│                          │                              │
       │                                  │ extract_with_yolo() ────>│                              │
       │                                  │   pickle(pdf_bytes,url)  │                              │
       │                                  │   via stdin ────────────>│                              │
       │                                  │                          │ import torch (1.5GB+ load,   │
       │                                  │                          │   isolated to THIS process)  │
       │                                  │                          │ _extract_yolo_sync():        │
       │                                  │                          │   _prescan_document()        │
       │                                  │                          │   for each page:             │
       │                                  │                          │     YOLO layout detection    │
       │                                  │                          │     crop figures/tables       │
       │                                  │                          │     extract text blocks      │
       │                                  │                          │   _compute_confidence_score()│
       │                                  │   pickle(html) on stdout │<─────────────────────────────│
       │                                  │<─────────────────────────│ (process exits, torch freed   │
       │                                  │                          │  from address space entirely) │
       │                                  │ extract title/author/abstract from html + PDF metadata   │
       │                                  │ strip duplicate byline/abstract from body                │
       │ upload_pdf(pdf_bytes) ──────────────────────────────────────────────────────────────────────>│
       │   (no-op if AWS_S3_BUCKET unset)│                          │                              │
       │ status='completed' ─────────────│                          │                              │
       │ generate_embedding.delay() ──── (joins the same embedding task as §1)
```

### 6.3 Pseudocode trace, with state after each step

```python
# ── Fork point inside extract_metadata (app/tasks/extraction.py:456) ──
if content_type == "pdf":
    _process_pdf(item, resp.content, url)
    s3_key = upload_pdf(user_id=item.user_id, item_id=item.id, pdf_bytes=resp.content)
    if s3_key: item.s3_key = s3_key
    item.processing_status = "completed"
    db.commit()
    generate_embedding.delay(item.id)
    return   # extract_full_content is never called for this item

# ── _process_pdf — orchestration (app/tasks/extraction.py:668) ──────
def _process_pdf(item, pdf_bytes, url):
    html = extract_with_yolo(pdf_bytes, url)
    if not html: raise ValueError("PDF extraction returned empty result")

    # title priority: <h1>/<h2> from the rendered HTML > PDF's own metadata > URL-derived fallback
    html_title = pop_first_heading(html)         # also strips it from the body — avoid showing title twice
    pdf_meta = fitz.open(pdf_bytes).metadata
    item.title = html_title or pdf_meta.get("title") or title_from_url(url)
    item.author = pdf_meta.get("author") or None
    item.description = extract_injected_abstract_meta_tag(html)   # see Stage 3 below

    # strip duplicate byline/affiliation lines and the abstract block from the
    # body — both are already shown in the Reader header, would otherwise repeat
    html = strip_frontmatter_duplicates(html)
    item.full_text = html
    item.word_count, item.reading_time_minutes = compute_stats(html)
    item.content_vertical = "academic"

# ── extract_with_yolo — subprocess isolation (extraction_implementations.py:472) ──
def extract_with_yolo(pdf_bytes, url):
    payload = pickle.dumps((pdf_bytes, url))
    result = subprocess.run([sys.executable, "_yolo_worker.py"], input=payload,
                             stdout=PIPE, env={**os.environ, "OMP_NUM_THREADS": "2"},
                             timeout=300)
    if result.returncode != 0 or not result.stdout:
        return ""   # caller turns this into a ValueError
    return pickle.loads(result.stdout)
# WHY a subprocess, specifically: torch + ultralytics cost ~1.5GB+ RSS to
# import. Loading them inside the long-lived Celery worker process would
# leak that memory for the worker's entire lifetime, even between PDF
# extractions. Spawning a subprocess means the OS reclaims all of it the
# instant the subprocess exits — the Celery worker's own memory footprint
# never grows. The cost: pickle serialization of the full PDF bytes across
# a pipe, and a hard 300s timeout on the whole subprocess.

# ── _extract_yolo_sync — runs ONLY inside the subprocess (line 423) ─────
def _extract_yolo_sync(pdf_bytes, url):
    doc = fitz.open(pdf_bytes)
    model = get_or_load_yolo_model()   # hf_hub_download("hantian/yolo-doclaynet"),
                                         # cached on disk after first download —
                                         # cold-load cost is paid once per subprocess,
                                         # i.e. on EVERY PDF, since each gets a fresh subprocess
    scan = _prescan_document(doc)      # Stage 1, see below

    for page_num, page in enumerate(doc):
        regions, caption_rects = detect_layout_yolo(page, model)   # Stage 2
        page_html, num_images = extract_page_content(page, regions, caption_rects, scan, page_num)
        scan.yolo_conf_sum += sum(r["conf"] for r in regions)
        scan.total_words_extracted += count_words(page_html)
        html_pages.append(f"<div class='page'>{page_html}</div>")

    confidence = compute_confidence_score(scan)   # Stage 3, see below
    return wrap_html(html_pages, confidence, scan.title, scan.abstract)

# ── Stage 1: pre-scan (text metadata only, no rendering yet) ────────────
def _prescan_document(doc):
    # text-layer analysis across all pages before any image rendering:
    #   - column layout detection (1-col vs 2-col)
    #   - header/footer band y-positions (so Stage 2 can skip them)
    #   - repeating header/footer text (running titles, page numbers)
    #   - document title guess (largest font on page 1)
    #   - abstract guess (text block following an "Abstract" label)
    return DocumentScanResult(...)

# ── Stage 2: per-page YOLO layout detection ──────────────────────────────
def detect_layout_yolo(page, model):
    png = render_page_to_image(page, dpi=150)
    with torch.inference_mode():
        results = model(png)   # yolov8n-doclaynet classes: picture, table, formula, caption, ...
    return [{"label": r.label, "box_2d": r.box, "conf": r.confidence} for r in results], caption_rects
    # this is genuinely a vision model inference call per page, not a heuristic —
    # the cost scales with page count, run inside the same subprocess as everything else

# ── Stage 3: confidence scoring (extraction-result-based, not just layout heuristics) ──
def _compute_confidence_score(scan):
    score = 0
    score += min(35, scan.total_words_extracted / scan.page_count / 200 * 35)  # words/page density
    score += min(30, (scan.yolo_conf_sum / scan.yolo_conf_count) * 30)          # mean YOLO confidence
    score += min(25, scan.pages_with_content / scan.page_count * 25)           # % pages with any text
    score += min(10, scan.images_extracted / max(1, scan.images_attempted) * 10)  # image success rate
    return round(score)   # max 100; <60 labeled "low" confidence in the rendered HTML's meta tag
```

### 6.4 Why no Phase 2 / why this differs from the article path

Articles split into two Celery tasks (§1) specifically so a cheap metadata
fetch can update the UI fast while the slower full-text extraction runs
separately. PDFs don't get that benefit because there's no equivalent
"cheap metadata, then slow body" split available — YOLO has to run the full
layout pipeline before *any* useful text exists, so there's nothing
worthwhile to show after only a partial step. The entire PDF pipeline runs
synchronously inside Phase 1, and `extract_full_content` is simply never
invoked for these items — checked via `content_type == "pdf"`, never
revisited later in the pipeline.

### 6.5 Where the raw PDF bytes end up

`upload_pdf()` (`app/core/storage.py`) uploads to
`pdfs/{user_id}/{item_id}.pdf` in S3 — but only if `AWS_S3_BUCKET` is set;
otherwise it's a verified no-op and the key is simply never stored.
`item.full_text` (the extracted HTML) is what the Reader actually renders;
the original PDF bytes themselves are never kept in Postgres, only
(optionally) in S3, addressable later by `item.s3_key`.

### 6.6 What can go wrong / sharp edges

- **The YOLO model reloads from scratch on every single PDF** — because
  isolation is per-PDF (one subprocess per extraction), the
  `hf_hub_download` cache hits disk but the model weights still get loaded
  into a fresh process's memory every time. There's no warm worker pool for
  this specific task; the 1-3GB RSS spike mentioned in the code comments
  happens and is fully discarded after every PDF.
- **Hard 300s subprocess timeout** — a very long or image-heavy PDF that
  exceeds 5 minutes of wall-clock time inside the subprocess gets killed,
  `extract_with_yolo` returns `""`, and `_process_pdf` raises
  `ValueError("PDF extraction returned empty result")` — which propagates up
  into `extract_metadata`'s generic `except Exception` handler (§1.4),
  landing the item at `status='failed'` with no retry (that catch-all branch
  doesn't call `self.retry()`).
- **Confidence score is informational, not a gate** — a PDF that scores 10/100
  confidence still gets `status='completed'` and gets embedded; the score is
  surfaced to the reader as a low-confidence label, not used anywhere to
  trigger a fallback extraction method or a different status.
- **`extraction_implementations.py` has local uncommitted changes** (per
  `git status` at the time this doc was written) — the confidence-scoring
  function was mid-refactor from layout-heuristic-based scoring to
  extraction-result-based scoring (words/page, YOLO confidence, page
  coverage, image success — the version traced above). Worth re-checking
  `git diff` on that file before treating the exact point values above as
  final/committed.

---

## 7. Recommendation generation

### 7.1 Fully synchronous, fully uncached, no ML model

This is the simplest flow in the doc structurally — no Celery, no Redis, no
embedding generated on the fly — but it's worth tracing precisely because
"recommendation engine" sounds like it implies a model, and it doesn't: it's
a hand-written point formula, computed fresh in Python on every request.

### 7.2 Sequence diagram

```
Browser (dashboard)       FastAPI GET /content/recommended         Postgres
   │ click "For You"              │                                    │
   │ ──────────────────────────────>│ SELECT recent reads (7 days, with embedding) ──>│
   │                                │ <───────────────────────────────────────────────│
   │                                │ SELECT all unread items ────────────────────────>│
   │                                │ <───────────────────────────────────────────────│
   │                                │ for each unread item:                            │
   │                                │   cosine_similarity(item.embedding, each recent read.embedding)  [pure Python]
   │                                │   + recency decay + tag overlap + reading-time match            [pure Python]
   │                                │ sort by score, paginate                          │
   │ 200 {items, total} ────────────│                                                  │
   │ <───────────────────────────────│                                                  │
   │ render ContentCard grid       │                                                  │
```

### 7.3 Pseudocode trace, with the exact scoring breakdown as coded

```python
# app/api/content.py:326  GET /content/recommended
def get_recommended_content(skip=0, limit=10, mood=None, user, db):
    recent_reads = db.query(ContentItem).filter(
        user_id=user.id, is_read=True,
        read_at >= now - timedelta(days=7),
        embedding.isnot(None),
    ).all()

    unread = db.query(ContentItem).filter(
        user_id=user.id, is_read=False, deleted_at=None,
    ).all()
    if not unread: return {"items": [], "total": 0, ...}

    scored = []
    for item in unread:
        score = 0

        # Factor 1 — embedding similarity, max 30 pts
        if recent_reads and item.embedding:
            sims = [cosine_similarity(item.embedding, r.embedding)
                    for r in recent_reads if r.embedding]
            if sims:
                score += max(sims) * 30   # best match among recent reads, not average

        # Factor 2 — recency decay, max 20 pts, NOT capped at 0-floor by clock skew
        days_old = (now - item.created_at).days
        score += max(0, 20 - days_old / 10)   # hits 0 at 200 days old

        # Factor 3 — tag overlap, 10 pts PER matching tag, UNCAPPED
        if user.reading_patterns.get("preferred_tags"):
            overlap = len(set(item.tags) & set(user.reading_patterns["preferred_tags"]))
            score += overlap * 10   # an item with 5 matching tags scores 50 here alone —
                                      # the "max 75" framing in ARCHITECTURE.md is a typical
                                      # ceiling assuming ~1 tag overlap, not an enforced cap

        # Factor 4 — reading-time match, max 15 pts
        if user.reading_patterns.get("avg_reading_time") and item.reading_time_minutes:
            diff = abs(item.reading_time_minutes - user.reading_patterns["avg_reading_time"])
            score += max(0, 15 - diff / 2)   # hits 0 at a 30-minute difference

        # Mood filter — applied as a skip, not a score adjustment
        if mood == "quick_read" and item.reading_time_minutes > 10: continue
        if mood == "deep_dive" and item.reading_time_minutes and item.reading_time_minutes < 10: continue
        if mood == "light" and item.word_count and item.word_count > 5000: continue

        scored.append((item, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return paginate(scored, skip, limit)   # full items returned, score itself never exposed to frontend

def cosine_similarity(a, b):   # pure Python, no numpy, no pgvector — runs once per (unread, recent) pair
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm(a) * norm(b))
```

### 7.4 Where `reading_patterns` actually comes from

```python
# app/api/content.py:92  update_reading_patterns(user, item)
# Called synchronously inside the PATCH /content/{id} handler, whenever a
# request marks is_read=True OR read_position crosses 0.9 (auto-mark-as-read
# on scroll). NOT a separate background job — it's part of the same request
# that marks the article read.
def update_reading_patterns(user, item):
    if item.reading_time_minutes:
        user.reading_patterns["readings"].append(item.reading_time_minutes)
        user.reading_patterns["readings"] = last_20(user.reading_patterns["readings"])  # rolling window
        user.reading_patterns["avg_reading_time"] = mean(user.reading_patterns["readings"])

    if item.tags:
        for tag in item.tags:
            if tag not in user.reading_patterns["preferred_tags"]:
                user.reading_patterns["preferred_tags"].append(tag)
        # NOTE: preferred_tags only ever grows. No dedup-by-recency, no decay,
        # no cap. A user who's read 500 different tags over a year has all
        # 500 sitting in this list, weighted identically to a tag they read
        # yesterday. This directly feeds Factor 3 above — old, possibly
        # abandoned interests keep scoring recommendations forever.
```

### 7.5 Cost model and why there's no cache

- **No Redis cache, no DB-stored score, no TTL.** Every `GET
  /content/recommended` call re-fetches recent reads + the full unread set
  and rescoring happens in pure Python, with cosine similarity computed by
  hand (no numpy, no pgvector operator) for every `(unread item, recent
  read)` pair — `O(unread_count × recent_read_count)` per request.
- This is a deliberate simplicity choice, not an oversight: at the scale of
  one user's unread queue (typically tens to low hundreds of items) and a
  7-day recent-read window, this finishes well under typical request
  budgets. It would not hold up if either set grew into the thousands —
  there's no pagination-at-the-query-level; both `recent_reads` and `unread`
  are loaded as full Python lists before any scoring happens.
- **Mood filtering happens after full scoring**, as a `continue` inside the
  scoring loop — it doesn't reduce the query scope, it just discards already-
  scored items before sorting.

### 7.6 What can go wrong / sharp edges

- **Cold-start has almost no signal**: a brand-new user (no `recent_reads`,
  no `reading_patterns`) gets pure recency-decay scoring (Factor 2 only,
  max 20/75) — every unread item is ranked essentially by "how recently was
  it saved," not personalization. This is invisible in the UI; there's no
  "not enough data yet" messaging distinguishing a cold-start ranking from a
  personalized one.
- **The "max 75 points" figure is a typical case, not an enforced ceiling**
  — Factor 3 (tag overlap) has no `min()` clamp, so an item matching many
  preferred tags can score arbitrarily higher than 75 and dominate the
  ranking disproportionately to the other three factors.
- **`reading_patterns["preferred_tags"]` never shrinks** — see §7.4. Tag
  preference is monotonically additive for the lifetime of the account.
