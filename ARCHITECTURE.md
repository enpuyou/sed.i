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
| GET | `/search/connections/{highlight_id}` | Per-highlight connections. Response: `{source_note, connections[]}` with article metadata, shared tags, and matched passages. |
| GET | `/search/connections/{highlight_id}/insight/{article_id}` | AI-generated one-sentence insight linking the highlight to a connected article. Cached in Redis (7 days). Returns `{insight: null}` on failure. |
| GET | `/search/connections/article/{content_id}` | All cross-article connections for highlights in the given article, grouped by connected article. |
| GET | `/search/connections/article/{content_id}/highlights` | All-highlights grouped view for Mode 2 panel. Returns `HighlightWithConnections[]` (highlights with ≥1 connection only). |
| GET | `/search/{item_id}/similar` | Articles in user's queue similar to the given item. |

**Route ordering note:** `/semantic`, `/connections/...` must come **before**
`/{item_id}/similar` in the router. Within connections routes: `/connections/article/{id}/highlights` before `/connections/article/{id}` (literal "highlights" before the broader path), and both before `/{highlight_id}`.

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
| `generate_tags` | `tasks/tagging.py` | LLM-only semantic extraction. Two-level prompt (DOMAIN + CONCEPTS), diverse domain examples. Writes to `tags`. Calls `upsert_tag_embeddings` after. |
| `upsert_tag_embeddings` | `tasks/tagging.py` | Embeds any new tag labels via `text-embedding-3-small` and writes to `tag_embeddings`. Idempotent — already-present labels are skipped. |
| `cluster_user_tags_task` | `tasks/clustering.py` | Runs cosine similarity + union-find on a user's tag embeddings, writes `reading_clusters`. Requires ≥10 tagged articles. |
| `cluster_all_users_task` | `tasks/clustering.py` | Weekly beat task — dispatches `cluster_user_tags_task` for every user. |
| `backfill_semantic_tags` | `tasks/tagging.py` | Re-tags articles with empty `tags`. Rate-limited to 50/min. |
| `generate_summary` | `tasks/summarization.py` | Triggered by `POST /content/{id}/summary`. Calls OpenAI to produce a summary. |
| `fetch_discogs_metadata` | `tasks/discogs.py` | Fetches vinyl metadata from Discogs API. |
| `cleanup` | `tasks/cleanup.py` | Periodic task (beat). Removes old data / temp files. |
| `consolidate_memory_task` | `tasks/memory.py` | Per-user: merges reading activity since `last_consolidated` onto the user's `user_profiles` row. First run (bootstrap) uses earliest actual activity up to 30 days back. Skipped if < 3 activity items. |
| `consolidate_all_users_task` | `tasks/memory.py` | Nightly beat fan-out — queries users with activity since their last consolidation and dispatches `consolidate_memory_task` for each. |

### Memory profile system

`user_profiles` stores a hybrid memory record per user:

| Column | Type | Notes |
| ------ | ---- | ----- |
| `current_focus` | Text | Specific sub-domain, updated by consolidation. Used by MCP synthesis tool for context injection. |
| `reading_velocity` | Enum | `fast` / `deep` / `browsing` — inferred from read% and highlight count, not topics. |
| `memory_text` | Text | Free-form prose (3–6 sentences), LLM-managed. Covers trajectory, depth asymmetry, behavioral pattern, backlog signal. |
| `last_consolidated` | Timestamp | Delta cutoff for next run. Null = bootstrap pending. |

**Activity format**: consolidation formats saved articles, read articles (with inline highlights, in chronological order), never-opened saves, reading lists, and topic clusters into a structured activity string. Relative timestamps (`2h ago`, `5d ago`) and reading order are included so the LLM can observe sequencing signals (burst-saving, curriculum progression, topic pivots).

**Bootstrap vs delta**: if `last_consolidated` is null, the task uses `_BOOTSTRAP_PROMPT` and finds the earliest actual activity (up to 30 days). Subsequent runs use `_DELTA_PROMPT` with `last_consolidated` as the since cutoff. The delta prompt instructs the model to patch/merge rather than rewrite.

**Prompt selection (eval-validated)**: the bootstrap prompt uses an explicit four-dimension checklist (trajectory, depth asymmetry, behavioral pattern, backlog signal) plus a specificity rule. Evaluated against two alternatives (A: old single prompt, C: briefing framing) on 10 synthetic cases — current prompt (B) scores 0.860 weighted vs. 0.621 for the baseline. See `evals/memory-consolidation-prompt/results/report.md`.

**API**: `GET /memory/profile` returns current profile. `POST /memory/consolidate` queues `consolidate_memory_task` for the current user (202 async).

---

## 10. PDF extraction

If a saved URL returns `Content-Type: application/pdf`, it is routed to `_process_pdf()` in `tasks/extraction.py`, which calls `extract_with_yolo()`. Full pipeline detail: `docs/design/systems/pdf-extraction.md`.

