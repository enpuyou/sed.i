---
layout: layouts/chapter.njk
title: Saving a URL
part: 1
order: 1
summary: Why the API answers 201 straight away, how a URL is cleaned and checked for duplicates, and how the item's status moves while the slow work happens later.
verified: 2026-10-25
permalink: /ingestion/saving-a-url/
---

## In one minute

When you paste a URL, the API does only the fast part. It checks that you are allowed to call it and are not calling it too often, cleans the URL (lowercase host, no tracking parameters, no fragment), looks for the same URL already in your library, and inserts one row with `processing_status = 'pending'`. Then it puts a message on a queue and answers `201 Created`. The page is not downloaded yet.

A separate worker process takes the message later, downloads the page, extracts the article and moves the row through `processing`, then `completed` or `failed`. The browser polls the row to show progress. If the message never reaches the queue, a sweep that runs every 5 minutes finds rows still `pending` after 10 minutes and sends the message again.

## The problem

**Why does `POST /content` return before the article exists, and what keeps that arrangement correct?**

The obvious design is synchronous: receive the URL, download the page, extract the text, store it, and reply when done. It is simple, and the reply contains the finished article. It fails in four ways.

1. **Latency is controlled by someone else.** A remote site can take 30 seconds, hang, or refuse the connection. The code's own timeout for a page download is 30 seconds per fetch, and extraction fetches the page twice. A web request that long is a bad button press and ties up a server worker for the whole time.
2. **Failures are the normal case.** Paywalls, bot blocks, 403s and timeouts happen on a meaningful share of pages. A synchronous design has to decide whether a failed download means "do not save the URL". For a read-it-later app the answer is no: the user wants the link kept even when the text could not be fetched.
3. **Retries need somewhere to live.** Retrying a slow download with backoff inside a web request is not possible; in a queue it is a setting.
4. **Work must survive a restart.** A deploy during a synchronous request loses the request. A row in Postgres plus a queue message can be picked up again.

The price of the asynchronous design is that the system now has a state to keep honest: a row that says `pending` must eventually become something else. Most of this chapter is about the places where that can go wrong.

## Mental model

Think of a coat check. The attendant takes your coat, writes a ticket (the row), hangs the coat on a rail (the queue) and hands you the ticket in seconds. The coat is cleaned later by someone in the back. If the ticket is written but the coat never reaches the rail, a person walks the front desk every five minutes looking for tickets older than ten minutes with no coat attached.

| Term | Meaning here |
|---|---|
| Normalization | Rewriting a URL into one canonical spelling so two spellings of the same page compare equal. |
| Duplicate detection | Refusing to create a second active row for the same user and normalized URL. |
| Celery task / message | A unit of background work. The API publishes a message (task name plus arguments) to Redis; a worker process consumes it. |
| `processing_status` | The row's state: `pending`, `processing`, `completed`, `failed`. |
| Soft delete | Setting `deleted_at` instead of removing the row. Duplicate checks ignore soft-deleted rows. |
| Sweep | A periodic task that looks for rows in a bad state and repairs them. |

{% fig "Saving a URL: what runs where" %}
```text
 browser / extension / MCP client
   | POST /content {url}
   v
 [rate limit]  ->  429 if over 10/min or 50/hour
   v
 [auth]        ->  401 if no valid token or cookie
   v
 [ingest_url]  normalize -> look up duplicate -> INSERT (pending)
   |               \-> 409 {existing_id} if duplicate
   | extract_metadata.delay(id)   (message to Redis)
   v
 201 Created  (row returned, pending)
 ..................................................
 worker (later)   pending -> processing -> completed | failed
 beat (every 5 min)   re-send message for rows pending > 10 min
```
{% endfig %}

{% fig "The status machine" %}
```text
 pending --extract_metadata starts--> processing
    |                                    |
    |                                    +--phase 2 ok-------> completed
    |                                    +--phase 2 soft error> completed
    |                                    |   (processing_error set)
    |                                    +--401/403/exception-> failed
    |
    +--extension save (API sets it directly)----------------> completed
```
{% endfig %}

## Walkthrough: saving one URL with tracking parameters

