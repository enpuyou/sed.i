# Changelog — 2026-05-09: Four Features

## 1. Strip `full_text` from list responses

**Intention:** `GET /content` was serializing the full article body (~50–500 KB per item) into every list response, bloating dashboard loads and cache entries.

**What changed:**
- Split `ContentItemResponse` (list shape, no `full_text`) from `ContentItemDetail` (reader shape, includes `full_text`)
- `GET /content` and `GET /content/{id}` now return `ContentItemResponse`; only `GET /content/{id}/full` returns `ContentItemDetail`
- Reader page (`frontend/app/content/[id]/page.tsx`) updated: cache checks require `full_text` to be present before using a cached item; PATCH response is merged with existing `full_text` to prevent flicker after status updates

**Impact:** List payloads no longer carry article bodies. Status updates no longer wipe `full_text` from reader state.

---

## 2. Highlight + note search

**Intention:** Users could search articles but not the text they'd highlighted or the notes they'd written — the most personally meaningful content in the queue.

**What changed:**
- Migration `008_highlights_search_vec`: added `search_vector TSVECTOR` column to `highlights` table + `UPDATE` trigger that sets it from `text || ' ' || coalesce(note, '')`
- `GET /search` now returns `{ articles: [...], highlights: [...] }` — highlights are ranked by `ts_rank` on the new vector
- `SearchModal` updated to render a second section for highlight results with their source article title and a link to the reader

**Impact:** Highlights and notes are now searchable. 3 new tests cover the trigger, the endpoint shape, and the frontend rendering path.

---

## 3. Multi-chunk embeddings with contextual retrieval

**Intention:** Semantic search matched only against a single embedding per article (the first ~500 tokens), missing content buried in long pieces.

**What changed:**
- New `content_chunks` table (migration `009_content_chunks`): `id`, `content_item_id`, `chunk_index`, `chunk_text`, `embedding vector(1536)`, `token_count`
- Structure-aware recursive splitter in `app/tasks/chunk_embeddings.py`: h1–h4 → paragraphs → sentences, ~350-token target, 40-word overlap
- Contextual retrieval prefix: `"From the article '{title}' (section {n} of {m}): {chunk_text}"` is prepended before embedding, improving retrieval recall
- `process_content_chunks` Celery task fires after every ingest; `process_all_missing_chunks` scanner backfills existing articles
- Semantic search rewritten as `UNION ALL` CTE: chunk-level cosine similarity + item-level fallback, `MAX(similarity)` per item, merged with keyword RRF scores

**Impact:** Semantic search now covers the full article body. 4 new tests cover chunking logic, contextual prefix, chunk search SQL, and the backfill scanner.

---

## 4. Extension ephemeral reader

**Intention:** Users could only save articles to the queue from the extension — there was no way to read immediately without saving, and no path to capture highlights before deciding to save.

**What changed:**
- **Extension:** Added "Read" button alongside "Save" in `popup.html/css`. `btn-read` click handler in `popup.js` runs content extraction, writes payload to `chrome.storage.session` via a new `setEphemeralArticle` message, then opens `{frontendBase}/read` in a new tab. Service worker handles `setEphemeralArticle` and `getEphemeralArticle` (one-time pickup, auto-clears after sending)
- **Frontend `/read` route:** New `app/read/page.tsx` — tries extension relay first (via `chrome.runtime.sendMessage`), falls back to `sessionStorage` for dev. Renders `EphemeralReader`
- **`EphemeralReader` component:** Wraps `Reader` with a sticky save-or-discard banner. Highlights created during ephemeral reading are collected via `onHighlightCreate` callback (threaded through `Reader` → `ReaderArticle` → `HighlightToolbar`). On save: calls `contentAPI.create` with all pre-extracted fields + `initial_highlights` array for atomic article+highlight creation
- **`POST /content`:** Updated to accept `initial_highlights` — creates highlights atomically after article insert

**Impact:** Users can read and annotate before deciding to save. Highlights made during ephemeral reading are preserved on save. The `chrome.storage.session` relay avoids the cross-origin sessionStorage restriction (extension ↔ frontend app on separate origins). 3 new tests cover `initial_highlights` atomicity, the relay message protocol, and the save flow.
