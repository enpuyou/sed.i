# sed.i — Domain Glossary (CONTEXT.md)

The canonical vocabulary for this codebase. Use these terms in code, docs, PR descriptions,
and architecture conversations. When a new concept needs a name, add it here first.

> **Keep this file up to date.** If you rename a concept in code, rename it here.
> If you introduce a new domain noun, add it. Target: 20-35 entries, one paragraph each.

---

## ContentItem

A URL a user has saved to their library. The central entity of the app. Has a lifecycle:
`pending` → `processing` → `completed` (or `failed`). Stores the normalized original URL,
extracted metadata (title, author, description, thumbnail), full article HTML (`full_text`),
an OpenAI embedding for semantic search, and reading state (`is_read`, `read_position`,
`is_archived`). Never hard-deleted — soft-deleted via `deleted_at` timestamp.

Model: `app/models/content.py`. Schema: `app/schemas/content.py`. API: `app/api/content.py`.

---

## Queue

The user's list of unread, non-archived ContentItems. The primary view of the app (`/`).
Not a stored entity — a filtered view of the Library. "Save to queue" means creating a
ContentItem.

---

## Library

All of a user's ContentItems regardless of read/archived status. The superset of Queue.
"Library" means all items; "Queue" means unread only.

---

## Ingestion

The pipeline from URL submission to a usable ContentItem. Two paths:

- **Normal path**: URL → `pending` ContentItem → Celery extraction task → `completed`.
- **Extension path**: URL + pre-extracted HTML (from browser extension) → skip Celery →
  `completed` immediately.

Re-adding a URL that already exists as an active item is blocked (HTTP 409). Re-adding a
previously deleted URL is allowed. Entry point: `POST /content` in `app/api/content.py`.

---

## Processing

The Celery phase that fills in a ContentItem's metadata and `full_text` after ingestion:
URL fetch, HTML extraction, metadata parsing, embedding generation, auto-tagging, optional
summarization. A ContentItem is in `processing` status while this runs. Lives in `app/tasks/`.

---

## Reading Status

A computed value (not stored) derived from `(is_read, read_position, is_archived)`:
- `archived` — `is_archived=True`
- `read` — `is_read=True` OR `read_position >= 0.9`
- `in_progress` — `read_position > 0`
- `unread` — otherwise

---

## Reader

The in-app full-article reading view. Two components:

- **`Reader.tsx`** — full-page reader. Owns fixed-position UI: navbar, progress bar, TOC
  sidebar, highlights panel, connections panel, keyboard shortcuts. Uses window scroll.
- **`ReaderArticle.tsx`** — article body only (typography, highlights, summary, image zoom,
  end actions). Embeddable in a split-pane. Accepts `embedded` prop to switch from window
  scroll to container scroll.

Route: `/content/:id`.

---

## Highlight

A user's text selection within a ContentItem. Stored with character offsets for re-rendering.
Has an OpenAI embedding used for Connection discovery. Minimum 20 characters for connection
search eligibility. Model: `app/models/highlight.py`.

---

## Connection

A semantic link between two Highlights across different articles, discovered by pgvector
cosine similarity on Highlight embeddings. Shown in the ConnectionsPanel in the Reader.
Not stored — computed on demand via `/search/connections/{highlight_id}`.

---

## List

A user-defined named collection of ContentItems. Can be public or shared. Has an optional
Draft. Separate from Queue (which is implicit). Model: `app/models/list.py`.

---

## Draft

A piece of long-form writing associated with a List. Stored as markdown, edited in the
Writing Workspace. Model: `app/models/draft.py`.

---

## Writing Workspace

The split-pane view for a List (`/lists/:id`). Left pane: ReaderArticle (embedded).
Right pane: Draft editor. Component: `WritingWorkspace.tsx`.

---

## Record (Vinyl)

An entry in the user's vinyl collection. Fetched from Discogs via a Celery task. Has tracks,
videos, ratings, genres, styles. Displayed in the Crates section. Gated by `SHOW_CRATES`
feature flag. Model: `app/models/vinyl.py`.

---

## Hybrid Search

The search system behind `/search/semantic`. Classifies the query and routes to:
- **Keyword** — PostgreSQL tsvector (no API call)
- **Filter** — SQL filter on known author/tag/domain (no API call)
- **Semantic** — OpenAI embedding + pgvector cosine similarity
- **Hybrid** — keyword + semantic fused with Reciprocal Rank Fusion (RRF)

`mode=full` always runs all three. `mode=auto` picks the cheapest path.
Lives in `app/core/hybrid_search.py` and `app/core/search_router.py`.

---

## Embedding

A 1536-dimensional vector (OpenAI `text-embedding-3-small`) encoding text meaning. Stored
on ContentItems (article-level) and Highlights (passage-level) in a pgvector `Vector(1536)`
column. `NULL` until the Celery embedding task completes.

---

## MCP (Model Context Protocol)

The server that exposes sed.i tools to AI assistants (Claude Desktop, Claude.ai). Two
transport modes: stdio (local) and HTTP + OAuth 2.1 + PKCE (cloud). 13 tools across read
and write categories. Full spec: `docs/mcp-server.md`. Implementation: `app/mcp/tools/`.

---

## Auto-tags

AI-suggested tags for a ContentItem, generated by the Celery tagging task. Stored in
`auto_tags` (array). Separate from user-confirmed `tags`. Shown as suggestions for the
user to accept or dismiss.

---

## Feature Flag

A `NEXT_PUBLIC_*` env var that gates incomplete or experimental UI sections. Current flags:
`SHOW_FOR_YOU`, `SHOW_HIGHLIGHT_CONNECTIONS`, `SHOW_CRATES`, `SHOW_EDIT_ARTICLE`.
Checked via helpers in `frontend/lib/flags.ts`.

---

## Celery Task

A Python function run asynchronously by a Celery worker. Broker: Redis. Key tasks:
`extract_metadata`, `generate_embedding`, `generate_summary`, `generate_auto_tags`,
`fetch_discogs_data`. All retry on failure with backoff. Task files: `app/tasks/`.