The input is a URL pasted from a newsletter:

```text
https://Journal.Example.com/posts/pour-over/?utm_source=newsletter&utm_medium=email&id=42#comments
```

Everything below was captured by running the real code: the FastAPI `TestClient` against a scratch Postgres database (not the development data), with the real Celery publisher writing to a Redis logical database. The only substitution was the queue name, so that no worker could consume the messages. The user, host names and ids are made up for the demonstration.

### Step 1: the request passes the rate limiter

`RateLimitMiddleware` wraps every request. For `POST /content` it applies two sliding windows: 10 requests per 60 seconds and 50 per 3,600 seconds. It needs a key to count against, and builds it here:

{% fig "middleware/rate_limit.py: choosing the identifier" %}
<!--src content-queue-backend/app/middleware/rate_limit.py#142-152-->
```python
# Get identifier
user_id = "unknown"

if hasattr(request.state, "user"):
    user = request.state.user
    if user and hasattr(user, "id"):
        user_id = f"user:{user.id}"
elif request.client:
    user_id = f"ip:{request.client.host}"

route_key = f"{request.method}:{request.url.path}:{user_id}"
```
{% endfig %}

The code prefers a user id and falls back to the client IP. **The user branch never runs.** Nothing in the application assigns `request.state.user` (searching `app/` finds only this read), and the middleware runs before the route's authentication dependency, so there is no user to read at that point. `hasattr` is therefore always false and the key is always the IP. The captured Redis keys after the first request confirm it:

{% fig "Redis keys created by one POST /content (captured)" %}
```text
ratelimit:POST:/content:ip:203.0.113.7:hour:3600
ratelimit:POST:/content:ip:203.0.113.7:minute:60
```
{% endfig %}

Each key is a sorted set of request timestamps; the check removes entries older than the window, counts what is left, and rejects when the count has reached the limit. A rejection is a `429` with a `Retry-After` header of 60 or 3,600 seconds. If Redis cannot be reached, the limiter allows the request ("fail open").

One consequence was also captured: two different users calling from the same IP share one bucket. After user A made 10 requests, user B's first request from the same address got `429` with `Retry-After: 60`; from another address user B got `201`. This is a limit per address, not per user.

### Step 2: authentication

The route depends on `get_current_active_user`, which calls `get_current_user`. That function reads the token from the `Authorization: Bearer` header first (the browser extension and MCP clients use it) and falls back to the httpOnly auth cookie (the web frontend uses it). Both are the same JWT, decoded with the same secret; the `sub` claim is the user's email, and the user row is loaded from it. No token or a bad token gives `401`; an inactive user gives `403`. Cookie-authenticated writes are additionally covered by the CSRF middleware; Bearer requests are not. Chapter [Auth and security](/platform/auth-and-security/) covers this in depth.

### Step 3: normalization

`ingest_url` first calls `normalize_url`:

{% fig "services/content.py: normalize_url" %}
<!--src content-queue-backend/app/services/content.py#89-104-->
```python
url = url.strip()
parsed = urlparse(url)
clean_params = {
    k: v
    for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
    if k.lower() not in _STRIP_PARAMS
}
clean_path = parsed.path.rstrip("/")
normalized = parsed._replace(
    scheme=parsed.scheme.lower(),
    netloc=parsed.netloc.lower(),
    path=clean_path,
    query=urlencode(sorted(clean_params.items()), doseq=True),
    fragment="",
)
return normalized.geturl()
```
{% endfig %}

It lowercases scheme and host, strips trailing slashes from the path, removes any query parameter named in `_STRIP_PARAMS` (35 names: the `utm_*` family, `fbclid`, `gclid`, `msclkid`, HubSpot `hsa_*`, Mailchimp, and the generic `ref`, `source`, `campaign`, `medium`), sorts what remains, and drops the fragment. The output of the function on nine inputs (captured):