**Architecture**: hybrid pipeline — PyMuPDF (`fitz`) handles all text extraction and image cropping; YOLOv8n-doclaynet handles visual region detection only. The two are not alternatives: YOLO finds where figures/tables/formulas are; fitz extracts the text and crops the image out of the PDF.

**Subprocess isolation (ADR-0007)**: `extract_with_yolo()` always runs in an isolated subprocess. `torch` + `ultralytics` (~1–1.5 GB RSS) load inside the subprocess and are freed by the OS on exit — they never enter the Celery worker's address space. The subprocess is invoked with `sys.executable -P` to prevent `app/tasks/email.py` from shadowing the stdlib `email` module (which `torch` needs internally). Timeout: 300s.

**Pipeline stages:**

1. **Pre-scan** — text-metadata-only analysis (no pixel rendering): detects column layout (1/2-column), header/footer band boundaries from horizontal rules or repeating text, page-number locations, title, and abstract.
2. **YOLO detection** — renders each page to PNG at 150 DPI, runs YOLOv8n-doclaynet (`conf=0.35`), maps pixel detections back to PDF point coordinates. Keeps `picture`/`table`/`formula` as visual regions; tracks `caption` for anchoring. Caption-gap inference recovers figures YOLO missed.
3. **Content extraction** — per page: crops visual regions as base64 PNG, extracts text blocks (skipping header/footer bands, blocks overlapping visual regions), detects headings (font-size, section-number prefix, spaced-small-caps paths), merges split paragraphs, sorts by column-aware order.
4. **Confidence scoring** — 0–100 score from: words/page (0–35 pts), YOLO mean detection confidence (0–30 pts, 15 neutral if no visuals), pages with content (0–25 pts), image crop success rate (0–10 pts). Embedded as `<meta name="extraction-confidence">` in the HTML output.

**Post-processing in `_process_pdf`:**

| Step | Action |
| --- | --- |
| Title | First `<h1>` or `<h2>` in body → stored as `item.title`; heading removed from body to avoid duplication in Reader. |
| Author removal | Short `<p>` blocks (< 300 chars) between title and abstract heading removed (author/affiliation lines). |
| Abstract | `extraction-description` meta tag from pipeline HTML → stored as `item.description`. |
| Thumbnail | First `<div class="figure-block"><img src="data:..."/>` → stored as `item.thumbnail_url`, removed from body. |
| Confidence badge | `extraction-confidence` meta tag parsed by the Reader (regex handles both BS4 attribute orderings). |

---

## 11. Semantic tagging pipeline (detail)

**Goal:** Extract fine-grained semantic labels from article content and store them with embeddings for similarity queries.

**Strategy in `generate_tags` (`tasks/tagging.py`):**

The old free-pass (pgvector similarity to already-tagged articles) was removed. It propagated coarse categorical labels and blocked semantic extraction. The pipeline is now LLM-only:

1. Call `gpt-4o-mini` with title + description + first 800 words of plain text.
2. Two-level prompt: **DOMAIN** (1-2 field-level labels, e.g. "personal finance") + **CONCEPTS** (3-4 specific ideas, e.g. "compound interest mechanics"). Examples span tech, food, finance, politics, health, and arts to avoid AI/tech bias.
3. Validated labels (max 6 words, max 100 chars) written to `item.tags`.
4. `upsert_tag_embeddings` is called immediately after — embeds any new labels via `text-embedding-3-small` and writes them to `tag_embeddings` (idempotent; already-embedded labels are skipped).

**`tag_embeddings` table:** Global lookup `{label TEXT UNIQUE, embedding VECTOR(1536)}`. Embeds each unique tag label once across all users. Powers tag-level similarity queries and the clustering pipeline.

**`auto_tags`/`tags` merge:** `auto_tags` was removed as a separate concept. All semantic tags write directly to `tags`. Users remove tags they don't want.

**Backfill:** `backfill_semantic_tags` Celery task re-tags articles with empty `tags`. Rate-limited to 50/min.

**Reader display:** Tags render inline below the author/date section in `ReaderArticle`. First tag = `FIELD` row (domain label); remaining tags = `IDEAS` row (concept labels joined with ` · `). Uses `font-mono text-[9px] uppercase` labels with a fixed `w-10` column. Dot format (`● tag`) used in Find Related shared-tags row.

## 11a. Reading themes (tag clustering)

**Goal:** Group a user's semantic tags into reading clusters so users can see what they're reading about.

**Pipeline (`tasks/clustering.py`, `cluster_user_tags`):**

1. Load all articles for the user that have `tags`.
2. Skip if fewer than 10 tagged articles.
3. Build a map of `{tag → [article_ids]}` for all unique tags.
4. Fetch embeddings for each tag from `tag_embeddings`.
5. Compute cosine similarity matrix via numpy.
6. Union-Find: merge tags with cosine similarity ≥ 0.60 into connected components.
7. Each component with ≥3 articles → one `ReadingCluster` row.
8. Cluster label = the tag within the cluster that appears on the most articles.
9. Replace existing clusters for the user (idempotent — rerun replaces, does not append).

