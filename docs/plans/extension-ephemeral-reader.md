---
type: plan
status: archived
last_updated: 2026-05-08
consumer: agent
---

# Plan: Extension Ephemeral Reader
Date: 2026-05-08
Status: Draft

## Goal
Add a "Read" mode to the browser extension that renders any webpage in sed.i's full Reader UI — without saving to the library. The user can highlight while reading, then optionally save the article with those highlights already attached. Clicking Save in ephemeral mode transitions the article into the library seamlessly.

## Non-goals
- Does not deprecate the existing Save flow — the extension keeps its current save behavior
- Does not require a backend change to render (transport is sessionStorage + postMessage)
- Does not support TTS in ephemeral mode (TTS requires a saved `content_item_id` for progress tracking — add in a later iteration)
- Does not support the Connections panel in ephemeral mode (requires highlight embeddings which are generated async)
- Does not support links panel or reading progress persistence in ephemeral mode

## Current state
The extension only has a Save flow. It runs Readability.js on the page, inlines images, and POSTs `pre_extracted_html` to the backend. The frontend has a full Reader (`/content/[id]`) but it always loads from a saved `ContentItem`. No ephemeral path exists anywhere.

---

## How the industry does this
Standard approach (used by Pocket, Readwise, Instapaper extensions): the extension passes extracted HTML to a new tab via `sessionStorage` and `postMessage`. No backend call needed to render. The frontend reads from sessionStorage on mount, renders the article using the existing Reader component, and the content never touches the database unless the user explicitly saves.

---

## Architecture decisions

**Decision**: Transport method — how extracted HTML gets from extension to frontend tab
**Options considered**:
1. `postMessage` from extension to a tab it opens — requires the extension to control the tab lifecycle
2. `sessionStorage` key written by extension, frontend reads on mount — simpler, tab can be opened independently
3. Short-lived backend endpoint (extension POSTs HTML, gets a temp UUID) — adds backend state, unnecessary complexity
**Recommendation**: Option 2 (sessionStorage). Extension writes `sedi_ephemeral_article` key with JSON payload, then opens `{appUrl}/read`. Frontend reads it on mount, clears the key after reading. This is the standard pattern — no auth required, no server roundtrip, works offline if the app is loaded.
**Reversibility**: Easy — a sessionStorage key has no persistence.

**Decision**: Highlights during ephemeral reading — what happens on save?
**Options considered**:
1. Discard ephemeral highlights on save — simplest, no merge problem; user re-highlights after save
2. Bundle ephemeral highlights with the save POST — atomic: article + highlights created together; user doesn't re-do work
3. Save article first, then POST highlights as a second request — risk of partial success (article saved, highlights lost on network error)
**Recommendation**: Option 2 (bundle). Extend the `POST /content` request to accept an optional `initial_highlights` array. Backend creates the content item and inserts highlights in one transaction. If the user highlights more after saving, those are normal highlights on a saved article — no merge conflict, no edge case. Ephemeral highlight data is discarded from sessionStorage after save.
**Reversibility**: Additive — `initial_highlights` is optional; existing callers unaffected.

**Decision**: Where to route the ephemeral reader — new route or existing `/content/[id]`?
**Options**:
1. New route `/read` — clean separation, ephemeral-specific UI (no archive button, different save affordance)
2. Existing `/content/[id]` with a `?ephemeral=1` flag — reuses everything but requires conditionals throughout
3. New route `/read` that renders the existing `Reader` component with an ephemeral flag prop
**Recommendation**: Option 3 — new `/read` page that mounts the same `Reader` component but passes `ephemeral={true}`. The Reader component already receives a `content` prop object; an ephemeral article just populates that prop from sessionStorage instead of an API call. Add minimal UI differences: "Save to Library" banner replaces the archive/delete menu; no reading-progress ring; save button is prominent.
**Reversibility**: Easy — the `/read` route is additive.

---

## Dependency mapping
- Phase 1 (backend: `initial_highlights`) is independent of Phase 2
- Phase 2 (frontend `/read` page) is independent of Phase 1 but ships together for a complete feature
- Phase 3 (extension UI changes) depends on Phase 2 being deployed

