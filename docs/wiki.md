# sed.i — System Architecture Wiki

> A deep-dive into every major feature: what it does, how it's built, and why each decision was made. Written to be interview-ready — covering design trade-offs, not just descriptions.

---

## Table of Contents

1. [What the app is](#1-what-the-app-is)
2. [System topology](#2-system-topology)
3. [Data model](#3-data-model)
4. [Authentication and sessions](#4-authentication-and-sessions)
5. [Content ingestion pipeline](#5-content-ingestion-pipeline)
6. [PDF extraction](#6-pdf-extraction)
7. [The reader](#7-the-reader)
8. [Hybrid search](#8-hybrid-search)
9. [Recommendation engine](#9-recommendation-engine)
10. [Auto-tagging](#10-auto-tagging)
11. [Highlights and idea connections](#11-highlights-and-idea-connections)
12. [Lists and drafts](#12-lists-and-drafts)
13. [MCP server — LLM agent interface](#13-mcp-server--llm-agent-interface)
14. [Public profiles](#14-public-profiles)
15. [Crates — vinyl record collection](#15-crates--vinyl-record-collection)
16. [Chrome extension](#16-chrome-extension)
17. [Rate limiting](#17-rate-limiting)
18. [Error handling conventions](#18-error-handling-conventions)
19. [Analytics](#19-analytics)
20. [Security posture](#20-security-posture)
21. [Key design decisions (interview cheat sheet)](#21-key-design-decisions-interview-cheat-sheet)

---

## 1. What the app is

sed.i is a **read-it-later queue** built around search and retrieval, not just saving. A user pastes a URL and the system saves it, extracts the full article text in the background, generates an embedding for semantic search, auto-tags it, and presents it in a clean reader with highlights and cross-article idea connections.

The core user loop:
1. Save a URL from the browser or extension
2. System extracts content asynchronously
3. User reads in-app with highlights and notes
4. Search across the queue semantically, by keyword, or by filter operators
5. Recommendations surface what to read next
6. An LLM agent (via MCP) can query and write to the library

Secondary features: vinyl record collection (Crates), public profiles, list-based writing drafts, and a hosted MCP server with OAuth so AI agents can access the library.

---

## 2. System topology

```
Browser / Chrome Extension
  └─> Next.js 14 (Vercel)
        └─> FastAPI (Railway)
              ├─> PostgreSQL + pgvector   ← main data store + vector search
              ├─> Redis                   ← Celery broker + embedding cache + OAuth codes
              └─> Celery Workers (Railway)
                    ├─> URL fetch + metadata (requests + BeautifulSoup)
                    ├─> Article text extraction (trafilatura)
                    ├─> PDF layout extraction (PyMuPDF + YOLO/ONNX)
                    ├─> Embedding generation (OpenAI text-embedding-3-small)
                    ├─> Auto-tagging (pgvector similarity + gpt-4o-mini fallback)
                    ├─> AI summarization (OpenAI)
                    └─> Discogs metadata (vinyl)

LLM Agents (Claude Desktop, etc.)
  └─> MCP stdio (local) or MCP HTTP (hosted, OAuth 2.1 + PKCE)
        └─> FastAPI /mcp-transport
```

### Why this split

**FastAPI returns immediately.** URL fetching can take 2–10 seconds; LLM calls take longer. The API creates the DB row and returns 201 in <100ms; Celery handles everything slow.

**Why Celery + Redis over a managed queue (SQS, Cloud Tasks)?**
Redis was already needed for caching and OAuth state. Celery is well-understood, gives retry semantics, and requires no additional infrastructure.

**Why Vercel + Railway instead of one host?**
Next.js has first-class Vercel support. Railway handles long-running processes (FastAPI web + Celery worker + beat scheduler). Each host does what it's best at.

**Why PostgreSQL instead of a dedicated vector DB (Pinecone, Weaviate)?**
pgvector keeps everything in one database — joins are free, transactions span vector and relational data, and there's no sync problem between two stores. At personal-library scale (thousands to low millions of items per user), cosine similarity over pgvector is fast enough. The trade-off is that pgvector doesn't support ANN indexing as efficiently as dedicated vector DBs at billion-scale.

---

## 3. Data model

### `content_items` — the core entity

| Column | Purpose | Design note |
|--------|---------|-------------|
| `user_id` | Ownership | Every query filters by this. Cross-user leakage is structurally impossible. |
| `processing_status` | Pipeline state | `pending → processing → completed / failed`. Frontend shows reduced-opacity cards while extraction runs. |
| `processing_error` | Failure detail | Normalized: 401/403/paywall errors are classified as "source access issues", not parser bugs. Items can be `processing_status='completed'` but have a `processing_error` (partial extraction — something was extracted but less than expected). |
| `full_text` | Raw HTML | Stored as HTML, not plain text. The reader renders it; search uses a plain-text derivative. |
| `search_vector` | tsvector | Maintained by a PostgreSQL trigger. Dual-dictionary (english + simple) so both stemmed words and acronyms index correctly. |
| `embedding` | Vector(1536) | Populated by Celery after extraction. Null until then — all search paths degrade gracefully. |
| `read_position` | 0.0–1.0 | Scroll progress. Auto-marks `is_read=True` when ≥ 0.9. |
| `deleted_at` | Soft delete | Rows are never hard-deleted. Enables undo, audit trails, and safe Celery task execution. |
| `tags` / `auto_tags` | Two-layer tagging | `auto_tags` are AI suggestions pending review; `tags` are user-confirmed. |
| `submitted_via` | Source tracking | `'web'`, `'extension'`, `'api'`, `'email'` — useful for analytics and pipeline routing decisions. |

### `highlights`

A highlight stores character offsets (`start_offset`, `end_offset`) into `full_text`. It also gets an OpenAI embedding — this is what powers cross-article idea connections.

### `users` — `reading_patterns` JSONB column

Rolling reading stats stored as JSONB on the user row:
- `avg_reading_time` — weighted average, updated on each article completion
- `readings` — last 20 completed item IDs (rolling window, used for embedding-based recommendations)
- `preferred_tags` — tags from recently read articles

This is a deliberate denormalization. The recommendation engine reads one row instead of joining across thousands of `content_items` at request time. The stats are maintained incrementally (each article completion triggers an update) rather than computed on read.

### `drafts`

One draft per list per user. Stores markdown content, optional title, and word count. Auto-created on first `PATCH` so the frontend can always use a single endpoint without checking for existence first.

### Why soft deletes everywhere

`deleted_at IS NULL` is the canonical "is this row active" check. Missing this filter in a query is a potential data leak — it's enforced across all query helpers. Advantages: users can undo, background Celery tasks don't crash on a deleted item ID, and analytics can count historical saves.

---

## 4. Authentication and sessions

### JWT design

Login returns a signed JWT (`sub=email`, `exp=now+N_minutes`). Every request passes it as `Authorization: Bearer <token>`. FastAPI's `get_current_active_user` dependency decodes and verifies the token on every request.

**Why JWT over server sessions?**
JWTs are stateless — any FastAPI instance can verify a token without shared session storage. With sessions you'd need a shared Redis session store; with JWTs the signing key alone is sufficient. This matters because Railway can run multiple web instances.

**Trade-off:** JWTs can't be revoked before expiry. If a user changes their password, an old token is still valid until it expires. Acceptable for a personal reading app.

**Token storage:** `localStorage['token']` in the browser. XSS-accessible — a known gap, documented in §20.

### Registration with onboarding seeding

Registration runs synchronously (not a background task) because it must complete before the 201 response so the UI shows content immediately on first login. What gets seeded:

1. **"Getting Started with sed.i" guide article** — pre-extracted HTML, `processing_status='completed'` immediately. Includes two demo highlights (yellow: "Select any text", green: "Create Lists").
2. **Two example articles** queued for background extraction — New Yorker articles sent through the normal Celery pipeline.
3. **One example vinyl record** — Hiroshi Yoshimura's A·I·R (Discogs URL), queued for `fetch_discogs_metadata`.

### Email verification and password reset

Both use a `VerificationToken` table:

| Field | Notes |
|-------|-------|
| `token` | `secrets.token_urlsafe(32)` — 43 chars of URL-safe base64 |
| `token_type` | `"email_verification"` (24hr TTL) or `"password_reset"` (1hr TTL) |
| `is_used` | Prevents replay — marked true on first use |
| `expires_at` | Enforced at validation time |

Password reset invalidates any existing unused reset tokens for the user before creating a new one — prevents confusion from multiple concurrent reset links.

Emails are sent via **Resend** (HTTP API) as Celery background tasks. If `RESEND_API_KEY` is absent in local dev, the email HTML is logged to stderr instead of sent — no mock required, you just read the link from the terminal.

---

## 5. Content ingestion pipeline

A URL save triggers a multi-stage Celery chain. The API returns 201 before any of this runs.

### Stage 1: `extract_metadata`

```
fetch URL (requests, follow redirects)
  ├─> detect Content-Type → PDF path or HTML path
  ├─> parse OG / Twitter Card / JSON-LD metadata (BeautifulSoup)
  ├─> detect content_type (article / pdf / video / tweet / unknown)
  ├─> thumbnail fallback chain: OG → Twitter → JSON-LD → link[rel=image_src] → first in-content image
  ├─> run trafilatura (output_format="xml", include_tables/images/links=True)
  │     └─> xml_to_html() converts structured XML → clean HTML
  │         fallback: if <100 chars, convert plain text → <p> tags
  ├─> compute word_count, reading_time_minutes = max(1, round(word_count / 200))
  ├─> run paywall/access heuristics → set processing_error if restricted
  ├─> set processing_status = 'completed'
  └─> chain: generate_embedding.delay() → generate_tags.delay()
```

**Why trafilatura outputs XML first, not HTML?**
trafilatura's XML output preserves structural information (heading levels, table cells, list items) that plain HTML or text loses. The `xml_to_html()` conversion maps these back to semantic HTML elements the reader can render.

### Paywall detection — 7 heuristics

Items can reach `processing_status='completed'` with a `processing_error` set, meaning: we extracted something, but it's probably not the full article. The `_detect_limited_extraction_reason()` function applies these checks in order:

| Heuristic | Signal |
|-----------|--------|
| Schema markers | JSON-LD `isAccessibleForFree: false`, `content_tier` meta tags (`paid`, `premium`, `subscriber`, `metered`) |
| Restriction text | Keywords like "paywall", "subscriber-only", "access options" in page text or DOM class names |
| Bot blocking | Status codes 403/429, "cloudflare", "captcha", "blocks bots" in error text |
| Coverage ratio | Extracted text / source text < 0.4 AND < 5 paragraphs AND < 1800 chars |
| Teaser overlap | OG description found verbatim in extracted text AND extracted < 1200 chars AND < 5 paragraphs |
| Media-only | Figure/img present + 0 paragraphs + < 900 chars (image galleries, paywalled articles showing only art) |
| Structural truncation | Source has 6+ paragraphs, extracted ≤ 4 AND ratio < 0.4 AND < 1800 chars |

These map to user-facing categories via `ingestErrors.ts` on the frontend: `blocked`, `unauthorized`, `source_access`, `network`, `partial`, `unknown`. Each category has its own badge text, severity, and reader fallback message.

### Extension fast path

The Chrome extension runs Mozilla Readability client-side (in the browser, on the page being viewed) and sends `pre_extracted_html` in the POST body. The backend:
1. Calls `_clean_extension_html()` to strip duplicate title/description/thumbnail elements that Readability might preserve
2. Sets `processing_status='completed'` immediately
3. Still fires `extract_metadata.delay()` for missing metadata fields and to trigger the embedding chain

Result: extension-saved articles are fully readable in seconds. The extension also sets `pre_extracted_access_restricted=True` if `detectAccessRestriction()` fires — this immediately sets `processing_error` before Celery even runs.

### Retry semantics

`max_retries=3` on extraction tasks. Each failure saves a `processing_error` and eventually sets `processing_status='failed'`. The frontend shows failed items with a specific failure badge based on the error category.

---

## 6. PDF extraction

PDF extraction is a separate 3-stage pipeline handled by PyMuPDF (fitz) and an ONNX/YOLO layout model.

### Stage 1: Pre-scan (`_prescan_document`)

Runs on every page before any model inference. Pure text analysis — fast and free.

Per page:
- **Column detection:** Measures X-distribution of text blocks. A split at 0.42/0.58 page width indicates two-column layout.
- **Header/footer bands:** Detected via horizontal rules from `page.get_drawings()` — narrow lines (< 3pt height, > 30% page width) in the top 10% or bottom 20% of the page. Falls back to repeating-text detection if no rules are found.
- **Page number rects:** Margin blocks matching `\d[\d\s]*` (max 6 chars) — excluded from reading bounds.
- **Ambiguity scoring:** Two-column confidence scored by how evenly text is distributed left/right.

Document-level:
- **Repeating header/footer texts:** Blocks appearing on 3+ pages (at least 40% of all pages), normalized with `re.sub(r"\b\d+\b", "#", ...)` to match page numbers with different values. These are filtered from output.
- **Column mode:** Majority vote across all pages.

### Stage 2: Layout detection

Three implementations available (configured by environment):

| Implementation | Cost | Speed | Accuracy |
|---|---|---|---|
| `pymupdf_layout` (ONNX model) | Free | ~200ms/page | Good |
| `yolo` (torch, local) | Free after download | Variable | Good |
| `gpt4o_mini_vision` | ~$0.03/page | Network-bound | Best |

The YOLO/ONNX approach detects reading regions, figures, tables, and headers as bounding boxes. These are then used to extract text in reading order (column-aware, skipping headers/footers/page numbers).

### Stage 3: Post-processing (`_process_pdf`)

After layout extraction produces raw HTML:

| Step | What |
|------|------|
| Title | First `<h1>` or `<h2>` → stored as `item.title` |
| Author line removal | Short `<p>` blocks (< 300 chars) between title and abstract heading → removed (arXiv author/affiliation lines) |
| Abstract (arXiv style) | Standalone `<h1>ABSTRACT</h1>` + following `<p>` → stored as `item.description`, removed from body |
| Abstract (journal style) | Inline `<p>ABSTRACT: text...</p>` → same treatment |
| Figure thumbnail | First `<div class="figure-block"><img src="data:..."/>` → stored as `item.thumbnail_url`, removed from body |
| Confidence badge | Injected as `<meta>` tag; parsed by the Reader |

**Why pre-scan first instead of running the model on every page?**
The pre-scan identifies structural features (columns, headers, footers) that inform how model outputs are interpreted. It also identifies pages that don't need model inference (single-column, clean layout). Running the model on every page without context leads to misrouted reading order in two-column academic papers.

---

## 7. The reader

The reader is a `Reader` shell component wrapping `ReaderArticle`.

### Rendering pipeline

The stored HTML goes through several transforms before display:

1. **`sanitizeContentHtml`** — strips ephemeral UI elements that might have been saved accidentally
2. **`stripDocumentWrappers`** — removes `<html>/<body>` wrappers from PDF-extracted content
3. **`addHeadingAnchors`** — generates deduplicated IDs on headings for table-of-contents links
4. **`toBionic`** (optional) — bolds the first ~50% of each word. Optional toggle in reading settings.
5. Rendered with `dangerouslySetInnerHTML` — XSS risk documented in §20.

### Scroll position persistence

`read_position` (0.0–1.0) is updated via a debounced `PATCH /content/{id}` as the user scrolls. On open, the reader scrolls to the saved position. Auto-marks as read at ≥ 0.9.

**Why PATCH not a dedicated endpoint?**
Reusing the general `PATCH /content/{id}` route reduces API surface area. The frontend sends only `{read_position: x}` — Pydantic's partial update schema (`Optional` fields) handles the rest.

### Reading settings and SSR hydration

All settings (`theme`, typography, bionic reading, feature visibility toggles) are stored in `ReadingSettingsContext`, persisted in `localStorage['sedi-reading-settings']`. The context initializes from defaults on the server (for SSR) and loads saved values in `useLayoutEffect` (client-only, after hydration). Components that depend on these settings return `null` until the `hydrated` flag is true — this prevents a flash of wrong font/theme on initial load.

### Highlights in the reader

Highlights are stored with character offsets (`start_offset`, `end_offset`) into `full_text`. When the reader renders, it injects highlight spans by finding character ranges.

**Why character offsets instead of DOM ranges?**
DOM ranges (XPath, CSS selectors) break when the HTML structure changes. Character offsets into the source text are stable as long as `full_text` doesn't change. Re-extraction is not triggered automatically after the first successful extraction for this reason.

---

## 8. Hybrid search

A single `GET /search/semantic?query=...` endpoint handles four search strategies, routed by a classifier that runs in <1ms.

### The classifier (`app/core/search_router.py`)

Priority-ordered rules — first match wins:

| Query shape | Route | Example |
|-------------|-------|---------|
| Contains `author:`, `tag:`, `site:`, `after:`, `before:`, `is:` | SQL filter | `author:Paul Graham after:2025-01-01` |
| Quoted phrase | tsvector keyword | `"attention is all you need"` |
| Looks like a domain | SQL site filter | `substack.com` |
| Matches user's known author name | SQL filter | (user has articles by "Simon Willison") |
| Matches user's known tag | SQL filter | `music` |
| Ends with `?` or starts with question word | pgvector semantic | `how does attention work?` |
| ≤ 4 words, no question words | tsvector keyword | `llm`, `react hooks` |
| Everything else | keyword + semantic fused with RRF | `building products with AI` |

`get_user_search_context()` preloads the user's known authors and tags before classification — one SQL query, called once per search request.

### The three engines

**SQL filter path** (`parse_filter_query`): Pure SQLAlchemy query, no vector math, no full-text index. Fastest. Tag filter uses `EXISTS (SELECT 1 FROM unnest(tags) t WHERE t ILIKE :pat)` — case-insensitive partial match across the Postgres array.

**Keyword path** (`keyword_search`): PostgreSQL `tsvector` full-text search via `ts_rank_cd`. The `search_vector` column uses a dual-dictionary trigger:
- `english` dictionary: stems words (`running → run`, `articles → article`)
- `simple` dictionary: stores tokens as-is (`LLMs → llms`, `RAG → rag`, `API → api`)
- Combined with `||` so both dictionaries contribute to ranking
- Single-token queries use prefix matching (`to_tsquery('simple', 'llm:*')`) so `llm` matches `llms`
- `ts_rank_cd` with normalization flag `32` caps scores at 0–1
- If keyword returns 0 results (misspelling, partial word), falls back to semantic silently

**Semantic path** (`_semantic_search`): Embeds the query via OpenAI, runs cosine similarity against `content_items.embedding` in pgvector. Falls back to keyword if embedding fails or no embeddings exist yet.

### Embedding cache

Query embeddings are cached in Redis (`qemb:{sha256(query)[:16]}`, 1hr TTL). Searching "llm" five times calls OpenAI once.

### Reciprocal Rank Fusion (RRF)

When two or more engines run, ranked lists merge with RRF:

```
score(item) = Σ  1 / (60 + rank_in_list)
              all lists containing item
```

The constant 60 (from the original RRF paper) dampens the benefit of high rank — a rank-1 result doesn't dominate completely. RRF scores are not percentages — they're only meaningful for sorting. The frontend does not display them.

**Three-way fusion (`mode=full`):** Used by the SearchModal. Filter + keyword + semantic all run simultaneously. Keyword + semantic are fused first, then fused with filter results. This maximizes recall — every result from every engine is considered.

### Date filtering

`after:` and `before:` operators are extracted from the query before routing. The filter SQL path applies them natively in `WHERE`. Keyword and semantic engines receive the stripped query and their results are post-filtered by `created_at` in Python (`_apply_date_filter`). This ensures date filtering works correctly regardless of which engine returned a result.

### Untitled items excluded

Items with no title (failed/partial extraction) are excluded from all three search engines — they can't contribute useful results and would clutter the list.

### SearchModal vs SearchBar

**SearchBar** (navbar): `mode=auto` — classifier picks the cheapest path. Returns 5 results inline. No OpenAI call for keyword/filter queries.

**SearchModal** (Cmd+K): `mode=full` — always runs all three engines. Returns 10 results per page with prev/next pagination. Date preset chips: last 7 days / 30 days / 3 months / last year / custom range. Custom range uses cross-validated `min`/`max` on the date inputs to prevent invalid ranges.

---

## 9. Recommendation engine

`GET /content/recommended` — no ML inference at request time. Pure scoring against pre-computed embeddings.

### Scoring formula (max 75 points per item)

| Signal | Max | How |
|--------|-----|-----|
| Embedding similarity to recent reads | 30 | Cosine similarity to articles read in last 7 days. Takes the **maximum** across all recent reads (not average — you might have one deep interest that day). |
| Reading time match | 15 | Penalizes items far from `avg_reading_time`. A user who reads 5-minute articles shouldn't get 45-minute papers. |
| Recency | 20 | `max(0, 20 - days_old / 10)` — linear decay, reaches 0 at 200 days. |
| Tag overlap | 10 per match | Matches against `reading_patterns.preferred_tags` from recent reads. |

`mood` filter (`quick_read`, `deep_dive`, `light`) is a hard cutoff applied before scoring.

**Why `reading_patterns` on the user row (not a separate table)?**
Joining `content_items` to compute rolling stats at request time is expensive. JSONB on the user row makes the recommendation query a single row read + N embedding comparisons. Stats are maintained incrementally — each article completion triggers an `update_reading_patterns()` call that updates the rolling window.

**Why not collaborative filtering?**
This is a single-user app. Collaborative filtering requires many users to find similarity patterns. The embedding approach works for one user because their past reads express their interests in vector space.

---

## 10. Auto-tagging

**Goal:** Suggest relevant tags without spending money on every article.

### Two-pass strategy

**Pass 1 — free (pgvector similarity):**
- Find the user's already-tagged content with cosine distance < 0.25 (very similar articles)
- If ≥ 2 tags appear across ≥ 2 similar articles → auto-accept those tags immediately

**Pass 2 — cheap LLM (gpt-4o-mini):**
- Only runs if pass 1 finds nothing (new library, first article in a topic area)
- Sends title + description + first 800 words of plain text to gpt-4o-mini
- Parses JSON array from response

**Both passes write to both `auto_tags` and `tags` immediately.** The `/tags/accept` and `/tags/dismiss` endpoints exist for manual correction after the fact, not as a mandatory approval step.

**Why this design over always calling GPT?**
At 1000 articles, calling gpt-4o-mini for every article costs ~$0.20/month. But at 10,000 articles with a well-tagged library, pass 1 handles ~80% of new articles for free. The cost curve flattens as the library grows — the system gets cheaper per article over time.

---

## 11. Highlights and idea connections

### Highlight storage

Character offsets (`start_offset`, `end_offset`) into `full_text`. The reader injects highlight spans by finding character ranges in the rendered HTML.

### Embeddings on highlights

After a highlight is created, `generate_embedding` embeds the highlight text (same OpenAI model, same Celery task used for articles). The 1536-dim vector is stored in `highlights.embedding`.

### Cross-article connections

`GET /search/connections/{highlight_id}`:

```sql
SELECT h.id, h.text, h.color, ci.title,
       (1 - (h.embedding <=> CAST(:source AS vector))) as similarity
FROM highlights h
JOIN content_items ci ON h.content_item_id = ci.id
WHERE h.user_id = :uid
  AND h.content_item_id != :source_article_id
  AND h.embedding IS NOT NULL
  AND LENGTH(h.text) >= 20
  AND (1 - (h.embedding <=> :source)) >= :threshold
ORDER BY h.embedding <=> :source
LIMIT :limit
```

The `LENGTH(h.text) >= 20` filter excludes trivially short highlights that produce noisy embeddings.

`GET /search/connections/article/{content_id}` — runs the above for every highlight in an article, groups by connected article, and sorts by total similarity score. This powers the "Connections" tab in the reader.

**Why a threshold (default 0.75) instead of top-K?**
Top-K always returns results, even irrelevant ones. A threshold means if nothing in your library is meaningfully related, the panel is empty rather than showing noise.

---

## 12. Lists and drafts

### Lists

Many-to-many join table (`content_list_membership`) between users and content items. Key design decisions:

- **`added_by`** on the join table — preserves who added an item (future sharing feature)
- **List soft deletion doesn't cascade-delete items** — items just lose membership
- **`GET /lists/{id}/content`** joins `content_items` and filters `deleted_at IS NULL` — items independently soft-deleted don't appear

### Drafts

Each list can have one markdown draft (one per list per user). The draft is a writing space for notes, summaries, or essays about the list's content.

**API:**
- `GET /lists/{id}/draft` — 404 if no draft exists yet
- `POST /lists/{id}/draft` — explicit creation (409 if already exists)
- `PATCH /lists/{id}/draft` — **auto-creates if not exists**; this is the autosave target
- `DELETE /lists/{id}/draft` — removes the draft

**Why auto-create on PATCH?**
The frontend can always `PATCH` without checking whether a draft exists. This removes a whole class of race conditions around "create vs update" at the cost of one extra SQL check per autosave. For an autosave endpoint called every few seconds, the simpler mental model is worth it.

### Split-pane reader in list view

`/lists/[id]` renders a split-pane layout: list on the left, `ReaderArticle` on the right. `ReaderArticle` accepts an `embedded` prop that switches from `window.scroll` to a container scroll, so the scroll position and reading progress bar work correctly inside the panel.

---

## 13. MCP server — LLM agent interface

The MCP (Model Context Protocol) server exposes the library to LLM agents. It has two deployment modes.

### Phase 1: Local stdio (Claude Desktop)

Launched as a subprocess by Claude Desktop:
```json
{
  "mcpServers": {
    "sedi": {
      "command": "poetry",
      "args": ["run", "python", "-m", "app.mcp.server"],
      "cwd": "/absolute/path/to/content-queue-backend",
      "env": { "SEDI_TOKEN": "<your-sedi-jwt>" }
    }
  }
}
```

Auth: the JWT from `localStorage['token']` in the browser is passed as an env var. `app/mcp/auth.py` decodes and verifies it against the database on every tool call. All MCP stdio logging goes to stderr (stdout is reserved for JSON-RPC messages).

### Phase 2: Hosted HTTP (OAuth 2.1 + PKCE)

Mounted as an ASGI app at `/mcp-transport` on the main FastAPI server. Fully compliant OAuth 2.1 with PKCE (S256 only — no plain challenge method).

**OAuth endpoints:**

| Endpoint | Standard | Purpose |
|----------|---------|---------|
| `/.well-known/oauth-authorization-server` | RFC 8414 | Discovery metadata |
| `/.well-known/oauth-protected-resource` | RFC 9728 | Resource server metadata |
| `/mcp-transport/register` | RFC 7591 | Dynamic client registration |
| `/mcp-transport/authorize` GET | — | Login form (HTML) |
| `/mcp-transport/authorize` POST | — | Credential check + auth code issuance |
| `/mcp-transport/token` | RFC 6749 | Code exchange + refresh token grant |

**Token lifecycle:**
- Auth codes: 5-minute TTL, stored in Redis (`mcp:code:*`)
- Refresh tokens: configurable TTL (`MCP_REFRESH_TOKEN_EXPIRE_DAYS`), stored as SHA-256 hash in Redis
- Access tokens: the app's standard JWTs — no separate token type

**Client validation:** Static allowlist from `MCP_OAUTH_CLIENTS_JSON` env var (JSON dict of `client_id → [redirect_uri...]`). If the env var is empty, any dynamically registered client is accepted. Non-matching `client_id` or `redirect_uri` → 400.

**Security:** OAuth login form HTML-escapes all reflected parameters (`client_id`, `redirect_uri`, `state`, `code_challenge`) to prevent reflected XSS. CSRF embedded in OAuth state parameter.

**Why PKCE?**
PKCE prevents authorization code interception attacks. Even if an attacker captures the auth code in transit, they can't exchange it without the code verifier that was never sent over the network.

### MCP tools (15 total)

**Read tools:**

| Tool | What it does |
|------|-------------|
| `list_lists()` | All reading lists with item counts |
| `get_list_content(list_id, include_full_text, limit)` | Articles in a list. `full_text` capped at 32,000 chars to prevent LLM context overflow. Max 200 items. |
| `get_content_item(item_id, include_full_text)` | Single article metadata or full content |
| `search_content(query, limit)` | Hybrid search (same engine as the app). Max 50 results. |
| `find_similar(item_id, limit, threshold)` | Cosine similarity search. Default threshold 0.5. |
| `get_highlights(item_id?, list_id?)` | Three modes: all highlights for an article, all highlights in a list, or all highlights across the library (max 100) |
| `get_draft(list_id)` | Writing draft for a list, or null if none |
| `get_reading_stats()` | `{total_items, read_count, unread_count, archived_count}` |
| `summarize_list(list_id, style, max_items)` | AI summary. Styles: `overview`, `themes`, `gaps`, `timeline`. `gaps` style includes draft content to identify what's missing. Cached in-process by (user_id, list_id, content_hash, style). |

**Write tools:**

| Tool | What it does |
|------|-------------|
| `add_content(url)` | Save URL, triggers background extraction |
| `update_draft(list_id, content, title?)` | Create or update list draft |
| `create_list(name, description?)` | New reading list |
| `add_to_list(list_id, item_id)` | Add content item to a list |

**Why 32,000 char limit on `full_text`?**
An LLM agent calling `get_list_content` with a large list could easily overflow its context window with full article text. The 32k cap prevents this while still giving the agent substantial content to work with. Agents needing full text should call `get_content_item` per article.

---

## 14. Public profiles

Users can expose their queue and/or crates publicly via `/[username]`.

### Visibility model

Three independent toggles:
- `user.is_public` — profile visible at all
- `user.is_queue_public` — queue items visible
- `user.is_crates_public` — vinyl records visible

Individual items additionally have `is_public` — a user can have a public queue but hide specific articles.

### No-auth API routes

`/public/u/{username}/...` routes have no `get_current_active_user` dependency. They check `is_public` flags and return 403 if the profile is private. These are the only unauthenticated API routes in the system (besides `/auth`).

### Guest reading limit

The public reader tracks reads in `localStorage` per profile owner. After 3 reads from the same profile, it shows a signup prompt. Entirely client-side — a conversion nudge, not server-enforced access control.

---

## 15. Crates — vinyl record collection

A second vertical for managing a vinyl record collection. Users paste a Discogs URL; Celery fetches metadata from the Discogs API asynchronously.

### `vinyl_records` table (separate from `content_items`)

The metadata structure (tracklist JSON, label, catalog number, year, artist) doesn't fit the `content_items` schema. A separate table is cleaner than adding 10 nullable columns to a content items table that doesn't need them.

| Column | Notes |
|--------|-------|
| `discogs_release_id` | Integer parsed from URL, used for the Discogs API call |
| `tracklist` | JSONB: `[{position, title, duration}, ...]` |
| `videos` | JSONB: `[{title, uri, duration}, ...]` — Discogs links + user-added YouTube links |
| `status` | `'collection'`, `'wantlist'`, `'library'` — not a boolean `wantlist` column |
| `processing_status` | Same `pending → completed/failed` pattern as content items |

### Music playback

`PlayerContext` manages a queue of `QueueTrack[]` objects derived from `vinyl_records.videos`. `YouTubePlayer` is an invisible div hosting the YouTube IFrame API — plays videos sequentially. Queue persists to `localStorage['sedi-player']` across navigation.

**Why YouTube IFrame API instead of an audio element?**
Discogs stores YouTube links as listening sources. The IFrame API gives programmatic play/pause/next without needing a raw audio URL. The trade-off: requires the video to be embeddable (some YouTube videos disable embedding).

---

## 16. Chrome extension

MV3 extension. Three components in a chain:

### `content.js` (injected into every page)

Runs Mozilla Readability on the page to extract clean article HTML. Also runs `detectAccessRestriction()` — checks JSON-LD `isAccessibleForFree`, `content_tier` meta tags, and known paywall DOM selectors (subscription modal class names, etc.).

### `popup.js`

Shows a preview before saving: word count, read time, author, publish date, access-restriction signal. User clicks "Save" → sends payload to service worker.

### `service_worker.js`

Calls `POST /content` with `pre_extracted_html`, `pre_extracted_access_restricted`, and all metadata fields. Maps `accessRestricted: true` → `pre_extracted_access_restricted: true`, which triggers immediate `processing_error` flagging on the backend before Celery runs.

**Dev mode:** Long-press (2s) on the extension logo reveals an API URL field — saved to `chrome.storage.local` so it persists. Lets you point the extension at `localhost` without rebuilding.

---

## 17. Rate limiting

`RateLimitMiddleware` applies to `POST /content` only. Sliding window algorithm:

- Each user (JWT user ID) has a `deque` of request timestamps
- On each request: pop timestamps older than the window; if `len(deque) < max_requests`, allow and append
- Limits: **10 requests / 60 seconds** AND **50 requests / 3600 seconds**
- 429 response includes `Retry-After` header and CORS headers

**Known limitation:** State is in-memory per process. Multiple Railway instances would each have independent counters — a user could exceed the limit across instances. Production fix: Redis-backed `INCR` + `EXPIRE`. Documented, not yet implemented.

---

## 18. Error handling conventions

### Backend — one shape, always

All errors return `{detail: string}`. Global exception handlers in `app/main.py`:
- `RequestValidationError` → 422 with simplified field messages (no internal Pydantic paths leaked)
- `SQLAlchemyError` → 500 with "database error" (no query details leaked)
- Unhandled `Exception` → 500 with "unexpected error" (full traceback logged server-side)

### Frontend — inline, never toasts

All error feedback is contextual — near the action that failed. `InlineError` (left red border, muted background) is the only error UI primitive.

**Error message tone:** "Couldn't [action]. Try again." Never "Failed to..." or "Error: ...".

**State rendering order:** loading → error → empty → data. Mutually exclusive — never render two at once.

**Optimistic updates:** UI updates immediately; API call fires; if it fails, UI reverts and `InlineError` appears.

**`fetchWithAuth`:** Central API helper. Parses `{detail}` from backend responses. Rate limit reads `Retry-After` header. All API methods (including deletes) route through it.

### Ingestion error classification (`ingestErrors.ts`)

Maps `(processing_status, processing_error)` → user-facing category:

| Category | When | Badge / reader message |
|----------|------|----------------------|
| `unauthorized` | 401 errors, "login required", "authentication required" | "Blocked by source site" |
| `blocked` | 403, "cloudflare", "captcha", "blocks bots", 429 | "Blocked by source site" |
| `source_access` | Other HTTP errors | "Source connection issue" |
| `network` | "timeout", "connection", "dns", "unreachable" | "Source connection issue" |
| `partial` | `status='completed'` AND `processing_error` set | "Partial content" |
| `unknown` | Fallback | "Extraction failed" |

Each category maps to a severity (`error` / `warning`), badge text shown on the queue card, and a full message shown in the reader when `full_text` is unavailable.

---

## 19. Analytics

### PostHog

Server-side events captured in `app/api/auth.py`:
- `user_signed_up` — on successful registration
- `user_logged_in` — on successful login
- `account_deleted` — just before account removal

Initialized in the FastAPI `lifespan` handler. If `POSTHOG_API_KEY` is absent, `posthog.disabled = True` — no events sent, no errors. `posthog.shutdown()` called on app shutdown to flush the queue.

Frontend: `PostHogIdentify` component (mounted inside `AuthProvider`) calls `posthog.identify(userId, {email, username})` when logged in and `posthog.reset()` on logout. Autocapture, pageview, and pageleave events enabled by default.

### Stats API

`GET /analytics/stats` — pure SQL aggregation, no PostHog:
- `total_items`, `items_read`, `items_unread`, `items_archived`, `total_reading_time_minutes`, `read_reading_time_minutes`

**The `is_read.is_(False)` bug fix:** The original code used Python `not ContentItem.is_read` which always evaluates `False` (SQLAlchemy column objects are truthy). The fix: `ContentItem.is_read.is_(False)`.

---

## 20. Security posture

### Current protections

| Area | Implementation |
|------|---------------|
| Passwords | bcrypt via passlib |
| API auth | JWT signed with `SECRET_KEY`, expiry enforced on every request |
| Cross-user isolation | Every DB query filters by `user_id = current_user.id` |
| CORS | Origin allowlist from `ALLOWED_ORIGINS` env var |
| Rate limiting | Sliding window on `POST /content` |
| XSS in OAuth | HTML-escaped reflected parameters in MCP OAuth login page |
| MCP OAuth | Strict client_id + redirect_uri allowlist; PKCE S256 only; CSRF in state |
| Error sanitization | No internal query details or stack traces in API responses |

### Known gaps (documented, not hidden)

| Risk | Current state | Production fix |
|------|--------------|---------------|
| XSS via article HTML | `dangerouslySetInnerHTML` in reader without sanitization | DOMPurify or sandboxed iframe |
| SSRF | Backend fetches user-provided URLs without IP validation | Block internal ranges (169.254.x.x, 10.x.x.x, 127.x.x.x) |
| Token storage | `localStorage` is XSS-accessible | httpOnly cookies + CSRF tokens |
| Rate limit in-memory | No cross-instance enforcement | Redis-backed `INCR` + `EXPIRE` |

The gaps are accepted trade-offs for a personal-use app at current scale, not oversights. Any engineer picking up this codebase can see exactly what needs hardening before a public launch.

---

## 21. Key design decisions (interview cheat sheet)

---

**Q: Why Celery + Redis instead of FastAPI background tasks?**

FastAPI's `BackgroundTasks` run in the same process. A slow extraction blocks other requests. Celery runs in a separate worker process — failures, retries, and slow jobs don't affect API latency. Redis is already provisioned for caching and OAuth state, so Celery adds no infrastructure cost.

---

**Q: Why store article HTML instead of plain text?**

The reader needs structure — headings, code blocks, images, lists. Plain text loses all of that. The trade-off is storage size and XSS risk. XSS is on the known-gaps list (DOMPurify is the fix).

---

**Q: Why not just use Elasticsearch or Typesense for search?**

pgvector + tsvector together cover keyword, semantic, and filter search in one database. No sync lag, no second datastore, no additional cost. The RRF fusion technique is what production systems (Elasticsearch 8.x ships it natively) use. The only thing missing is ANN indexing (HNSW) for billion-scale vector search — not relevant here.

---

**Q: How does the hybrid search classifier work?**

It's a priority-ordered set of regex heuristics and user data lookups that runs in <1ms with no LLM call. Explicit operators first, then domain detection, then the user's known authors/tags (loaded from DB before classification), then question detection, then word count threshold (≤4 words = keyword, more = hybrid). `mode=full` bypasses the classifier entirely and runs all three engines simultaneously.

---

**Q: Why dual-dictionary tsvector instead of just the English dictionary?**

The English stemmer treats "LLMs" as a token it doesn't know how to stem — it comes out as `llms`. A query for `llm` (stemmed via English) doesn't match `llms`. The simple dictionary stores tokens as-is, so `llms` is indexed and prefix matching `llm:*` catches it. Running both dictionaries means you get stemming benefits (run/running match) AND acronym accuracy.

---

**Q: How does the MCP server handle auth in two different modes?**

Phase 1 (stdio): JWT passed as environment variable, decoded by `app/mcp/auth.py` on every tool call. Simple but requires the user to copy a token from their browser.

Phase 2 (HTTP): Full OAuth 2.1 + PKCE flow. The MCP client redirects to the sed.i login form, user authenticates, gets an auth code, exchanges it for a sed.i JWT. The end result is the same JWT the regular API uses — no separate token type, no extra verification logic.

---

**Q: Why character offsets for highlights instead of DOM ranges?**

DOM ranges (XPath, CSS selectors) break when HTML structure changes — e.g. bionic reading transforms, re-extraction, or adding heading anchors. Character offsets into the source `full_text` string are stable as long as that string doesn't change. This is also why re-extraction after a successful extraction isn't triggered automatically.

---

**Q: Why JSONB for `reading_patterns` instead of a separate analytics table?**

The recommendation engine reads one row per request. A separate table would require joining `content_items` across potentially thousands of rows to compute rolling stats at request time. JSONB on the user row is a deliberate denormalization — stats are maintained incrementally (each article completion updates the rolling window) rather than computed on read.

---

**Q: Why soft deletes everywhere?**

Hard deletes are irreversible and can crash background Celery tasks holding a reference to a deleted ID. Soft deletes let users undo, preserve audit trails, and keep background tasks safe. The trade-off: every query must include `AND deleted_at IS NULL`. Missing this is a data leak — it's enforced at the ORM layer in shared query helpers.

---

**Q: How does the extension fast path work, and why?**

The extension runs Mozilla Readability client-side (in the browser, on the page being viewed) and sends pre-extracted HTML. The backend skips trafilatura, sets `processing_status='completed'` immediately, and still fires Celery for embeddings and any missing metadata. Result: extension-saved articles are fully readable in seconds. The extension also detects paywalls client-side (JSON-LD signals, DOM selectors) and signals this with `pre_extracted_access_restricted=True` — the backend sets `processing_error` before Celery even runs.

---

**Q: How does the PDF extraction pipeline differ from article extraction?**

PDFs go through a 3-stage pipeline: (1) a free pre-scan per page that detects column layout, header/footer bands, and page numbers using PyMuPDF text block positions; (2) layout model inference (YOLO/ONNX by default, GPT-4o-mini vision as a higher-accuracy option) to identify reading regions, figures, and tables; (3) post-processing that extracts the title from the first heading, removes author lines from arXiv-style front matter, detects and removes abstracts (stored as `description`), and extracts the first figure as a thumbnail. The pre-scan informs how model outputs are interpreted, especially for two-column academic papers where reading order matters.

---

**Q: What can an LLM agent do with the MCP server?**

Read: browse lists, read full article content (capped at 32k chars to prevent context overflow), search the library with the same hybrid engine the app uses, find articles similar to a given one, read all highlights, get reading stats, and generate AI summaries of a list in different styles (overview, themes, gaps, timeline). Write: save new URLs, create lists, add articles to lists, and create/update writing drafts. The `gaps` summary style cross-references the list's draft to identify what's missing — useful for research writing workflows.

---

*Last updated: 2026-04-07. Reflects the hybrid search feature and all features through the enhancement/hybrid-search branch.*