**Scheduled:** `cluster_all_users_task` (Celery beat, weekly) dispatches per-user tasks.

**API:** `GET /themes` (`api/themes.py`) — returns `{clusters: [{id, label, article_count, tag_labels, top_articles}]}`.

**Frontend:** `/themes` page — card grid behind `NEXT_PUBLIC_SHOW_READING_THEMES=true` flag. Link appears in dashboard quick-actions row when flag is enabled.

---

## 11b. Entity graph

**Goal:** Extract named entities and relations from articles, embed them, and use them as a third retrieval lane in hybrid search to bridge vocabulary gaps between queries and articles.

**Tables:** `entities` (id, user_id, name, entity_type, description, embedding), `entity_mentions` (entity_id → content_item_id), `entity_relations` (source_entity_id, target_entity_id, relation_type, strength, description).

**Extraction (`tasks/article_analysis.py`, `analyze_article_task`):** Single LLM call (gpt-4o-mini) producing domain tags, concept tags, named entities (PERSON|CONCEPT|ORGANIZATION|PAPER|TOOL), and relations in one round-trip. Replaces the old separate `generate_tags_task` + `extract_entities_task`. Entities are upserted via `app/core/entity_graph.py:upsert_entity` (case-insensitive name deduplication per user). Concept tags not matched by an extracted entity are promoted to CONCEPT stubs so every article has searchable concept nodes.

**Embedding (`tasks/entity_embedding.py`, `embed_new_entities_task`):** Embeds any unembedded entity nodes as `"{type}: {name} — {description}"`. Called after every `analyze_article_task` run and available as a standalone backfill task.

**Beat schedule (`tasks/entity_backfill.py`):**

- `embed_new_entities_beat_task` — runs hourly; catches entity nodes that missed their embedding window (e.g. broker was down when `.delay()` fired). Calls `embed_new_entities()` directly per user. Idempotent.
- `backfill_missing_entities_task` — runs daily; queues `analyze_article_task` for articles where `entities_analyzed_at IS NULL`. Throttled to 50 articles/run. Uses `entities_analyzed_at` (not `entity_mentions`) so articles that were analyzed but produced zero entities are not re-queued.

**`entities_analyzed_at` column (`models/content.py`):** Nullable `DateTime` on `content_items`. Set to `NOW()` at the end of every `analyze_article` call regardless of how many entities were extracted. Migration: `b5c2f1d8e4a6`. Articles that pre-date the entity system have `NULL` — the daily backfill targets them.

**Deduplication (`tasks/entity_dedup.py`):** `entity_graph.merge_entity()` merges a loser entity into a winner — redirecting all mentions and relations atomically. `deduplicate_entities_task` uses HNSW ANN (O(N × K × log N)) to find candidate pairs above a similarity threshold (0.82), verifies each with an LLM call, then merges confirmed pairs. Winner is the lower UUID string (deterministic tie-breaker). The old O(N²) self-join has been removed. Known limitation: no cross-name deduplication; "Claude" and "Claude Code" are distinct entities.

**`matched_via` field:** `_entity_search` now returns `matched_via: [{name, sim}]` on each result — the entity names and cosine similarities that caused the article to be scored. Sorted by sim descending. Backend only; not surfaced in the frontend API response yet.

**Eval results:** See `evals/retrieval/results/report.md`. Entity lane adds net value on 45-query dataset (A→D: +1.4pp R@10 on full set). Regressions on 5 queries traced to vocabulary mismatch and Claude-family name fragmentation. See `docs/design/systems/hub-cap-investigation.md`.

---

## 11c. Highlight Connections — two-mode panel

The connections system surfaces how ideas in one article link to ideas captured elsewhere in a user's library, using a combination of embedding similarity and shared semantic tags.

### Backend endpoints

`GET /search/connections/{highlight_id}` — per-highlight connections.
- Response: `ConnectionsForHighlightResponse { source_note: str | null, connections: HighlightArticleConnection[] }`
- Each `HighlightArticleConnection` includes: `article_id`, `article_title`, `article_author`, `article_domain`, `shared_tags`, `passages` (top 2 matched passages from the connected article)
- Filters: only connections with ≥1 shared tag; similarity threshold 0.7; excludes highlights in the same article

`GET /search/connections/article/{content_id}/highlights` — all-highlights grouped view (Mode 2).
- Response: `list[HighlightWithConnections]` — each item has `highlight_id`, `highlight_text`, `connections: HighlightArticleConnection[]`
- Highlights with zero connections are omitted. Capped at 30 highlights.
- **Route ordering note:** registered before `GET /search/connections/article/{content_id}` (literal "highlights" segment must win over the broader pattern).

`GET /search/connections/{highlight_id}/insight/{article_id}` — AI-generated one-sentence insight explaining the conceptual link between a highlight and a connected article.
- Response: `InsightResponse { insight: str | null }` — `null` if generation fails (never 500)
- Cached in Redis with key `insight:{highlight_id}:{article_id}`, TTL 7 days
- Uses `gpt-4o-mini`. Two helpers isolated for test patching: `_get_redis_client()`, `_call_openai_insight()`