{% fig "normalize_url on real-looking URLs (captured)" %}
```text
'https://Example.COM/Posts/Espresso-Basics/?utm_source=newsletter&utm_medium=email&id=42#comments'
  -> 'https://example.com/Posts/Espresso-Basics?id=42'
'https://blog.example.org/2026/10/grind-size/?fbclid=IwAR0x&ref=twitter'
  -> 'https://blog.example.org/2026/10/grind-size'
'https://news.example.net/story?b=2&a=1&gclid=abc'
  -> 'https://news.example.net/story?a=1&b=2'
'https://example.com/watch?v=abc123&t=90s'
  -> 'https://example.com/watch?t=90s&v=abc123'
'  http://example.com  '
  -> 'http://example.com'
'https://example.com/search?q=&page=2'
  -> 'https://example.com/search?page=2&q='
'https://example.com/Article/'
  -> 'https://example.com/Article'
'example.com/no-scheme'
  -> 'example.com/no-scheme'
'https://example.com/posts/espresso-basics'
  -> 'https://example.com/posts/espresso-basics'
```
{% endfig %}

(Long lines in the first block are the inputs, shown as printed.) Read the results for what normalization does not do:

- The **path keeps its case**: `/Posts/Espresso-Basics` and `/posts/espresso-basics` are different URLs after normalization, which matches how most servers treat paths, but some sites would serve the same page for both.
- `http` and `https`, and `www.` and bare hosts, stay different. So do default ports.
- A **string with no scheme passes through unchanged**. The web endpoint does not validate that the input is a URL at all; `"not a url"` returned `201` and a row (captured). The MCP save path does check for a scheme and host. See "What is weak or missing".
- Stripping the generic names `ref`, `source`, `campaign` and `medium` is a judgment call: on a site where `?source=` selects the page, two different pages would collapse to one.

For our input the result is:

```text
https://journal.example.com/posts/pour-over?id=42
```

The `id=42` parameter stays because it is not a known tracking name; the trailing slash, the `utm_*` pair and `#comments` are gone.

### Step 4: duplicate detection

The next call is one indexed lookup:


{% fig "services/content.py: find_existing_active_item" %}
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

This is correct only if every stored `original_url` is already normalized. New rows are (the web save, the MCP save and the onboarding seeds all store the normalized form). Older rows were not, so migration `a7d3e9c15b42` (dated 2026-10-25) rewrote every active row. When two of a user's rows collapsed to the same URL, it kept one winner (the row with highlights, else the already-normalized one, else the newest), gave the winner the losers' list memberships, and soft-deleted the losers. Before that migration the lookup had a second step that normalized every stored URL in Python, a scan proportional to the size of the library. `defer_heavy_columns()` keeps the lookup from loading article text: in the development library the median stored article is 14,433 characters, the 90th percentile 56,745 and the largest 4,463,712 (measured, 66 items with text).

The lookup ignores soft-deleted rows, so a URL you deleted can be saved again. It does not ignore archived rows: an archived duplicate still blocks the save, and the response says so with `is_archived`.

The database backs the lookup with a partial unique index, created by migration `5534fbac2811`:

{% fig "alembic/versions/5534fbac2811: one active row per user and URL" %}
<!--src content-queue-backend/alembic/versions/5534fbac2811_add_unique_constraint_content_url.py#42-44-->
```sql
CREATE UNIQUE INDEX uq_content_items_user_url_active
ON content_items (user_id, original_url)
WHERE deleted_at IS NULL
```
{% endfig %}

The index does two jobs: it makes the lookup fast, and it rejects a second active row if two requests race. It exists only because a migration creates it. The SQLAlchemy model does not declare it, so a test database built with `create_all` (as `tests/conftest.py` does) has no such index; the scratch database for this chapter created it by hand.

Submitting a second spelling of the same page, `https://journal.example.com/posts/pour-over?id=42&fbclid=zzz`, normalizes to the stored value and returns `409`:

{% fig "Second submit of the same page (captured)" %}
```json
{"detail": "{\"message\": \"Already in your library\", \"existing_id\": \"8db7b280-346f-40be-9fa9-fecdd970d096\", \"is_archived\": false}"}
```
{% endfig %}

