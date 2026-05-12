# Content Queue — Architecture & Design Doc

> **Living document.** Update in the same commit as the feature change.
> Goal: any new contributor (or future AI session) can get oriented quickly.

---

## 1. What the app does

Content Queue is a read-it-later app branded **sed.i**. Users paste a URL and the
system:

1. Saves it to their personal queue immediately.
2. Extracts title, description, thumbnail, and author in a background job.
3. Extracts the full article text for an in-app reader.
4. Generates an embedding for semantic search.
5. Auto-suggests tags with a hybrid free/cheap-LLM approach.
6. Supports organization into lists, highlights, and a vinyl record collection.

---

## 2. Glossary

| Term | Definition |
|------|-----------|
| **Frontend** | Code running in the browser (UI, pages, state). |
| **Backend** | Code running on the server (auth, data rules, API). |
| **API** | Set of HTTP routes the frontend calls to get or update data. |
| **ORM** | Object-Relational Mapper — use classes instead of raw SQL. |
| **JWT** | JSON Web Token — signed string that proves a user is logged in. |
| **Background job** | Work queued and executed after the HTTP request returns. |
| **Celery task** | A Python function run by a Celery worker process. |
| **Embedding** | A vector (list of 1536 numbers) encoding text meaning. |
| **pgvector** | Postgres extension for storing and querying vectors. |
| **Cosine distance** | `<=>` operator in pgvector — 0 means identical, 2 means opposite. |
| **Soft delete** | Mark rows with `deleted_at` rather than removing them. |
| **SSRF** | Server-Side Request Forgery — backend fetches a URL controlled by attacker. |
| **XSS** | Cross-Site Scripting — malicious HTML/JS in the browser. |
| **Feature flag** | Env var–driven boolean that hides incomplete UI sections. |

---

## 3. System overview

```
Browser / Extension
  └─> Next.js Frontend (Vercel)
        └─> FastAPI Backend (Railway)
              ├─> Postgres + pgvector  (data + vector search)
              ├─> Redis               (Celery broker/result backend)
              └─> Celery Workers (Railway)
                    ├─> URL fetch + metadata extraction (requests + BS4 + trafilatura)
                    ├─> PDF layout extraction (YOLO-based)
                    ├─> Embedding generation (OpenAI text-embedding-3-small)
                    ├─> Auto-tagging (pgvector similarity + gpt-4o-mini fallback)
                    ├─> AI summarization (OpenAI)
                    └─> Discogs metadata fetch (vinyl records)
```

**Why this split:**
- Frontend must feel instantly responsive — API returns before slow work finishes.
- Backend enforces auth and data ownership.
- Workers handle slow/failable work (URL fetching, LLM calls).

---

## 4. Technology choices

### Frontend

| Technology | Role |
|-----------|------|
| **Next.js 14 App Router** | Routing, SSR, Vercel deployment. Folders under `frontend/app/` map to URLs. |
| **React** | Component-based UI. Key pattern: props + local state + context. |
| **Tailwind CSS** | Utility classes for all styling. |
| **React Context** | Shared state without prop drilling (auth, lists, toasts, player). |
| **YouTube IFrame API** | Vinyl player embedded via `YouTubePlayer.tsx`. |

### Backend

| Technology | Role |
|-----------|------|
| **FastAPI** | HTTP API framework. `@router.get/post/patch/delete` decorators define routes. |
| **Pydantic** | Request/response validation. FastAPI uses it automatically. |
| **SQLAlchemy** | ORM — Python classes map to Postgres tables. |
| **Alembic** | Database migrations. Run `alembic upgrade head`. |
| **Postgres + pgvector** | Main database. pgvector adds `Vector(1536)` column type and `<=>` cosine operator. |
| **Redis** | Message broker for Celery. |
| **Celery** | Distributed task queue. Workers pick up jobs from Redis and run Python functions. |
| **OpenAI** | `text-embedding-3-small` for embeddings; `gpt-4o-mini` for tag generation fallback. |
| **trafilatura** | Article text extraction from HTML. |
| **BeautifulSoup** | HTML parsing for metadata and extension HTML cleanup. |

---

## 5. Data model

### `users`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `email` | String unique | Login identifier. |
| `username` | String unique | Public profile identifier (required). |
| `hashed_password` | String | bcrypt hash, never plain text. |
| `full_name` | String | Optional display name. |
| `is_active` | Boolean | False = account disabled. |
| `reading_patterns` | JSONB | Rolling stats updated on article completion: `avg_reading_time`, `readings` (last 20), `preferred_tags`. Used by `/content/recommended`. |

### `content_items`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK → users | Ownership. Never cross-user. |
| `original_url` | Text | The URL as submitted, stored in normalized form: scheme+host lowercased, trailing slash stripped, fragment dropped, known tracking query params removed, and remaining query params sorted. Partial unique index `uq_content_items_user_url_active` on `(user_id, original_url) WHERE deleted_at IS NULL` prevents duplicate active entries per user after this normalization. |
| `submitted_via` | String | `'web'`, `'extension'`, `'api'`, `'email'`. |
| `title`, `description`, `author` | Text | Extracted by Celery or provided by extension. |
| `thumbnail_url` | Text | OG image or PDF figure. |
| `content_type` | String | `'article'`, `'pdf'`, `'video'`, `'tweet'`, `'unknown'`. |
| `summary` | Text | AI-generated summary (Celery task). |
| `tags` | `ARRAY(String)` | User-confirmed tags. |
| `auto_tags` | `ARRAY(String)` | AI-suggested tags (pending user accept/dismiss). |
| `published_date` | DateTime | From OG metadata. |
| `full_text` | Text | Full HTML from trafilatura or pre-extracted HTML. **Excluded from list responses** (`ContentItemResponse`). Only returned by `GET /content/{id}/full` (`ContentItemDetail`). |
| `word_count` | Integer | Computed from `full_text`. |
| `reading_time_minutes` | Integer | `max(1, round(word_count / 200))`. |
| `read_position` | Float (0.0–1.0) | Scroll progress. Auto-marks read at ≥ 0.9. |
| `embedding` | `Vector(1536)` | OpenAI embedding. Null until Celery runs. |
| `is_read`, `is_archived` | Boolean | Status flags. |
| `read_at` | DateTime | Set when `is_read` becomes true. |
| `processing_status` | String | `'pending'`, `'processing'`, `'completed'`, `'failed'`. |
| `processing_error` | Text | Error message if extraction failed. 401/403-style failures are normalized as source-site access issues (authorization/anti-bot), not parser failures. |
| `deleted_at` | DateTime | Soft delete. Null = active. Indexed. |