Quality filters applied at query time:
- `SIMILARITY_THRESHOLD_CONNECTIONS = 0.7` (cosine similarity; 0.5 was effectively zero)
- Only connections with `shared_tags` (intersection of source and target article tags) are returned
- Top 2 passages per connected article

### Frontend — ConnectionsPanel (two-mode)

`ConnectionsPanel` routes between two sub-panels based on `activeHighlightId: string | null`:

**Mode 1** (single highlight, `activeHighlightId` is set) — fetches `findHighlightConnections`, shows a compact "← all highlights" back button, optional source note, then connection cards. Each card has two zones: identity (title, author/domain, shared tag dots, lazy-loaded insight sentence) and passages (matched text from the connected article).

**Mode 2** (all highlights, `activeHighlightId = null`) — fetches `findHighlightGroupedConnections`, shows each highlight as a header card; connected articles are listed below each highlight.

**`c` key state machine (Reader.tsx):**
- panel closed → open in Mode 2
- Mode 1 → switch to Mode 2
- Mode 2 → close panel

**Clicking a highlight** in ReaderArticle calls `onShowConnections(highlightId)` → Reader sets `activeHighlightId` and opens the panel in Mode 1. The `onShowConnections` prop type changed from `() => void` to `(highlightId: string) => void`.

## 11d. Draft relevant reads (Phase 4)

`GET /lists/{list_id}/draft/relevant-reads` returns up to 5 library articles relevant to the current draft.

- Requires ≥50 words in the draft — returns `{items: []}` otherwise.
- Query = draft title + first 200 chars of content, run through `hybrid_search` (mode=full).
- User-scoped: only returns the authenticated user's library items.
- Frontend: `RelevantReadsPanel` renders below the editor in writing mode, behind `NEXT_PUBLIC_SHOW_DRAFT_READS=true`. Panel mounts only after the first autosave (`savedAt !== null`); shows "No matches yet" copy rather than collapsing when results are empty. Fires after each autosave via `onSaved` callback threaded through `WritingWorkspace → MarkdownEditor`.

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

**Entity lane** — `hybrid_search(mode="full")` adds a third retrieval path via
`_entity_search`. Query embedding is pre-computed once (shared with the semantic
lane to avoid double embedding) and compared against `entities.embedding` vectors
(type+name+description format) via an HNSW index on `entities.embedding`. All
entities above a sim threshold (0.40) are returned (no hardcoded `LIMIT`). Each
matching entity contributes `sim / log2(2 + article_count)` (IDF dampening) to
each article it mentions; per-article score is `best_contribution + 0.3 × sum(rest)`.
1-hop neighbor entity sims are computed via direct SQL cosine query against stored
embeddings rather than a fixed proxy value. Entity-sourced article scores are
blended into the RRF sum (`entity_score × 0.025`). The `_ENTITY_HUB_ARTICLE_CAP`
binary gate has been removed — hub entities are penalized proportionally by IDF
rather than excluded. The scoring logic is isolated in the pure function
`_score_entity_articles()` (unit-testable without DB). Entity lane adds net
retrieval value for vocabulary-distant queries; known regressions are documented
in `docs/design/systems/hub-cap-investigation.md`.

