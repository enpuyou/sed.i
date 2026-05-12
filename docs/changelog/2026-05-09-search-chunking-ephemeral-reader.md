# Changelog — 2026-05-09: Highlight Search, Multi-Chunk Embeddings, Ephemeral Reader

## 1. Strip `full_text` from list responses

**Intention:** `GET /content` was serializing the full article body (~50–500 KB per item) into every list response, bloating dashboard loads and cache entries.

**What changed:**
- Split `ContentItemResponse` (list shape, no `full_text`) from `ContentItemDetail` (reader shape, includes `full_text`)
- `GET /content` (list) returns `ContentItemResponse`; `GET /content/{id}` and `GET /content/{id}/full` both return `ContentItemDetail`
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
- **Extension:** Added "Read" button alongside "Save" in `popup.html/css`. `btn-read` click handler in `popup.js` sets `window.__SEDI_SKIP_IMAGE_INLINE` (skips async selector waits for instant extraction), runs content extraction while the popup is still alive, passes the article payload via `window.__sediArticle__`, then injects `content/reader-overlay.js` into the active tab. The popup closes and the overlay takes over — no new tab, no navigation.
- **`reader-overlay.js`:** In-tab DOM swap. Saves the original `document.body` reference, replaces it with a full reader UI (navbar with font size / theme toggle / save button, TOC sidebar, progress bar, article typography). Esc or the "← esc" button restores the original body. Theme cycles light → dark → true-black; font size has small/medium/large — both persist to `sessionStorage`. "Save to sed.i" sends via `chrome.runtime.sendMessage` to the service worker.
- **Frontend `/read` route:** New `app/read/page.tsx` — reads article from `sessionStorage` (set by the web app's own save flow) or URL hash. Renders `EphemeralReader`. Used for the "save and navigate to reader" path, not the extension overlay path.
- **`EphemeralReader` component:** Wraps `Reader` with a sticky save-or-discard banner. Highlights created during ephemeral reading are collected via `onHighlightCreate` callback (threaded through `Reader` → `ReaderArticle` → `HighlightToolbar`). On save: calls `contentAPI.create` with all pre-extracted fields + `initial_highlights` array for atomic article+highlight creation.
- **`POST /content`:** Updated to accept `initial_highlights` — creates highlights atomically after article insert.

**Impact:** Users can read instantly on the current page without saving — no tab opened, no loading. Highlights made during ephemeral reading are preserved on save. 3 new tests cover `initial_highlights` atomicity and the save flow.