---

## Phases

### Phase 1 — Backend: accept initial_highlights on content create (Priority: P0)

**Goal**: `POST /content` can receive an optional array of highlight ranges to create atomically with the content item.

**Entry criteria**: None.

**Changes**:

1. `content-queue-backend/app/schemas/content.py`: Add `EphemeralHighlight` schema and extend `ContentItemCreate`:
   ```python
   class EphemeralHighlight(BaseModel):
       text: str
       note: str | None = None
       start_offset: int
       end_offset: int
       color: str = "yellow"

   class ContentItemCreate(BaseModel):
       # ... existing fields ...
       initial_highlights: list[EphemeralHighlight] | None = None
   ```

2. `content-queue-backend/app/api/content.py`: In the `POST /content` handler, after the content item is committed, if `initial_highlights` is present, bulk-insert `Highlight` rows with the new `content_item_id` and current user. Wrap in the same transaction so partial failures roll back.

3. Add backend test: POST with `initial_highlights` creates both the content item and the highlight rows atomically. POST with a DB failure mid-highlight rolls back the content item too.

**Exit criteria**:
- [ ] `POST /content` with `initial_highlights` creates both item and highlights in one transaction
- [ ] Highlights appear in `GET /content/{id}/highlights` after save
- [ ] `make lint` passes
- [ ] `pytest tests/test_content_api.py -x -q` passes (add test for atomic creation)

**Estimated scope**: ~30 lines in schemas.py, ~25 lines in content.py, ~20 lines test.

---

### Phase 2 — Frontend: `/read` ephemeral route (Priority: P0)

**Goal**: A page that reads article content from sessionStorage and renders it in the full Reader, with a "Save to Library" affordance and ephemeral highlight support.

**Entry criteria**: Phase 1 backend deployed (or feature-flagged).

**Changes**:

1. `frontend/app/read/page.tsx` (new file): On mount, read `sedi_ephemeral_article` from sessionStorage. Parse JSON into `{ html, title, description, author, thumbnail_url, url, published_date }`. Clear the key immediately after reading (prevent stale re-renders). Construct a fake `ContentItem`-shaped object with a sentinel `id` (e.g., `"ephemeral"`). Render `<EphemeralReader content={...} />`.