**Entity deduplication** (`tasks/entity_dedup.py`) — replaced the O(N²) self-join
with a per-entity HNSW ANN query (`_ANN_K = 20` neighbors). Candidate pairs are
normalised to `(min_uuid, max_uuid)` to collapse symmetric duplicates. Scales as
O(N × K × log N); at 2K entities/user the old O(N²) approach cost ~15s; the ANN
approach costs ~2s. The HNSW index (`entities_embedding_hnsw`, migration
`a3f1e8b2c7d9`) must be in place for full speedup; both dedup and entity search
fall back to sequential scan if the index is absent.

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
| `Reader` | content/[id], read/ | Shell: fixed navbar, reading progress bar, optional NowPlaying, HighlightsPanel + optional ConnectionsPanel sidebars, TOC sidebar, KeyboardShortcuts. Renders `<ReaderArticle>`. Manages `activeHighlightId` state (null = Mode 2 / all-highlights, string = Mode 1 / single-highlight). `c` key state machine: closed→Mode2, Mode1→Mode2, Mode2→close. `onShowConnections(highlightId)` callback opens Mode 1 directly from a highlight click. |
| `ConnectionsPanel` | reader | Two-mode connections panel. Mode 1: `activeHighlightId` is set — fetches per-highlight connections, shows back button + source note + article cards with insight. Mode 2: `activeHighlightId` is null — fetches all-highlights grouped view. Props: `contentId`, `activeHighlightId`, `isOpen`, `onBackToAll`, `onSelectHighlight`, `onNavigateToArticle`. |
| `EphemeralReader` | read/ | Wraps `Reader` with a sticky "Reading without saving" / "Save to Library" banner. Collects highlights locally via `onHighlightCreate` ref. On save: calls `contentAPI.create` with `initial_highlights`, clears sessionStorage, navigates to `/content/{id}`. |
| `ReaderArticle` | content/[id], lists/[id] | Reusable article body. Handles highlights, selection toolbar, summary, metadata editing, similar articles, ImageZoomModal, scroll position save/restore. `embedded` prop switches from window scroll to container scroll (used in split-pane list view). `focusModeEnabled` prop controlled by Reader's navbar. Exposes `highlights`, `refreshHighlights`, `scrollToHighlight` via `forwardRef`/`useImperativeHandle` for Reader's sidepanels. Uses `getIngestIssue(...)` fallback copy when full text is unavailable, including source-blocked and partial-extraction states. |
| `InlineError` | shared | Inline contextual error with optional dismiss/retry. See §16. |
| `EmptyState` | shared | Empty data state with optional CTA. Variants: `inline`, `bordered`. See §16. |
| `Sidebar` | layout | List navigation with counts. |
| `CratesClient` | crates | Grid of records, search/sort/filter, Now Digging bar. |
| `RecordDetail` | crates | Gatefold panel: art + metadata + tracklist + videos. Uses `useConfirmAction` hook for delete arm/trigger flow. |
| `ListeningMode` | crates | Full-screen music player overlay. `z-[80]` > RecordDetail `z-50`. |
| `VinylCard` | crates | Individual record card. |
| `YouTubePlayer` | crates | Invisible div hosting YouTube IFrame API. Plays queue sequentially. |
| `Navbar` | layout | Mini-player on mobile (hidden when Crates/audio feature is disabled). `mounted` guard avoids SSR hydration mismatch. Supports writing-mode controls (`Export`, `Close`) and fullscreen-aware auto-hide behavior (listens to editor scroll container instead of window). |
| `settings/ReadingSection` | settings | Keyboard-navigable carousel over 7 reading settings with live `PreviewBox`. Arrow keys cycle settings (←/→) and options (↑/↓). |
| `settings/FeatureVisibilitySection` | settings | CircleToggle rows for `showConnections` and `showCrates` feature flags. |
| `settings/PublicProfileSection` | settings | Username input + public profile visibility toggles. Calls `PUT /auth/me`. |
| `settings/DangerZone` | settings | Confirm-delete flow for account deletion. Uses `InlineError` for error feedback. |
| `settings/CircleToggle` | settings | Shared SVG circle toggle button used by Feature and Profile sections. |

### React Contexts

| Context | State |
|---------|-------|
| `AuthContext` | Current user, login/logout. Token stored in `localStorage['token']`. |
| `ListsContext` | List counts shown in sidebar. |
| `ToastContext` | Legacy — replaced by inline `InlineError` feedback. Context still exists but is unused. |
| `PlayerContext` | Vinyl player: `QueueTrack[]`, `currentIndex`, play/pause, `YT.Player` ref. Queue persisted in `localStorage['sedi-player']`. |
| `ReadingSettingsContext` | Reader preferences (`theme`, typography, bionic reading) plus local feature visibility toggles (`showConnections`, `showCrates`). Persisted in `localStorage['sedi-reading-settings']`. Initializes with `DEFAULTS` on server; loads saved values in `useLayoutEffect` after mount. Exposes `hydrated: boolean` — `PreviewBox` (inside `ReadingSection`) returns `null` until `hydrated` to avoid an SSR flash of defaults. |

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

### Custom hooks (`frontend/hooks/`)

| Hook | Purpose |
|------|---------|
| `useTagEditor` | Encapsulates tag state, available-tag loading, add/remove with optimistic updates, and inline error for `ContentItem` and `ContentCard`. Accepts `explicitTag` param on `handleAddTag` so suggestion dropdowns bypass state-sync issues. |
| `useConfirmAction` | Generic arm-then-trigger delete confirmation. Returns `{ armed, arm, cancel, trigger, toggle }`. All methods are stable (`useCallback`). `toggle()` arms on first call, triggers on second. |

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

**Run order**: golden-path tests first, then full suite.
```bash
pytest tests/test_golden_paths.py -v   # critical flows — must pass before anything else
pytest tests/ -x -q --ignore=tests/evals
```