**Reading status helper (`compute_reading_status`):**
Returns `'archived'`, `'read'`, `'in_progress'`, or `'unread'`.
`'read'` triggers if `is_read=True` OR `read_position >= 0.9`.

### `highlights`

User text selections within a content item.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `content_item_id` | UUID FK → content_items | CASCADE delete. |
| `user_id` | UUID FK → users | |
| `text` | Text | The selected text. |
| `note` | Text | Optional user annotation. |
| `start_offset`, `end_offset` | Integer | Character position in full_text. |
| `color` | String | `'yellow'`, `'green'`, etc. |
| `embedding` | `Vector(1536)` | Populated by Celery for connection search. |
| `search_vector` | `TSVECTOR` | Full-text index over `text` + `note`. Maintained by trigger (dual english+simple dictionary). Used by highlight search in `/search/semantic`. |
| `created_at` | DateTime | |

Highlight connections are discovered via the `/search/connections/{highlight_id}`
endpoint (cosine similarity across all of a user's highlights).

### `content_chunks`

Per-article passage chunks for multi-vector semantic search. Each article is split
into structure-aware chunks (~350 tokens each) and embedded individually.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `content_item_id` | UUID FK → content_items | CASCADE delete. |
| `user_id` | UUID FK → users | CASCADE delete. |
| `chunk_index` | Integer | 0-based position within the article. |
| `text` | Text | Plain text of the chunk (no HTML, no contextual prefix). |
| `embedding` | `Vector(1536)` | Embedding of the contextual-prefixed chunk text. |
| `created_at` | DateTime | |

Populated by the `generate_chunk_embeddings` Celery task after `generate_embedding`
completes. Old chunks are deleted and replaced on each run (idempotent). Articles
without chunks (ingested before chunking was deployed) fall back to the single
item-level embedding at search time.

### `lists`

User-defined collections of content items.

| Column | Type | Notes |
|--------|------|-------|
| `owner_id` | UUID FK → users | |
| `name`, `description` | Text | |
| `is_shared` | Boolean | Public share link (future). |
| `deleted_at` | DateTime | Soft delete. |

### `content_list_membership`

Join table (many-to-many: content ↔ lists).

| Column | Notes |
|--------|-------|
| `content_item_id`, `list_id` | Composite key. |
| `added_at` | Timestamp of addition. |
| `added_by` | User ID who added it. |

### `vinyl_records`

See [§17 Crates](#17-crates--vinyl-record-collection).

---

## 6. Authentication

### Registration

1. Client POSTs `{email, password, username, full_name}` to `/auth/register`.
2. Backend checks email uniqueness (400 if taken) and username uniqueness (400 if taken).
3. Password is hashed with bcrypt.
4. User row is inserted.
5. A `VerificationToken` (type `email_verification`, TTL 24 h) is created and a
   Celery task (`send_verification_email_task`) fires to deliver it via Resend.
6. **Onboarding content is seeded:** a "Getting Started with sed.i" guide article
   (`processing_status='completed'`) is created for the user, with a demo highlight
   and a demo vinyl record entry. This runs synchronously in the register handler.
7. Response: User object (201).

### Email service — Resend

All transactional emails (verification, password reset) are sent through the
[Resend](https://resend.com) HTTP API (`POST https://api.resend.com/emails`).

| Config var | Purpose |
|---|---|
| `RESEND_API_KEY` | Resend API key (required in production). |
| `EMAILS_FROM_EMAIL` | Sender address (must be a verified domain in Resend). |
| `EMAILS_FROM_NAME` | Display name for the sender. |
| `FRONTEND_URL` | Base URL inserted into verification/reset links. |

If `RESEND_API_KEY` is empty the helper logs a warning and skips the HTTP call
(useful for local dev without email credentials).

Implementation: `app/core/email.py` — `_send_email`, `send_verification_email`,
`send_password_reset_email`. Celery wrappers live in `app/tasks/email.py`.

### Analytics — PostHog

[PostHog](https://posthog.com) is used for product analytics: event tracking,
user identification, and session recording.

**Backend (Python SDK)**

- Initialised in the FastAPI `lifespan` handler (`app/main.py`).
  If `POSTHOG_API_KEY` is not set, `posthog.disabled = True` so nothing is sent.
- Server-side events captured in `app/api/auth.py`:
  - `user_signed_up` — on successful registration (includes `email`, `username`)
  - `user_logged_in` — on successful login
  - `account_deleted` — just before account removal

**Frontend (posthog-js)**

- `frontend/lib/posthog.ts` — initialises the SDK once on first client-side
  render (`initPostHog()`).  Guard: skips if `NEXT_PUBLIC_POSTHOG_KEY` is absent.
- `frontend/components/PostHogIdentify.tsx` — mounted inside `AuthProvider`;
  calls `posthog.identify(userId, {email, username})` when the user is logged in,
  and `posthog.reset()` on logout.
- Autocapture, pageview, and pageleave events are enabled by default.

**Environment variables required**

| Env var | Where | Purpose |
|---|---|---|
| `POSTHOG_API_KEY` | Backend `.env` | PostHog project API key |
| `POSTHOG_HOST` | Backend `.env` | PostHog ingest host (default `https://us.i.posthog.com`) |
| `NEXT_PUBLIC_POSTHOG_KEY` | Frontend `.env.local` | PostHog project API key (public) |
| `NEXT_PUBLIC_POSTHOG_HOST` | Frontend `.env.local` | PostHog ingest host |

### Login

1. Client POSTs `{username=email, password}` to `/auth/login` (OAuth2PasswordRequestForm).
2. Backend looks up user, verifies bcrypt hash.
3. Generates JWT with `sub=email`, `exp=now+ACCESS_TOKEN_EXPIRE_MINUTES`.
4. Response: `{access_token, token_type}`.

### Authenticated requests

- Frontend stores token in `localStorage` under key `token`.
- Every API call adds `Authorization: Bearer <token>`.
- `get_current_user` dependency in `app/core/deps.py` decodes the token, checks
  signature + expiry, loads user from DB. Returns 401 on any failure.
- `get_current_active_user` additionally checks `user.is_active`.

**Security note:** `localStorage` is XSS-accessible. Production hardening:
use httpOnly cookies + CSRF protection + strict CSP.

---

## 7. API routes

All routes require `Authorization: Bearer <token>` unless noted.

### Auth — `/auth`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Create user + seed onboarding content. |
| POST | `/auth/login` | Return JWT token. |
| GET | `/auth/me` | Current user info. |
| PUT | `/auth/me` | Update profile (username, visibility toggles). |
| DELETE | `/auth/me` | Delete account. Requires `{password}` body. Cascades all user data. |

### Content — `/content`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/content` | Save URL. Immediately returns 201. Queues extraction. Rate-limited to 20 req/min per user. Returns 409 with `detail: JSON.stringify({message, existing_id, is_archived})` if an active (non-deleted) item with the same URL already exists. Accepts optional `initial_highlights` array — highlight rows are created atomically with the content item in one transaction (used by ephemeral reader save). |
| GET | `/content` | List items. Filters: `is_read`, `is_archived`, `tag`. Pagination: `skip`, `limit`. |
| GET | `/content/tags` | All unique tags for current user with occurrence counts. |
| GET | `/content/recommended` | Scored unread items. Params: `mood` (`quick_read`, `deep_dive`, `light`). |
| GET | `/content/{id}` | Single item (no full text). |
| GET | `/content/{id}/full` | Single item with `full_text` (reader view). |
| PATCH | `/content/{id}` | Update: `is_read`, `is_archived`, `read_position`, `tags`, `full_text`. |
| DELETE | `/content/{id}` | Soft delete (sets `deleted_at`). Returns 204. |
| POST | `/content/{id}/summary` | Trigger AI summary generation (async, returns 202). |
| POST | `/content/{id}/tags/accept` | Copy `auto_tags` → `tags`. |
| POST | `/content/{id}/tags/dismiss` | Clear `auto_tags`, keep user `tags`. |

`ContentItemResponse` now includes both `processing_status` and `processing_error`
for list and single-item routes so the frontend can distinguish source-site
access failures (401/403/paywall/bot blocks) from parser/network issues.

**Extension path** (`pre_extracted_html`): When the body includes
`pre_extracted_html`, the backend skips trafilatura. It calls
`_clean_extension_html()` to strip title H1 / description P / thumbnail IMG
that would duplicate the card metadata, sets `processing_status='completed'`,
and still calls `extract_metadata.delay()` for any missing fields + embedding.

`pre_extracted_access_restricted: bool` — Optional field the extension sends
when `detectAccessRestriction()` (content script) detects a paywall via JSON-LD
`isAccessibleForFree: false`, `content_tier` meta, or DOM paywall selectors.
When `true`, the backend sets `processing_error` immediately (before `extract_metadata`
runs) so the item is flagged as limited without waiting for the Celery task.

**Route ordering note:** literal paths (`/tags`, `/recommended`) are registered
**before** `/{item_id}` in the router to prevent FastAPI parsing them as UUIDs.

### Lists — `/lists`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/lists` | Create list. |
| GET | `/lists` | All lists with content counts. |
| GET | `/lists/{id}` | List details. |
| PATCH | `/lists/{id}` | Update name/description/share. |
| DELETE | `/lists/{id}` | Soft delete list. |
| GET | `/lists/{id}/content` | Items in list (excludes soft-deleted items). |
| POST | `/lists/{id}/content` | Add content item to list. |
| DELETE | `/lists/{id}/content` | Remove content item from list. |

### Search — `/search`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/search/semantic?query=...` | Hybrid search. Returns `{ articles: [...], highlights: [...] }`. Articles: classifies query and routes to keyword, filter, semantic, or RRF-fused path. Highlights: tsvector keyword search over `highlights.search_vector`. Supports `mode=full`, `offset`, `after`/`before` date operators. |
| GET | `/search/connections/{highlight_id}` | Highlights in other articles similar to this highlight. Threshold param (default 0.75). |
| GET | `/search/connections/article/{content_id}` | All cross-article connections for highlights in the given article, grouped by connected article. |
| GET | `/search/{item_id}/similar` | Articles in user's queue similar to the given item. |

**Route ordering note:** `/semantic`, `/connections/...` must come **before**
`/{item_id}/similar` in the router (same reason as content routes).

**pgvector raw SQL:** All similarity queries use `text(...)` with
`CAST(:emb AS vector)` instead of the SQLAlchemy ORM operator. This is because
`op("<=>")` on a value doesn't carry pgvector type info and would fail.
The embedding is serialized as `"[0.1, 0.2, ...]"` (PostgreSQL vector literal).

**Similarity formula:** `1 - (embedding <=> CAST(:q AS vector)) / 2`

### MCP OAuth + Transport

| Method | Path | Description |
|--------|------|-------------|
| GET | `/.well-known/oauth-authorization-server` | OAuth discovery metadata for MCP clients. |
| GET | `/mcp/authorize` | OAuth authorize UI (PKCE). Validates `client_id` + exact `redirect_uri` allowlist match. |
| POST | `/mcp/authorize` | Credential check + auth code issuance in Redis (`mcp:code:*`, 5-min TTL). |
| POST | `/mcp/token` | Auth code + PKCE verifier exchange for sed.i JWT bearer token. |
| POST | `/mcp` | Streamable HTTP MCP endpoint (mounted ASGI app), JWT bearer auth required. |

**Security constraints:**
- OAuth clients are loaded from `MCP_OAUTH_CLIENTS_JSON` and treated as strict allowlists (`client_id -> [redirect_uri...]`).
- Unknown `client_id` or non-matching `redirect_uri` are rejected with `400`.
- OAuth login HTML escapes reflected parameters (`client_id`, `redirect_uri`, `state`, `code_challenge`) to prevent reflected XSS.
Maps cosine distance [0, 2] → similarity score [1, 0].

### Highlights — nested under `/content/{content_id}/highlights`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/content/{id}/highlights` | Create highlight (text, offsets, color, optional note). |
| GET | `/content/{id}/highlights` | Get all highlights, ordered by `start_offset`. |
| PATCH | `/highlights/{highlight_id}` | Update note or color. |
| DELETE | `/highlights/{highlight_id}` | Hard delete highlight. |

### Analytics — `/analytics`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/stats` | Total items, read, unread (`.is_(False)` — see bug note), archived, total reading time. |

**Bug fixed:** The original code used Python `not ContentItem.is_read` which
always evaluates to `False` (Column objects are truthy). Replaced with
`ContentItem.is_read.is_(False)`.

### Vinyl — `/vinyl`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/vinyl` | List records. Filters: `status`. Sort: `created_at`, `year`, `artist`. |
| POST | `/vinyl` | Create record from Discogs URL. Queues `fetch_discogs_metadata`. |
| GET | `/vinyl/{id}` | Single record. |
| PATCH | `/vinyl/{id}` | Update notes, rating, tags, status, videos. |
| DELETE | `/vinyl/{id}` | Soft delete. |

---

## 8. Rate limiting

**Implementation:** `app/middleware/rate_limit.py` — `RateLimitMiddleware`.

- Applies to: `POST /content` only.
- Algorithm: sliding window. Each user ID gets a `deque` of request timestamps.
  On each request, timestamps older than the window are popped from the left.
  If `len(deque) < max_requests`, request is allowed and timestamp is appended.
- Limit: **10 requests / 60 seconds** AND **50 requests / 3600 seconds** per user
  (identified by JWT user ID; falls back to client IP if auth not on request state).
- Response on exceeded: HTTP 429 with `{detail: "Too many requests. Please try again later."}`,
  CORS headers from `ALLOWED_ORIGINS`, and a `Retry-After` header (seconds).
- **Known limitation:** State is in-memory per process. Does not work correctly
  across multiple FastAPI instances. Production fix: move to Redis.
- **Test isolation:** Tests must call `rate_limiter.requests.clear()` between
  tests that POST to `/content`.

---

## 9. Background processing pipeline

### Flow for a normal URL save

```
POST /content (201 returned)
  └─> extract_metadata.delay(item_id)          # Stage 1
        ├─> fetch URL (requests)
        ├─> parse OG/Twitter tags (BS4)
        ├─> update: title, description, thumbnail, author, published_date
        ├─> detect content_type
        ├─> run trafilatura → structured XML → HTML → full_text
        ├─> compute word_count, reading_time_minutes
        ├─> set processing_status = 'completed'
        └─> chain:
              ├─> generate_embedding.delay(item_id)   # Stage 2
              │     ├─> combine title+description+full_text
              │     ├─> call OpenAI text-embedding-3-small
              │     └─> store Vector(1536)
              └─> generate_tags.delay(item_id)        # Stage 3 (after embedding)
```

### Flow for extension save (`pre_extracted_html`)

```
POST /content (201, processing_status='completed' immediately)
  ├─> _clean_extension_html() called inline
  ├─> extract_metadata.delay(item_id)   # fills any missing metadata fields
  └─> generate_embedding.delay(item_id)  # separate task (not chained via extract_metadata)
```

### Celery tasks

| Task | File | Description |
|------|------|-------------|
| `extract_metadata` | `tasks/extraction.py` | Full pipeline: fetch → parse → trafilatura. For article URLs, limited extraction is flagged via source-restriction/truncation heuristics (paywall/access markers, schema/content-tier signals, teaser-description overlap, media/caption-only extraction, and low extraction coverage), not a raw short-text threshold. Thumbnail extraction uses a fallback chain: OG/Twitter meta → JSON-LD image → `link[rel=image_src]` → first usable in-content image. |
| `generate_embedding` | `tasks/embedding.py` | OpenAI embedding, stored in pgvector. Also embeds highlights. |
| `generate_tags` | `tasks/tagging.py` | Hybrid two-pass. Pass 1 (pgvector similarity, free): if ≥2 high-confidence tag matches found, auto-accepts into both `auto_tags` and `tags`. Pass 2 (`gpt-4o-mini`): if pass 1 misses, calls LLM and also auto-accepts. `auto_tags`/`tags` endpoints exist for manual override. |
| `generate_summary` | `tasks/summarization.py` | Triggered by `POST /content/{id}/summary`. Calls OpenAI to produce a summary. |
| `fetch_discogs_metadata` | `tasks/discogs.py` | Fetches vinyl metadata from Discogs API. |
| `cleanup` | `tasks/cleanup.py` | Periodic task (beat). Removes old data / temp files. |

---

## 10. PDF extraction

If a saved URL returns `Content-Type: application/pdf`:

1. Backend downloads the file.
2. `_process_pdf()` in `tasks/extraction.py` runs YOLO-based layout analysis.
3. Output converted to structured HTML.

**Post-processing in `_process_pdf`:**

| Step | Action |
|------|--------|
| Title | First `<h1>` or `<h2>` in body → stored as `item.title`. |
| Author removal | Short `<p>` blocks (< 300 chars) between title and abstract heading removed (author/affiliation lines in arXiv papers). |
| Abstract (arXiv style) | Standalone `<h1>ABSTRACT</h1>` + following `<p>` → stored as `item.description`. |
| Abstract (journal/ACS style) | Inline `<p>ABSTRACT: text...</p>` → stored as `item.description`. |
| Thumbnail | First `<div class="figure-block"><img src="data:..."/>` → stored as `item.thumbnail_url`, removed from body. |
| Confidence badge | Injected as `<meta>` tag; parsed by the Reader with a regex that handles both BS4 attribute orderings. |

---

## 11. Auto-tagging pipeline (detail)

**Goal:** Suggest relevant tags without spending money on every article.

**Two-pass strategy in `generate_tags`:**

1. **Free pass (pgvector):** Find user's already-tagged content with cosine distance
   < 0.25. If ≥2 tags appear across ≥2 similar articles (`should_accept_tags`),
   auto-write to both `auto_tags` **and** `tags` immediately.
2. **LLM pass (gpt-4o-mini):** If pass 1 finds nothing, call `gpt-4o-mini` with
   the article title + description + first **800 words** of plain text.
   Parse JSON array from response; also auto-writes to both `auto_tags` and `tags`.

**Both passes auto-accept immediately** — when tagging succeeds the item is
considered done. The `/tags/accept` and `/tags/dismiss` endpoints exist for
manual correction after the fact (UI can show suggested tags and let user override).

---

## 12. Hybrid search and connections

### Content search

`GET /search/semantic?query=...` — unified hybrid search entry point. The query
is classified by `app/core/search_router.py` and dispatched to the cheapest path:

| Query type | Path | Example |
|---|---|---|
| Operator syntax | SQL filter | `author:Paul Graham`, `tag:music`, `after:2025-01-01` |
| Short keyword (≤4 words) | tsvector full-text | `llm`, `react hooks` |
| Natural language / question | pgvector semantic | `how does attention work?` |
| Longer conceptual phrase | keyword + semantic fused with RRF | `building products with AI` |

**`mode=full`** (used by the SearchModal) bypasses classification and always runs
all three engines, fusing results with three-way Reciprocal Rank Fusion.

**Date filtering** — `after:YYYY-MM-DD` / `before:YYYY-MM-DD` operators are
extracted from the query before routing. Filter path applies them in SQL;
keyword/semantic paths receive the stripped query and results are post-filtered
by `created_at` in Python.

**tsvector index** — `search_vector` column maintained by a PostgreSQL trigger
using dual-dictionary (english + simple) so stemmed words AND acronyms both match.
Prefix matching (`llm:*`) catches plurals.

**Chunk-level semantic search** — articles are split into ~350-token structure-aware
chunks stored in `content_chunks`. At query time, an article's score is
`MAX(cosine_similarity)` across all its chunks — so a 20-section article surfaces
if *any* section matches, not just if the averaged embedding matches. Articles
without chunks (pre-deployment) fall back to the single item-level embedding via
a `UNION ALL` CTE. See `docs/chunking-and-search.md` for the full architecture.

**Highlight search** — every query to `/search/semantic` also runs a tsvector
keyword search over `highlights.search_vector` (text + note) and returns matching
highlights alongside articles. Results include `article_title` so the frontend can
link to the parent article.

**Embedding cache** — `app/core/embedding_cache.py` caches query embeddings in
Redis (`qemb:{sha256[:16]}`, 1hr TTL) to avoid redundant OpenAI calls.

**Untitled items excluded** — content with no title (failed extraction) is
excluded from all search paths.

`GET /search/{item_id}/similar` — uses an existing item's embedding as the query
vector for cosine similarity search.

### Highlight connections

Each Highlight gets an embedding (via `generate_embedding` task).

`GET /search/connections/{highlight_id}` — finds highlights in **other** articles
(same user) whose embedding is within a similarity threshold (default 0.75).
This powers the "Connections" tab in the reader: showing how ideas in the current
article link to ideas captured elsewhere in your queue.

`GET /search/connections/article/{content_id}` — aggregates all connections for
all highlights in an article, grouped by the connected article and sorted by
total similarity score.

---

## 13. Recommendation engine

`GET /content/recommended` — no ML required.

Scoring per unread item (max 75 points):

| Factor | Max points | Logic |
|--------|-----------|-------|
| Embedding similarity to recent reads | 30 | Cosine similarity to last 7 days' read articles. Takes max similarity. |
| Reading time match | 15 | Penalizes items far from `user.reading_patterns.avg_reading_time`. |
| Recency | 20 | Linear decay: `max(0, 20 - days_old / 10)`. Decays to 0 at 200 days. |
| Tag overlap | 10/overlap | +10 per tag that matches `user.reading_patterns.preferred_tags`. |

Optional `mood` filter:
- `quick_read` — skips articles > 10 min.
- `deep_dive` — skips articles < 10 min.
- `light` — skips articles > 5000 words.

`reading_patterns` on `User` is updated whenever an article is marked as read
(manually or auto at scroll position ≥ 0.9).

---

## 14. Frontend structure

### Pages (`frontend/app/`)

| Route | File | Description |
|-------|------|-------------|
| `/` | `page.tsx` | Landing / marketing page. |
| `/dashboard` | `dashboard/page.tsx` | Main queue. Add form, filters, content cards. |
| `/content/[id]` | `content/[id]/page.tsx` | Reader view. Fetches via `GET /content/{id}/full` (detail shape with `full_text`). Caches in `sessionStorage` only when `full_text` is present to prevent blank reader on PATCH-induced cache overwrite. |
| `/read` | `read/page.tsx` | Ephemeral reader. Loads article from extension relay (`chrome.storage.session` via `getEphemeralArticle` message) or `sessionStorage` fallback. Renders `EphemeralReader` with "Save to Library" banner. No article in storage → "No article to read" empty state. |
| `/lists` | `lists/page.tsx` | List management. |
| `/lists/[id]` | `lists/[id]/page.tsx` | Items inside a list. |
| `/crates` | `crates/` | Vinyl record collection (feature-flagged). |
| `/guide` | `guide/` | Static user guide page. |
| `/mockups/failed-ingest` | `mockups/failed-ingest/page.tsx` | Design exploration page with three visual directions for failed-ingestion cards. |
| `/login`, `/register` | auth pages | |
| `/settings` | `settings/` | User preferences. Reading settings + feature visibility toggles (Connections, Crates/audio player) with live preview. |
| `/[username]` | `[username]/PublicProfileClient.tsx` | Public profile. Standard Navbar, same ContentItem/index layout as dashboard, all actions hidden (`readOnly`). List/index toggle persisted in localStorage. Identity breadcrumb `@username's queue`. |
| `/[username]/content/[id]` | `[username]/content/[id]/page.tsx` | Public reader. Uses `publicAPI.getPublicContentItem()`. Guest limit: 3 reads per profile owner tracked in localStorage; shows signup prompt after limit. |

### Key components

| Component | Location | Role |
|-----------|----------|------|
| `AddContentForm` | dashboard | Collects URL, submits to API. |
| `ContentList` | dashboard | Fetches items, client-side filter, list/index view toggle. Sort field/dir persisted in localStorage. RetroLoader on all loading states. Active sort header highlighted in accent color (no glyph). |
| `ContentItem` | dashboard / public profile | Card view. Accepts `readOnly?: boolean` — hides all action buttons (read, archive, delete, tag, list) when true. Accepts `navigateTo?: string` to override default `/content/:id` link. Uses `getIngestIssue(...)` mapping to show clearer ingest failure badges (blocked/auth/network/partial) instead of a single generic extraction failure label. Failed-ingest items only use the compact single-line row (status badge + source domain + date + right-aligned delete) when extraction fails before meaningful metadata is available; failed items with usable metadata keep the standard full card layout. |
| `ContentIndexItem` | dashboard / public profile | Index row. Responsive layout layout: Desktop shows Date \| Title \| Author/Source \| hover menu. Mobile collapses to just Date \| Title to preserve space. Hovering reveals absolute-positioned multi-action tools (Read, Archive, Delete). Delete uses click→"Delete?"→click confirm. Accepts `readOnly` and `navigateTo` props. |
| `Reader` | content/[id], read/ | Shell: fixed navbar, reading progress bar, optional NowPlaying, HighlightsPanel + optional ConnectionsPanel sidebars, TOC sidebar, KeyboardShortcuts. Renders `<ReaderArticle>`. Accepts `onHighlightCreate` override prop — when provided, highlight creation calls the callback instead of the API (used by `EphemeralReader` to collect local highlights before save). |
| `EphemeralReader` | read/ | Wraps `Reader` with a sticky "Reading without saving" / "Save to Library" banner. Collects highlights locally via `onHighlightCreate` ref. On save: calls `contentAPI.create` with `initial_highlights`, clears sessionStorage, navigates to `/content/{id}`. |
| `ReaderArticle` | content/[id], lists/[id] | Reusable article body. Handles highlights, selection toolbar, summary, metadata editing, similar articles, ImageZoomModal, scroll position save/restore. `embedded` prop switches from window scroll to container scroll (used in split-pane list view). `focusModeEnabled` prop controlled by Reader's navbar. Exposes `highlights`, `refreshHighlights`, `scrollToHighlight` via `forwardRef`/`useImperativeHandle` for Reader's sidepanels. Uses `getIngestIssue(...)` fallback copy when full text is unavailable, including source-blocked and partial-extraction states. |
| `InlineError` | shared | Inline contextual error with optional dismiss/retry. See §16. |
| `EmptyState` | shared | Empty data state with optional CTA. Variants: `inline`, `bordered`. See §16. |
| `Sidebar` | layout | List navigation with counts. |
| `CratesClient` | crates | Grid of records, search/sort/filter, Now Digging bar. |
| `RecordDetail` | crates | Gatefold panel: art + metadata + tracklist + videos. |
| `ListeningMode` | crates | Full-screen music player overlay. `z-[80]` > RecordDetail `z-50`. |
| `VinylCard` | crates | Individual record card. |
| `YouTubePlayer` | crates | Invisible div hosting YouTube IFrame API. Plays queue sequentially. |
| `Navbar` | layout | Mini-player on mobile (hidden when Crates/audio feature is disabled). `mounted` guard avoids SSR hydration mismatch. Supports writing-mode controls (`Export`, `Close`) and fullscreen-aware auto-hide behavior (listens to editor scroll container instead of window). |
| `ProfileSettings` | settings | Public profile toggles (is_public, is_queue_public, is_crates_public), username, full name. Inline "Saved." state replaces alert(). Preview link to `/{username}` shown when public. |

### React Contexts

| Context | State |
|---------|-------|
| `AuthContext` | Current user, login/logout. Token stored in `localStorage['token']`. |
| `ListsContext` | List counts shown in sidebar. |
| `ToastContext` | Legacy — replaced by inline `InlineError` feedback. Context still exists but is unused. |
| `PlayerContext` | Vinyl player: `QueueTrack[]`, `currentIndex`, play/pause, `YT.Player` ref. Queue persisted in `localStorage['sedi-player']`. |
| `ReadingSettingsContext` | Reader preferences (`theme`, typography, bionic reading) plus local feature visibility toggles (`showConnections`, `showCrates`). Persisted in `localStorage['sedi-reading-settings']`. Initializes with `DEFAULTS` on server; loads saved values in `useLayoutEffect` after mount. Exposes `hydrated: boolean` — components that render settings-dependent UI (`PreviewBox`, `SettingsCarousel`, `SettingsPreview`) return `null` until `hydrated` to avoid an SSR flash of defaults. |

### Feature flags (`frontend/lib/flags.ts`)

Environment variable driven — all default to `true` unless explicitly disabled:

| Flag | Env var | Controls |
|------|---------|---------|
| `SHOW_FOR_YOU` | `NEXT_PUBLIC_SHOW_FOR_YOU` | "For You" / recommended tab on dashboard. |
| `SHOW_HIGHLIGHT_CONNECTIONS` | `NEXT_PUBLIC_SHOW_HIGHLIGHT_CONNECTIONS` | Connections panel in reader. |
| `SHOW_CRATES` | `NEXT_PUBLIC_SHOW_CRATES` | Crates/vinyl section in nav. |
| `SHOW_EDIT_ARTICLE` | `NEXT_PUBLIC_SHOW_EDIT_ARTICLE` | Edit article button in reader. Defaults to `false`. |

### Frontend utilities (`frontend/lib/`)

| File | Key exports | Purpose |
|------|------------|---------|
| `bionicReading.ts` | `toBionic`, `addHeadingAnchors`, `stripDocumentWrappers`, `sanitizeContentHtml` | Reader UX. Bionic reading bolds the first ~50% of each word. `addHeadingAnchors` generates deduped IDs for TOC links. `stripDocumentWrappers` strips `<html>/<body>` from PDF-extracted content. `sanitizeContentHtml` removes ephemeral UI before saving. |
| `blockParser.ts` | `parseHtmlToBlocks` | Converts HTML to `ContentBlock[]` for the block editor. Maps h1–h6 and p/ul/ol to block types. SSR guard included. |
| `flags.ts` | Feature flag booleans | See table above. |
| `ingestErrors.ts` | `getIngestIssue` | Classifies ingestion failures (`processing_status` + `processing_error`) into user-facing categories (`blocked`, `unauthorized`, `network`, `paywall_partial`, `partial`, `unknown`) used by queue cards and reader fallback messaging. |

---

## 15. Security

### Current protections

- Password hashing: bcrypt via passlib.
- JWT: signed with `SECRET_KEY`, expiry enforced.
- CORS: origin list driven by environment variable.
- Rate limiting: 20 POST /content per user per 60s.
- Cross-user isolation: every query filters by `user_id = current_user.id`.

### Known gaps / mitigations

| Risk | Current state | Fix |
|------|--------------|-----|
| XSS | Extracted HTML rendered directly in reader. | Sanitize HTML (DOMPurify) or use sandboxed iframes. |
| SSRF | Backend fetches user-provided URLs. | Validate URLs; block internal IPs (169.254.x.x, 10.x.x.x, etc.). |
| Rate limit in-memory | Resets on restart; no cross-instance enforcement. | Move to Redis. |
| localStorage token | XSS-accessible. | Migrate to httpOnly cookies + CSRF tokens. |

---

## 16. Error handling and reliability

### Backend error shape

All error responses use `{detail: string}` — a single, consistent shape.

| Code | When |
|------|------|
| 400 | Bad request (duplicate email, invalid input). |
| 401 | Missing or invalid JWT. |
| 403 | Inactive user or forbidden access. |
| 404 | Item not found or soft-deleted (or belongs to another user). |
| 422 | Validation error (simplified field messages from `RequestValidationError`). |
| 429 | Rate limit exceeded on POST /content. Includes `Retry-After` header. |
| 500 | Unhandled error (sanitized via global exception handler — no internal details leaked). |

**Global exception handlers** (registered in `app/main.py`):

- `RequestValidationError` → 422 with simplified field messages.
- `SQLAlchemyError` → 500 with generic "database error" detail, logged server-side.
- `Exception` → 500 with generic "unexpected error" detail, logged with traceback.

### Background jobs

- `max_retries=3` on extraction tasks.
- Saves `processing_error` if extraction fails after retries.
- `processing_status` set to `"failed"` (not `"completed"`) on error.
- `processing_status` surfaces to frontend — UI shows processing items at reduced opacity.

### Frontend error conventions

**No toasts.** All error feedback is inline and contextual — near the action that failed.

**Shared components:**

| Component | File | Usage |
|-----------|------|-------|
| `InlineError` | `components/InlineError.tsx` | Left red border, muted bg, concise text. Props: `message`, optional `onDismiss`, optional `onRetry`, optional `className`. Used everywhere an action can fail. |
| `EmptyState` | `components/EmptyState.tsx` | Muted centered text. Props: `message`, optional `description`, optional `actionLabel`/`onAction`, `variant` (`"inline"` or `"bordered"`). Used for all empty data states. |

**Error message tone:** Use "Couldn't [action]. Try again." pattern — concise, no jargon, action-oriented. Never "Failed to..." or "Error: ...".

**Empty state tone:** Sentence case, no emoji, no UPPERCASE. Optional CTA where the user can take action.

**State rendering:** Loading, Error, Empty, and Data states are mutually exclusive. Use the order: loading > error > empty > data.

**Optimistic updates:** Update UI immediately, revert on API failure, show `InlineError` near the action.

**`fetchWithAuth`** (`lib/api.ts`): Central API helper. Parses `{detail}` from backend responses. Rate limit reads `Retry-After` header. All API methods (including deletes) route through it.

---

## 17. Crates — Vinyl Record Collection

**What it is:** Section of the app (`/crates`, feature-flagged by `SHOW_CRATES`)
for managing a vinyl record collection. Users paste a Discogs URL; the system
fetches metadata via the Discogs API in a background Celery task.

### `vinyl_records` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK → users | CASCADE delete. |
| `discogs_url` | Text | Source URL. |
| `discogs_release_id` | Integer | Parsed from URL for API call. |
| `title` | Text | Album title. |
| `artist` | Text | Primary artist. |
| `label` | String(255) | Record label. |
| `catalog_number` | String(255) | Label catalog number (e.g. `CAT-001`). |
| `year` | Integer | Release year. |
| `cover_url` | Text | Album art URL. |
| `genres`, `styles` | `ARRAY(String)` | From Discogs. |
| `tracklist` | JSONB | `[{position, title, duration}, ...]`. |
| `videos` | JSONB | `[{title, uri, duration}, ...]` — Discogs + user-added YouTube links. |
| `notes` | Text | Personal notes. |
| `rating` | Integer | 1–5 star rating. |
| `tags` | `ARRAY(String)` | User tags. |
| `status` | String | `'collection'`, `'wantlist'`, `'library'`. Default `'collection'`. |
| `processing_status` | String | `'pending'` → `'completed'`/`'failed'`. |
| `deleted_at` | DateTime | Soft delete. |

**Common mistake:** The old doc said `wantlist (bool)` — that field does not
exist. Use `status='wantlist'` instead.

### Frontend components

| Component | Role |
|-----------|------|
| `CratesClient` | Main page: grid of records, search/sort/filter, Now Digging bar, ListeningMode toggle. |
| `RecordDetail` | Gatefold panel: cover art left, metadata + tracklist + video links right. |
| `ListeningMode` | Full-screen overlay. `z-[80]` covers RecordDetail (`z-50`). |
| `VinylCard` | Individual record in the grid. |

### Music playback

- `PlayerContext` holds the queue (`QueueTrack[]`), `currentIndex`, play/pause, `YT.Player` ref.
- `YouTubePlayer` — invisible div hosting YouTube IFrame API. Plays queue sequentially.
- Queue persists to `localStorage['sedi-player']`.
- Navbar mini-player: album art + play/pause on mobile, guarded by `mounted` to
  avoid SSR hydration mismatch.

### UX patterns

- "Now digging" bar shows last-opened record; changes to "Now listening" when
  music is actively playing.
- `lastDug` → actual key is `"now-digging"` stored in `localStorage` for
  cross-page-load persistence.

---

## 18. PDF Extraction

Covered in §10.

---

## 19. Public Profiles

**What it is:** Users can have a public profile accessible via `/[username]`. Content pieces, crates, and lists can be selectively marked as public. Unauthenticated users can view public content, but cannot edit or add to it.

### Data Model Changes
- `User.username` (String, unique) — Claimed at registration, identifies the profile.
- `User.is_public` (Boolean) — Toggles profile visibility.
- `ContentItem.is_public` (Boolean) — Visibility toggle.
- `VinylRecord.is_public` (Boolean) — Visibility toggle.
- `List.is_public` (Boolean) — Visibility toggle.

### API Routes — `/public`
These routes do not require authentication (`@router.get` without the `Depends(get_current_active_user)` guard). Implemented in `content-queue-backend/app/api/endpoints/public.py`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/public/u/{username}` | Profile details. 404 if not found, 403 if `is_public=False`. |
| GET | `/public/u/{username}/content` | Paginated public queue items (only `is_public=True`, non-archived). Only succeeds if `user.is_queue_public=True`. |
| GET | `/public/u/{username}/content/{item_id}` | Single public content item for the public reader. |
| GET | `/public/u/{username}/vinyl` | Public vinyl records. Only succeeds if `user.is_crates_public=True`. |

### Frontend UI
- `app/[username]/PublicProfileClient.tsx` — Uses standard `Navbar`. Loads profile + queue + vinyl in parallel via `Promise.allSettled`. Tabs (Queue/Crates) only shown for whichever sections are publicly enabled. Content rendered with `readOnly={true}` via `ContentItem` / `ContentIndexItem`. Navigation links go to `/[username]/content/[id]`, not `/content/[id]`.
- `app/[username]/content/[id]/page.tsx` — Public reader page. Calls `publicAPI.getPublicContentItem()`. Guest limit: reads tracked in `localStorage['publicReadsCount']` / `['publicReadsOwner']`; after 3 reads from the same profile, shows a signup prompt instead of article content.

---

## 20. Testing

### Backend — pytest

Location: `content-queue-backend/tests/`

| File | Coverage |
|------|----------|
| `conftest.py` | Postgres test DB fixtures, test client, user + auth header fixtures. Uses `NullPool`, `TRUNCATE TABLE` for isolation. |
| `test_auth_api.py` | Register, login, `/me`, duplicate email, wrong password, JWT tampering. |
| `test_analytics_api.py` | Stats counts; regression for `not ContentItem.is_read` bug. |
| `test_content_api.py` | Core content CRUD. |
| `test_content_extended.py` | `_clean_extension_html` edge cases; extension path; cross-user isolation. |
| `test_vinyl_api.py` | Full vinyl CRUD; soft delete; cross-user 404; Celery mocked. |
| `test_rate_limiter.py` | Sliding window unit tests (no DB). |
| `test_lists_api.py` | List CRUD; membership management; cross-user isolation. |

Run: `cd content-queue-backend && poetry run pytest tests/`

**Important patterns:**
- All Celery tasks are mocked with `patch(...)` — no broker needed.
- Cross-user isolation: always test that user A cannot act on user B's data.
- Rate limiter tests call `rate_limiter.requests.clear()` before any POST /content test to avoid 429 from test ordering.

### Frontend — Jest

Location: `frontend/__tests__/`

| File | Coverage |
|------|----------|
| `bionicReading.test.ts` | `toBionic`, `addHeadingAnchors` (deduplication), `stripDocumentWrappers`, `sanitizeContentHtml`. |

Run: `cd frontend && npm test -- --watchAll=false`

---

## 20. Key design decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Sync vs async extraction | Async (Celery) | URL fetching is slow and failable; API must stay responsive. |
| JWT vs sessions | JWT | Stateless — any backend instance can verify. |
| localStorage vs cookies | localStorage | Simpler for now; known XSS risk, documented above. |
| pgvector vs vector DB | pgvector | Keeps everything in one DB; acceptable at current scale. |
| Polling vs websockets | Polling | Simple to implement; websockets add operational complexity. |
| Raw SQL for vector queries | `text(...)` | SQLAlchemy's `op("<=>")` doesn't carry pgvector type info. |
| Tagging: free-first | pgvector sim first, LLM fallback | Minimizes OpenAI cost for users with growing tagged libraries. |
| Soft delete everywhere | `deleted_at` column | Enables undo, audit, and safer queries. |
| Feature flags | Env vars, default-on | Lets incomplete features be shipped to prod and toggled without redeployment. |

---

## 21. Documentation rule

When making a commit that adds or changes a feature, update this file in the
same commit:

- New feature → add or update the relevant section.
- Bug fix to a documented component → update any affected description.
- New model column or API route → add to the appropriate table.

The goal: ARCHITECTURE.md always reflects the current codebase so any new
contributor or future AI session can get oriented quickly without reading
every file.

---

## 23. Chrome Extension (`extension/`)

MV3 extension — version `0.1.2`. Four components:

| File | Role |
|------|------|
| `content/content.js` | Injected on demand via `chrome.scripting.executeScript`. Runs Readability, extracts HTML/metadata, calls `detectAccessRestriction()` (JSON-LD `isAccessibleForFree: false`, `content_tier` meta, paywall DOM selectors). |
| `content/reader-overlay.js` | Ephemeral reader. Swaps `document.body` with a clean reader DOM (CSS hard-reset via `all:initial!important` to prevent page style leakage). Features: progress bar, auto-hide navbar, TOC with scroll-spy, focus mode, theme toggle, reading settings panel (font/size/line-height/letter-spacing/width), "Save to sed.i" button. Metadata byline mirrors `ReaderArticle.tsx` exactly: two-zone layout (attribution row + reader-info row), font-mono tracking-tight, three color levels (--fg2/--fgm/--fgt). Strips `iframe/frame/object/embed/script` from injected HTML before render. |
| `popup/popup.js` | Two-button popup: **Read** (launches reader-overlay) and **Save to sed.i** (extracts + sends to service worker). On open, shows page title + favicon/domain immediately, then runs an inline `executeScript` to read og/meta tags (`og:image`, `og:description`, `og:site_name`, `article:author`, `article:published_time`) — no content script needed, under 10ms. Save button shows animated dot loading (`sending` → `sending...`) then `sent ✓` in green. Parses structured 409 errors (nested JSON `detail`) into human-readable messages. Theme (`light`/`dark`) persisted via `chrome.storage.local`. |
| `background/service_worker.js` | Calls `POST /content` with `pre_extracted_html` payload. Maps `accessRestricted` → `pre_extracted_access_restricted`. Reads API base URL from `chrome.storage.local` (default: `https://api.read-sedi.com`). |

**Dev mode:** Long-press (≥2s) on the extension logo reveals an API URL field for pointing the extension at localhost. The URL is saved to `chrome.storage.local` and persists across sessions.

**Dev worker hot-reload:** `content-queue-backend/scripts/dev_worker.sh` starts Celery with `watchfiles` monitoring `app/` — worker auto-restarts on any Python file change, eliminating the need to manually restart after editing tasks.

---

## 22. Engineering workflow standard

The operational standard for this repository (local dev, CI, Railway deploy,
and coding-agent behavior) is documented in:

- `docs/engineering-workflow.md`
- `docs/product-quality-execution-plan.md` (phased UX/state/error consistency rollout plan)

Use that document as the source of truth for:

- Local startup commands and quality gates
- GitHub Actions expectations and branch protection checks
- Railway process model (web + worker) and release flow
- Coding-agent change, validation, and documentation requirements