Note the shape: the HTTP body is the usual `{ "detail": string }`, and that string is itself JSON. Clients parse it a second time to get `existing_id` and `is_archived`, so they can link to the existing item or offer to unarchive it. No second Celery message was published (still one message for the item).

**Two requests at the same instant.** The lookup and the insert are separate statements, so two requests can both find nothing and both insert. The unique index then rejects the second insert at `commit()`. What happens next is in the code path, not in a handler: `ingest_url` does not catch `IntegrityError`. It propagates to the application's `SQLAlchemyError` handler, which logs it and returns a generic `500`. To reproduce it, two threads were made to wait for each other between the lookup and the insert. Captured result:

{% fig "Two concurrent POSTs of the same new URL (captured)" %}
```text
   201 <new item id>
   500 A database error occurred. Please try again later.
   rows for that URL: 1
```
{% endfig %}

The outcome is safe for the data (one row, one queued message; the loser never reaches the dispatch line) but not friendly: the loser sees an error instead of the `409` it would have got a moment later. The production-readiness audit proposed catching `IntegrityError` and re-running the lookup to raise `DuplicateContentError`; the index from that proposal exists, the catch does not.

### Step 5: insert the row and publish the message

If no duplicate exists, the rest of `ingest_url` runs:

{% fig "services/content.py: create the row, then dispatch" %}
<!--src content-queue-backend/app/services/content.py#174-200-->
```python
normalized = normalize_url(url)

existing = find_existing_active_item(
    db=db, user_id=user.id, normalized_url=normalized
)
if existing:
    raise DuplicateContentError(
        existing_id=str(existing.id),
        is_archived=existing.is_archived,
    )

item = ContentItem(
    user_id=user.id,
    original_url=normalized,
    submitted_via=submitted_via,
    processing_status="pending",
)
db.add(item)
db.commit()
db.refresh(item)

if dispatch_extraction:
    from app.tasks.extraction import extract_metadata

    extract_metadata.delay(str(item.id))

return item
```
{% endfig %}

The order matters. The row is committed before the message is published. If it were the other way round, a fast worker could receive the message before the row is visible and find nothing. The cost of this order is the gap between the two lines: if the process dies after `db.commit()`, or Redis refuses the publish, a `pending` row exists with no message.

The response is the new row, serialized by `ContentItemResponse` (the long `full_text` is not part of it). The relevant captured fields (the request took 76 ms on a laptop with a local Redis; one run, so a rough figure, not a benchmark):

{% fig "POST /content response, abridged (captured)" %}
```text
POST /content -> 201
  original_url: https://journal.example.com/posts/pour-over?id=42
  title: None
  processing_status: pending
  processing_error: None
  word_count: None
  content_type: None
```
{% endfig %}

And the message that landed on the queue. Celery stores it as JSON with a base64 body; decoded, the task name and arguments are:

{% fig "The Celery message (captured, decoded)" %}
```text
task:  app.tasks.extraction.extract_metadata
args:  ['8db7b280-346f-40be-9fa9-fecdd970d096']
```
{% endfig %}

The message carries only the item's id. The worker re-reads everything else from Postgres, so a message can be re-sent safely and never carries stale data. Re-sending a message is therefore mostly harmless.

### Step 6: the status changes as the worker runs

The status transitions were captured by running the tasks directly (not through a worker) with the page download replaced by a saved sample page, so no network call was made. The sample is the one used in [Extracting an article](/ingestion/extracting-an-article/).

{% fig "One item through the pipeline (captured)" %}
```text
after POST                   status=pending     title=None
after extract_metadata       status=processing  title='How pour-over coffee works'
   queued: extract_full_content
after extract_full_content   status=completed   words=257
   queued: generate_embedding
403 from the source          status=failed
   error='Access forbidden (403) - Site blocks bots'
```
{% endfig %}

`extract_metadata` flips the row to `processing` in its first commit and leaves it there while it reads the page's metadata. The row only becomes `completed` when the second task, `extract_full_content`, finishes. The sweep below depends on this first flip.

Which code sets each state:

| State | Set by | Condition |
|---|---|---|
| `pending` | `ingest_url`; the MCP save; the onboarding seed articles | Row creation (also the column default). |
| `processing` | `extract_metadata` | First commit, for every URL that has no pre-extracted text. |
| `completed` | `extract_full_content` | Text extracted, or "graceful" ends: request timeout, request error, or no extractable text (each with `processing_error` set). Also the PDF branch of `extract_metadata`. |
| `completed` | `create_content_item` (API handler) | Extension save with `pre_extracted_html`: set directly, no extraction task. |
| `failed` | `extract_metadata` | HTTP 401, HTTP 403, any other HTTP error, a timeout, or any other exception. |
| `failed` | `generate_embedding` | Embedding failed after its retries, even though the text was saved. |

Two details are easy to miss. For HTTP errors other than 401 and 403, and for timeouts, `extract_metadata` sets `failed`, commits, and then calls `self.retry` with a delay of 60 seconds times 2 to the retry number (Celery's default of 3 retries applies; read from the code, not run, because retries need a worker). So a row can read `failed` while a retry is waiting, then return to `processing`. And a `failed` row from the embedding step looks to the frontend like a failed extraction even though the article text is stored.

### Step 7: the extension path

The browser extension has already extracted the article in the user's own browser, with their login. It sends `pre_extracted_html` (plus optional title, author, description, thumbnail, date, highlights). The handler detects it with `bool(item_data.pre_extracted_html)` and changes three things:

{% fig "api/content.py: choosing the extension path" %}
<!--src content-queue-backend/app/api/content.py#151-173-->
```python
is_extension = bool(item_data.pre_extracted_html)

try:
    new_item = ingest_url(
        url=item_data.url,
        user=current_user,
        db=db,
        submitted_via="extension" if is_extension else "web",
        dispatch_extraction=not is_extension,
    )
except DuplicateContentError as exc:
    raise HTTPException(
        status_code=409,
        detail=json.dumps(
            {
                "message": "Already in your library",
                "existing_id": exc.existing_id,
                "is_archived": exc.is_archived,
            }
        ),
    )
```
{% endfig %}

`ingest_url` is told not to dispatch. The handler then strips the title heading, description paragraph and thumbnail image from the HTML (the reader shows those separately), writes `full_text` and the metadata fields, counts words (reading time at 200 words per minute), creates any highlights captured before saving, sets `completed`, commits, and publishes two messages: `extract_metadata` (which, seeing text already present, fetches the page only when the thumbnail or description is missing, and checks for a paywall) and `generate_embedding`. The row is therefore committed twice: first `pending` inside `ingest_url`, then `completed`. Chapter [The browser extension](/ingestion/browser-extension/) covers the client side.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/api/content.py` | `create_content_item`: auth dependency, extension branch, list attachment, the 409 payload |
| `content-queue-backend/app/services/content.py` | `normalize_url`, `find_existing_active_item`, `ingest_url`, `DuplicateContentError`; shared by the web API and MCP |
| `content-queue-backend/app/middleware/rate_limit.py` | `RateLimitMiddleware`, `RedisRateLimiter`, the per-route limits table |
| `content-queue-backend/app/core/deps.py` | `get_current_user`: Bearer first, cookie second |
| `content-queue-backend/app/tasks/extraction.py` | `extract_metadata`, `extract_full_content`, `recover_stale_pending_items` |
| `content-queue-backend/app/core/celery_app.py` | the beat schedule entry that runs the sweep every 5 minutes |
| `content-queue-backend/alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py` | rewrites old URLs so exact-match lookup is correct |
| `frontend/hooks/useProcessingPolling.ts` | polls items that are `pending` or `processing` every 5 seconds |

The recovery sweep, in full:

{% fig "tasks/extraction.py: recover_stale_pending_items" %}
<!--src content-queue-backend/app/tasks/extraction.py#1766-1781-->
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

for item in stale:
    logger.warning(
        f"recover_stale_pending_items: re-dispatching stuck item {item.id} "
        f"(created {item.created_at}, still pending)"
    )
    extract_metadata.delay(str(item.id))
```
{% endfig %}

The task takes a Redis lock first (time-to-live 10 minutes), so an overlapping run skips instead of dispatching the same item twice. It exists because a failure-injection test (`docs/changelog/2026-07-28-failure-injection-fixes.md`) found an item stuck at `pending` forever after a request arrived while Redis was reloading its data after a restart.

The same behavior can be seen directly. With a `pending` item aged 11 minutes and a `pending` item just created, one run of the sweep returned:

{% fig "recover_stale_pending_items (captured)" %}
```text
sweep: {'recovered': 1}
   stuck item queued: ['extract_metadata'] | fresh item queued: []
```
{% endfig %}

The failure that the sweep exists for was also reproduced: with the publish call made to raise a broker error, `POST /content` returned `500`, the row stayed `pending`, and retrying the same URL returned `409` (captured). So after a broker failure the user sees an error, their retry says the link is already saved, and the sweep makes it real within about 15 minutes (10 minutes of staleness plus up to one 5-minute beat interval). The `500` body in that capture was a debug traceback because the local environment turns `DEBUG` on; with `DEBUG` off the catch-all handler returns a generic message.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Return `201` with a `pending` row, extract later | Download inside the request; return `202` with a job id and no row | The user sees the item at once and the link is kept even if extraction fails. A row is the job record, so no separate job table is needed. |
| Row first, message second | Message first; a transactional outbox table | Commit-then-publish means a worker never sees a missing row. The gap is covered by a sweep instead of an outbox, which is less code and good enough at this scale. |
| Normalize on write, exact lookup on read | Normalize every stored URL at read time; store a separate canonical-URL column | One indexed equality lookup. It depends on a migration having rewritten old rows and on every writer normalizing; the previous scan did not depend on either. |
| Strip a fixed list of tracking parameters | Keep every parameter; keep only an allow-list | A fixed list is predictable and removes the common noise. It will miss new tracking names and wrongly strips generic names such as `source` on sites that use them for real. |
| Unique partial index plus a check | Check only; index only | The check gives a friendly 409; the index makes the invariant true under concurrency. Handling the index error as a 409 is the missing third piece. |
| Rate limit by IP in a middleware, backed by Redis | Per-user limits in a route dependency; an in-process counter | A middleware sees every request before any work. Redis shares the count across instances. The identifier choice is weak (below). |
| Sweep for `pending` rows | Wrap every `.delay()` in try/except; acknowledge messages late | One recovery path for any cause of a lost message, instead of many call sites. See the failure-injection note above. |

## How it fails

| Failure | What happens | What recovers it |
|---|---|---|
| Redis down when the message is published | `500` to the client; row committed as `pending` | The sweep re-sends after 10 minutes. The client's retry returns `409`. |
| Redis down when the limiter runs | Request allowed (fail open) | Nothing needed; limits are not enforced meanwhile. |
| Two identical requests at once | One `201`, one `500` (index violation) | Not recovered; the client can retry and gets `409`. |
| Worker killed after `processing` was set | Row stays `processing`; no sweep looks at it | Not recovered. The production-readiness audit (item P1-3) proposes sweeping old `processing` rows or acknowledging messages late; neither is in the code. |
| Source returns 403 or 401 | `failed` with a readable error; no retry | The user opens the original link. |
| Source times out or returns 5xx | `failed`, then retried with backoff up to 3 times | Retry; the final state stays `failed` if all attempts fail. |
| Rate limit exceeded | `429` with `Retry-After` | Wait. |

In the development library, 4 of 71 active items are `pending`, all saved through the web between January and July 2026. The likely cause is that no worker or beat process was running when they were saved; this was not investigated. It does show what a stuck row looks like to the browser, which keeps polling every 5 seconds for any `pending` or `processing` item it displays.

## What is weak or missing

Ranked by how likely each is to matter.

1. **The rate limit is per IP, not per user.** Documentation says per user; the code cannot do that because the middleware runs before authentication and `request.state.user` is never set. Users behind one address (an office, a shared connection) share 10 saves per minute. Worse, if the production proxy's address is what the app sees as the client, every user shares one bucket per route. The start command passes no trusted-proxy setting, and uvicorn only trusts forwarded headers from `127.0.0.1` by default, so this is likely; it was not checked in production. Fix: authenticate inside the limiter (decode the token for the key) or move the check to a route dependency after `get_current_user`, and configure the trusted proxy address for the fallback.
2. **A concurrent double submit returns a `500`.** Catch `IntegrityError` in `ingest_url`, roll back, re-run the lookup, and raise `DuplicateContentError`.
3. **`processing` rows are never swept.** A worker that dies mid-extraction leaves a spinner forever.
4. **No URL validation on the web path.** `ContentItemCreate.url` is a plain string; the MCP save checks for a scheme and host, the web endpoint does not. Nothing checks the host either: the fetch step itself does not block private or internal addresses (stated in the docstring of `core/fetch.py`; the audit tracks it as P0-1). Fix: require `http` or `https`, and refuse private, loopback and link-local addresses at both ingestion and fetch time, including after redirects.
5. **Normalization is lossy in both directions.** `http` and `https`, and `www.` and bare hosts, are different; generic parameter names are stripped on every site. The fragment is dropped, which merges pages on sites that route by `#`.
6. **Two commits in the extension path.** Between the `pending` insert and the `completed` update, the row looks unprocessed.
7. **Status is overloaded.** `failed` can mean a source error or a later embedding error; the client cannot tell from the status alone.

{% say %}
When you save a link, the API only does the quick part. It checks your token, counts the request against a Redis-backed limit, cleans the URL by lowercasing the host, dropping tracking parameters and the fragment, and looks for the same cleaned URL already in your library. If it is new, it inserts a row marked pending, publishes one message with the row's id, and returns 201. A worker picks up the message later, marks the row processing, fetches the page, and ends at completed or failed. A beat task every five minutes re-sends the message for any row still pending after ten minutes, which covers the case where the queue was down at save time. The weak spots I would name are that the rate limit is really per IP because the middleware runs before auth, that two simultaneous saves of one URL give one user a 500 instead of a 409, and that nothing recovers a row stuck in processing.
{% endsay %}

## Check yourself

{% qa "Why is the row committed before the Celery message is published, and what does that order cost?" %}
A worker may receive the message immediately. If the row were not committed yet it would find nothing. The cost is a window in which a row exists without a message (a crash or a broker error after the commit), which the 5-minute sweep repairs for rows that stay `pending`.
{% endqa %}

{% qa "Two users behind the same office router each save links. What does the rate limiter count, and what would you change?" %}
It counts per client IP, so both share one bucket of 10 per minute and 50 per hour. The code looks for a user id on `request.state`, but nothing sets it and the middleware runs before authentication. Key on the authenticated user (decode the token in the limiter or enforce the limit in a dependency) and keep the IP only as a fallback.
{% endqa %}

{% qa "A user saves https://site.com/a/?utm_source=x#top and later https://site.com/a. What does the second request return, and which two mechanisms are involved?" %}
`409` with the first item's id. Normalization maps both to `https://site.com/a`, and the lookup (backed by the partial unique index) finds the existing active row.
{% endqa %}

{% qa "Redis is unreachable at the moment a user saves a link. Trace what the user sees and how the link eventually gets processed." %}
The row is committed as `pending`, then the publish fails, so the user gets a `500`. If they retry they get `409` because the row exists. After the row is more than 10 minutes old, the beat sweep finds it still `pending` and publishes `extract_metadata` again; the sweep depends on `extract_metadata` setting `processing` as its first action.
{% endqa %}

{% qa "Why does the extension path set the status to completed in the API handler and still dispatch extract_metadata?" %}
The article text is already in the request, so there is nothing to wait for and the row can be read at once. The extraction task is still sent to fill missing metadata (such as a thumbnail) and to check the page for a paywall; it leaves the status as `completed`.
{% endqa %}

{% qa "The unique index was added in May 2026, yet duplicate detection still needed a migration in October 2026. Why?" %}
The index only blocks identical strings. Rows saved earlier were stored in different spellings (trailing slash, fragment, tracking parameters), so exact-match lookup on a normalized URL would not have found them. The October migration rewrote them to the normalized form, so one equality lookup is enough.
{% endqa %}