| File | Coverage |
|------|----------|
| `test_golden_paths.py` | **Golden-path tests** — 18 tests across 5 critical user flows: submit+retrieve, search (keyword+user-scoped), highlights, lists, auth gates. Real DB, real HTTP, no mocks (except Celery dispatch). Run these first. |
| `conftest.py` | Postgres test DB fixtures, test client, user + auth header fixtures. QueuePool; DELETE cleanup between tests. |
| `test_auth_api.py` | Register, login, `/me`, duplicate email, wrong password, JWT tampering. |
| `test_analytics_api.py` | Stats counts; regression for `not ContentItem.is_read` bug. |
| `test_content_api.py` | Core content CRUD. |
| `test_content_extended.py` | `_clean_extension_html` edge cases; extension path; cross-user isolation. |
| `test_vinyl_api.py` | Full vinyl CRUD; soft delete; cross-user 404; Celery mocked. |
| `test_rate_limiter.py` | Sliding window unit tests (no DB). |
| `test_lists_api.py` | List CRUD; membership management; cross-user isolation. |
| `test_highlight_connections.py` | Per-highlight connections endpoint shape, shared-tag filtering, cross-user isolation, grouped-highlights view. |
| `test_insight_endpoint.py` | Insight generation: cache miss, cache hit, failure → null, unauthenticated 401. |

**Important patterns:**
- All Celery tasks are mocked with `patch(...)` — no broker needed.
- Cross-user isolation: always test that user A cannot act on user B's data.
- Rate limiter tests call `rate_limiter.requests.clear()` before any POST /content test to avoid 429 from test ordering.

### Eval harness

Location: `evals/` (project root) + `content-queue-backend/tests/evals/`

| Eval | Runner | What it measures |
|------|--------|------------------|
| Retrieval quality | `evals/retrieval/runner.py` | R@10, MRR, NDCG — 4 variants (A–D), 45 real queries vs production library |
| Search routing | `tests/evals/test_search_evals.py` | Classifier accuracy across 17 queries |
| Tagging quality | `tests/evals/test_tagging_evals.py` | Specificity, coverage, forbidden-tag rate |
| MCP contracts | `tests/evals/test_mcp_evals.py` | Response shape contracts |

Evals requiring a live DB (`tests/evals/`) are excluded from `make test` (CI uses the test DB seeded with synthetic articles for the harness). `evals/retrieval/` requires the production library and runs manually. Baselines stored in `evals/retrieval/baselines.json`; run artifacts in `evals/*/results/` (gitignored). CI regression gate: `evals/check_regressions.py` (not yet implemented).

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
| `content/content.js` | Injected on demand via `chrome.scripting.executeScript({ files })`. Runs Readability, extracts HTML/metadata, calls `detectAccessRestriction()`. Exposes `window.__sediExtractAndInlineContent` as a global so `popup.js` can call it via `executeScript({ func })` and properly await the result (required for Safari). |
| `content/reader-overlay.js` | Ephemeral reader. Appends a `position:fixed` shadow-DOM overlay div to the existing body instead of swapping it. Shadow DOM isolates all reader CSS from page stylesheets — external rules cannot match shadow elements. All layout values use `px` (not `rem`) so the page's `html { font-size }` cannot affect reader typography. Close is instant: `host.remove()` with a 0.12s fade-out animation. Features: progress bar, auto-hide navbar, TOC with scroll-spy, focus mode, theme toggle, reading settings panel (font/size/line-height/letter-spacing/width), "Save to sed.i" button. Scroll tracking on the overlay div (not `window`). Metadata byline mirrors `ReaderArticle.tsx` exactly. Strips `iframe/frame/object/embed/script` from injected HTML before render. |
| `popup/popup.js` | Two-button popup: **Read** (launches reader-overlay) and **Save to sed.i** (extracts + sends to service worker). Extraction uses `executeScript({ files })` to inject Readability + content.js, then a separate `executeScript({ func })` call to invoke `window.__sediExtractAndInlineContent()` and await the result. On open, shows page title + favicon/domain immediately, then reads og/meta tags via inline `executeScript` — no content script needed, under 10ms. Save button shows animated dot loading then `sent ✓`. Parses structured 409 errors into human-readable messages. Theme (`light`/`dark`) persisted via `chrome.storage.local`. |
| `background/service_worker.js` | Calls `POST /content` with `pre_extracted_html` payload. Maps `accessRestricted` → `pre_extracted_access_restricted`. Reads API base URL from `chrome.storage.local` (default: `https://api.read-sedi.com`). |

**Dev mode:** Long-press (≥2s) on the extension logo reveals an API URL field for pointing the extension at localhost. The URL is saved to `chrome.storage.local` and persists across sessions.

**Dev worker hot-reload:** `content-queue-backend/scripts/dev_worker.sh` starts Celery with `watchfiles` monitoring `app/` — worker auto-restarts on any Python file change, eliminating the need to manually restart after editing tasks.

### Safari port (`safari-extension/`)

Generated from the Chrome extension using Apple's `safari-web-extension-converter` (Xcode 26). No API namespace rewrites required — Safari 15.4+ supports MV3 and aliases `chrome.*` to `browser.*`. One JS adaptation: `content.js` exposes the extractor as a global function so `popup.js` can invoke it via `executeScript({ func })`, which Safari properly awaits (unlike `files:` injections).

| Path | Contents |
| --- | --- |
| `safari-extension/sed.i/sed.i.xcodeproj` | Xcode project — open this to build |
| `safari-extension/sed.i/sed.i Extension/Resources/` | Copy of extension files (mirrored from `extension/`) |
| `safari-extension/sed.i/sed.i/Assets.xcassets/` | App icon (all macOS sizes generated from `icons/icon128.png`) |