2. `frontend/components/EphemeralReader.tsx` (new file): Wraps the existing `Reader` component but:
   - Passes `ephemeral={true}` to suppress the archive/delete menu, reading progress ring, and connection panel trigger
   - Shows a sticky `"Save to Library"` banner at the top (or a floating button)
   - Maintains an ephemeral highlights list in component state (since there's no backend to persist to)
   - On "Save": calls `contentAPI.create({ url, pre_extracted_html: html, ...meta, initial_highlights })` and on success, navigates to `/content/{newId}`

3. `frontend/lib/api.ts`: Extend `contentAPI.create()` to accept and pass `initial_highlights`.

4. `frontend/types/index.ts` or `generated.ts`: Add `EphemeralHighlight` type after regeneration.

**Highlight UX in ephemeral mode**:
- The existing `useHighlights` hook or equivalent runs against a local array instead of the API
- Visual rendering is identical (colored underlines in the reader body)
- On save, the local array is sent as `initial_highlights`
- After navigation to the saved article, highlights are already there — no re-highlighting needed

**Exit criteria**:
- [ ] Opening `/read` with a valid `sedi_ephemeral_article` sessionStorage key renders the article
- [ ] Opening `/read` with no sessionStorage key redirects to `/dashboard`
- [ ] Highlighting text in ephemeral reader shows the color picker and creates a local highlight
- [ ] Clicking "Save to Library" saves the article and navigates to `/content/{id}` with highlights present
- [ ] `npx tsc --noEmit` clean
- [ ] Design audit: Save banner follows button pattern; no raw colors; `rounded-none`

**Estimated scope**: ~120 lines across 2 new files + ~20 lines in api.ts.

---

### Phase 3 — Extension: "Read" button in popup (Priority: P0)

**Goal**: Extension popup shows two buttons: "Save" (existing) and "Read" (new). "Read" extracts the page and opens the ephemeral reader without saving.

**Entry criteria**: Phase 2 deployed.

**Changes**:

1. `extension/popup/popup.html` + `popup.css`: Add a "Read" button alongside the existing "Save" button. Match the sed.i button aesthetic (font-mono, no rounded corners, accent border on hover).

2. `extension/popup/popup.js`:
   - "Read" button click → run extraction (same `extractAndInlineContent()` path)
   - After extraction, write `sedi_ephemeral_article` to sessionStorage of the target app tab (or use `chrome.storage.session` for the handoff if cross-origin)
   - Open `{appUrl}/read` in a new tab
   - **Cross-origin sessionStorage note**: Extensions cannot write directly to another origin's sessionStorage. Use `chrome.tabs.create` then inject a content script that writes the key, OR encode the payload as a URL fragment/hash (for small payloads) OR use `chrome.storage.session` as a relay that the app reads via the extension messaging API.
   - **Recommended relay**: Write payload to `chrome.storage.session` under a key, open the `/read` tab, the `/read` page calls `chrome.runtime.sendMessage` to fetch the payload on mount, clears it after read. This avoids cross-origin sessionStorage restrictions entirely.

3. `extension/background/service_worker.js`: Add a message handler for `{ type: "GET_EPHEMERAL_ARTICLE" }` that reads from `chrome.storage.session` and replies. Clear the key after responding.

4. `frontend/app/read/page.tsx`: On mount, try `chrome.runtime.sendMessage` first (if in extension context), fall back to sessionStorage (for development/direct navigation).

**Exit criteria**:
- [ ] Extension popup shows "Read" and "Save" buttons
- [ ] Clicking "Read" opens a new tab at `/read` and renders the article
- [ ] The `/read` tab is not authenticated — no login required to read (only to save)
- [ ] Clicking "Save" in the `/read` tab saves to library (with highlights if any)

**Estimated scope**: ~60 lines across popup.js, service_worker.js, and page.tsx.

---

## Risks

**Risk**: Extracted HTML with inlined `data:` URIs is large (multi-MB for image-heavy pages)
**Likelihood**: Medium
**Mitigation**: Cap `chrome.storage.session` payload at 5MB (browser limit is ~10MB per key). For oversized payloads, drop images from the ephemeral payload (store only alt text). The Save path still gets images via the normal extension flow.
**Detection**: Log payload size in extension; warn user if images were dropped.

**Risk**: User reads in ephemeral mode, makes many highlights, then loses them (browser crash, closed tab)
**Likelihood**: Low
**Mitigation**: Auto-save ephemeral state to `chrome.storage.session` on each highlight creation, not just on explicit Save.
**Detection**: User feedback.

**Risk**: `chrome.runtime` not available in the `/read` tab (non-extension context)
**Likelihood**: Low — extension opens the tab, so context is extension-initiated
**Mitigation**: sessionStorage fallback already in the plan. Graceful degradation if neither is available: redirect to dashboard with an error message.

---

## Verification

### Automated
- `pytest tests/test_content_api.py` — `initial_highlights` atomic creation test
- `npx tsc --noEmit`
- `npx jest __tests__/components/EphemeralReader.test.tsx` (new test)

### Manual
- Open extension on any article, click "Read" → article renders in `/read` with correct title/body
- Highlight two passages → highlights appear visually
- Click "Save to Library" → navigates to `/content/{id}`, both highlights present
- Highlight a third passage on the now-saved article → normal highlight behavior, no regression
- Open `/read` directly (no sessionStorage) → redirects to `/dashboard`

---

## Open questions
- Should the "Read" experience require the user to be logged in? (Save requires auth; Read does not.)
- Should ephemeral articles be indexable/shareable? (No — they're private and transient.)
- Phase 2: Auto-save ephemeral state to prevent data loss on tab close?