**Build configuration notes:**

- Both targets have `ENABLE_APP_SANDBOX = YES` + `ENABLE_OUTGOING_NETWORK_CONNECTIONS = YES` (required so the extension's `fetch()` calls can reach the API).
- Bundle IDs: app = `com.sedi.sed-i`, extension = `com.sedi.sed-i.Extension` (must share prefix with parent app).
- `MACOSX_DEPLOYMENT_TARGET = 10.14` on the extension target (Safari Web Extensions require macOS 10.14+).

**To sync Chrome changes to Safari:** `make safari-sync` copies `extension/` into the Resources folder; then rebuild in Xcode (⌘B).

**To open the Xcode project:** `make safari-open`

**Manual steps required before first build:** Set the developer Team on both targets in Xcode (Signing & Capabilities). A free Apple ID personal team is sufficient for local testing. See `docs/plans/safari-extension-plan.md` for the full step-by-step.

---

## 24. LLM infrastructure (SOTA stack)

### LLMClient (`app/core/llm_client.py`)

Single entry point for all LLM calls. Provider-agnostic — call sites use task constants, never raw model strings.

| Method | Purpose |
| --- | --- |
| `llm_client.embed(texts)` | Always uses `EMBED_PROVIDER` (default `"openai"`). No fallback — fails loudly to prevent vector-space mixing. |
| `llm_client.chat(messages, task=TASK_*)` | Routes to `LLM_PROVIDER`. Per-task model resolved from `LLM_MODEL_{TASK}_{PROVIDER}` env var, falling back to hardcoded defaults. Primary provider failure retries on the other. |
| `llm_client.structured_chat(messages, response_model=..., task=...)` | Returns a validated Pydantic model. OpenAI uses `instructor`; Bedrock uses a manual retry loop with validation feedback. |

Task constants (import from `llm_client`): `TASK_TAGGING`, `TASK_SUMMARY`, `TASK_MCP_SUMMARY`, `TASK_SQL_GEN`, `TASK_INSIGHT`.

Pydantic response models live in `app/core/llm_schemas.py` (`TagResponse`).

### LLM providers

| Provider | Config | Models |
| --- | --- | --- |
| OpenAI (default) | `LLM_PROVIDER=openai`, `OPENAI_API_KEY` | `gpt-4o-mini` (fast tasks), `gpt-4o` (SQL gen) |
| Bedrock | `LLM_PROVIDER=bedrock`, `AWS_ACCESS_KEY_ID/SECRET`, `AWS_REGION=us-east-2` | `amazon.nova-micro-v1:0` (fast), `amazon.nova-lite-v1:0` (standard), `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (SQL gen) |

Embedding always uses OpenAI (`text-embedding-3-small`, 1536-dim). Switching `EMBED_PROVIDER` requires a full re-embed migration.

AWS infrastructure provisioned via Pulumi in `infra/`. IAM users are least-privilege (specific model ARNs + one S3 bucket only). See `infra/__main__.py`.

### Observability

| Layer | Tool | Config |
| --- | --- | --- |
| LLM traces | Braintrust | `BRAINTRUST_API_KEY` — wraps OpenAI client; async flush with `worker_process_shutdown` safety drain |
| Error tracking | Sentry | `SENTRY_DSN` (backend), `NEXT_PUBLIC_SENTRY_DSN` (frontend) |
| Distributed tracing | OTEL → Grafana Cloud | `OTEL_EXPORTER_OTLP_ENDPOINT/HEADERS/PROTOCOL`, `OTEL_RESOURCE_ATTRIBUTES` — FastAPI + SQLAlchemy + Celery instrumented. pydantic-settings values mirrored to `os.environ` before `Resource.create()` so the OTEL SDK reads them correctly. |

All observability is opt-in: any key left empty silently disables that tool. Safe in dev and test with no config.

Celery workers bootstrap observability via `worker_process_init` signal → `setup_worker_observability()` (skips FastAPIInstrumentor since no FastAPI app is present). FastAPI calls `setup_observability(app)` from its lifespan.

### S3 object storage (`app/core/storage.py`)

PDFs saved to `s3://sedi-assets-{env}/pdfs/{user_id}/{item_id}.pdf`. Presigned URL endpoint: `GET /content/{item_id}/pdf-url` (1h expiry, configurable via `AWS_S3_PRESIGN_EXPIRY`). `AWS_S3_BUCKET` empty = S3 skipped, bytes discarded.

### Text-to-SQL MCP tool (`app/mcp/tools/query.py`)

`query_library` MCP tool lets Claude query the user's library via natural language → SQL. Security: AST validation via `sqlglot` (SELECT-only, allow-list of 7 tables), `_enforce_user_isolation()` rejects any SQL where `:user_id` is absent or not in an equality predicate (two-tier: text scan + AST EQ walk), `user_id` bound parameter, 500ms `statement_timeout`. See `docs/decisions/0006-text-to-sql-security.md`.

### Pipeline observability (Prefect — Layer 8)

Opt-in (`PREFECT_ENABLED=false` default). When enabled, ingestion phases 2–5 run as a Prefect flow (`app/workflows/ingestion.py`) with per-step retries and timing. Requires a Prefect server + worker. In production: two additional Railway services (server + worker), both using `prefecthq/prefect:3-python3.11`. `PREFECT_API_URL` must point to the server's internal Railway URL.

---

## 25. Multi-agent research pipeline

### Overview

A user submits a natural-language research question via `POST /research`. The API creates a `ResearchRun` row and dispatches `run_research_lead_task` to Celery.

**Status machine**: `queued → planning → searching → synthesizing → verifying → done | partial | failed`

### Agent roles

| Agent | Task | Description |
|-------|------|-------------|
| Lead | `run_research_lead_task` | Plans sub-questions, dispatches subagents via Celery chord, collects results, iterates if budget remains |
| Subagent | `run_research_subagent_task` | For one sub-question: expand query → hybrid search → relevance filter → chunk retrieval |
| Collector | `collect_subagent_results_task` | Chord callback: merge results, check budget, iterate or advance to synthesis |
| Synthesizer | `synthesize_run_task` | Compose final `ResearchBrief` from retrieved articles |
| Verifier | `verify_synthesis_task` | Remove hallucinated citations; after completion fires `extract_research_memory_task` |
| Recovery | `recover_orphaned_runs` | Beat task: marks stale in-progress runs `partial` after 10 min |

### Key schemas

- `ResearchRun` (`app/models/research.py`) — one row per run; stores plan, sub_questions, subagent_results, synthesized brief as JSONB
- `ResearchBrief` (`app/schemas/research.py`) — Pydantic output schema: key_findings, source_citations, coverage_assessment, confidence_score, gaps_identified
- `SourceCitation` — article_id, title, representative_highlight, relevance_score, coverage

### Budget control

Default budget: 50k tokens, 3 iterations, 6 subagents, 300s timeout, 8 target articles. The lead agent tracks token usage across iterations; if budget exhausted before convergence, status is set to `partial`.

### Resume support

`POST /research/{run_id}/resume` re-enters the lead with the existing plan, skipping already-covered sub-questions (intra-run resume). Searches already run (tracked by `idempotency_key` in `searches_run` JSONB) are skipped.

### Cross-run persistent memory (`research_memory` table)

After each `done` run, `extract_research_memory_task` writes one `ResearchMemory` row per sub-question with:
- `topic_embedding` (1536-dim via text-embedding-3-small)
- `coverage` ("full" | "partial" | "none")
- `topic_summary`, `gap_description`, `source_item_ids`

At planning time, the lead agent embeds the new question, performs pgvector cosine similarity search (IVFFlat, lists=100), and injects top-K past memory entries as "past research context" into the planner system prompt. Config: `RESEARCH_MEMORY_K=5`, `RESEARCH_MEMORY_MAX_AGE_DAYS=90`.

### Gap propagation to user profile

The nightly memory consolidation task (`consolidate_memory`) reads recurring `none`-coverage sub-questions from `research_memory` and writes a `persistent_gaps` text field to `user_profiles`. This feeds back into the MCP synthesis context.

### Agentic features reference

See `docs/design/systems/agentic-features.md` for a full inventory of all agentic capabilities, design tradeoffs, and known gaps.

---

## 22. Engineering workflow standard

The operational standard for this repository is documented in:

- `docs/OBSOLETE-engineering-workflow.md` — superseded; see `docs/instructions/workflow.md` and `docs/instructions/deploy-to-prod.md`
- `docs/instructions/workflow.md` — TDD loop, subagent rules, PoC detection, commit discipline
- `CLAUDE.md` — coding agent constraints (Karpathy principles, hard rules, trigger-based actions)
- `docs/plans/coding-agent-flywheel.md` — the comprehensive agent workflow improvement plan

### CI gates (`.github/workflows/backend-ci.yml`)

| Gate | When it runs | Fails if |
|------|-------------|---------|
| Ruff lint | Every push/PR | Any linting error in `app/` |
| ARCHITECTURE.md freshness | Every push/PR | `app/` or `frontend/` changed but ARCHITECTURE.md was not updated. Add `[skip-arch]` to commit message for refactors that don't affect architecture. |
| Golden-path tests | Every push/PR | Any of the 18 critical-flow tests fail |
| Full test suite | After golden paths | Any test fails (evals excluded — require credentials) |

### Local dev gates

- `make install-hooks` — installs `.githooks/pre-push` (runs ruff + tsc before every push)
- `make lint` — ruff + tsc + eslint
- `make test` — full backend + frontend suite

### Module self-documentation

Every Python module in `app/` has a 3-5 line module-level docstring describing its scope,
primary seam, and what it explicitly does NOT do. This lets agents identify the right file
without reading its full contents.
