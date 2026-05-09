# Browser Extension Architecture

Everything the sed.i extension does — from clicking the icon to reading an article before saving.

---

## Files

```
extension/
├── manifest.json              Chrome extension manifest (MV3)
├── background/
│   └── service_worker.js      Auth + API calls (runs in background, no DOM access)
├── popup/
│   ├── popup.html             The popup UI
│   ├── popup.js               Popup controller (UI state machine)
│   └── popup.css
├── content/
│   └── content.js             Injected into the active tab to extract article content
└── lib/
    └── Readability.js          Mozilla's Readability library (bundled)
```

**Chrome MV3 constraint:** The service worker has no access to `localStorage`, the DOM, or `sessionStorage`. It only has `chrome.storage.local` (persistent) and can make `fetch` calls. The popup and content scripts are separate execution contexts — they communicate with the service worker via `chrome.runtime.sendMessage`.

---

## Flow 1 — Save an article

The standard flow: user is on a page they want to save to their library.

```
User clicks icon
    ↓
popup.js: init()
    ↓
service_worker: getToken
    ↓ (token exists)
setupReadyView() — shows title, Save button
    ↓
User clicks "Save"
    ↓
popup.js: chrome.scripting.executeScript
  → injects lib/Readability.js + content/content.js into the active tab
    ↓
content.js runs in the tab's DOM context:
  1. Waits for article selectors to appear (up to 3s)
  2. Clones the document (doesn't mutate live page)
  3. Removes noise: nav, footer, ads, hidden elements
  4. Runs Readability.parse() on the clone
  5. Extracts OG metadata (description, thumbnail, author, published date)
  6. Detects paywall (JSON-LD isAccessibleForFree, content_tier meta, DOM selectors)
  7. Processes images: resolves srcsets, fetches up to 15 images as base64 data URIs
  8. Returns { html, title, author, description, thumbnail, publishedDate, wordCount, accessRestricted }
    ↓
popup.js receives result → shows preview screen
  - word count signal (green/yellow/red)
  - reading time estimate
  - author, published date
  - "subscriber content" signal if foundSpecific selector matched
  - scrollable 800-char text snippet
    ↓
User clicks "Confirm Save"
    ↓
popup.js: sendMessage('saveContent', { payload })
    ↓
service_worker: handleSave(payload)
  - reads token + apiBase from chrome.storage.local
  - builds POST body: url + all pre_extracted_* fields
  - POST /content with Bearer token
  - returns { ok: true, data } or { ok: false, error }
    ↓
popup.js: shows result view
  - "Title saved to your queue."
  - "Open sed.i →" link to /dashboard
```

### What the backend receives

```json
{
  "url": "https://example.com/article",
  "pre_extracted_html": "<h2>...</h2><p>...</p>",
  "pre_extracted_title": "Article Title",
  "pre_extracted_author": "Jane Smith",
  "pre_extracted_description": "A summary of the article.",
  "pre_extracted_thumbnail": "https://example.com/og.jpg",
  "pre_extracted_published_date": "2026-04-15T00:00:00Z",
  "pre_extracted_access_restricted": false
}
```

The backend detects `pre_extracted_html` → skips its own fetch/trafilatura pipeline. The HTML is cleaned (`_clean_extension_html`) and stored as `full_text`.

---

## Flow 2 — Read without saving (ephemeral reader)

The "read first, save later" flow. **This requires the extension to write to the `/read` tab's `sessionStorage`** — which the extension cannot do directly (service worker has no DOM, popup is a different origin).

> **Current state:** The frontend `/read` route and `EphemeralReader` component are built and ready. The extension side (the "Read" button in popup.js and the `sessionStorage` write) has not been added yet. The section below describes the intended design.

### Intended flow

```
User clicks "Read" button in popup
    ↓
popup.js: chrome.scripting.executeScript
  → injects content.js into the active tab (same extraction as Save)
    ↓
content.js returns { html, title, author, … }
    ↓
popup.js: opens new tab: chrome.tabs.create({ url: frontendBase + "/read" })
    ↓
popup.js: waits for the new tab to finish loading
  (chrome.tabs.onUpdated until status === "complete")
    ↓
popup.js: chrome.scripting.executeScript on the NEW tab:
  → injects a tiny script that writes to sessionStorage:
    sessionStorage.setItem("sedi_ephemeral_article", JSON.stringify({
      url, html, title, author, description, thumbnail, publishedDate
    }))
    ↓
frontend /read page: reads sessionStorage on mount → renders EphemeralReader
```

### Why sessionStorage and not a URL parameter or chrome.storage?

- **URL params:** HTML blobs are often 100KB+. URLs have a ~2KB limit.
- `chrome.storage.local` is accessible from the extension but not from the frontend web app (different origin).
- `sessionStorage` is scoped to the tab and origin. The extension can write to it by injecting a script into the tab with `chrome.scripting.executeScript`. The frontend reads it on mount.
- Session storage clears when the tab closes — no cleanup needed.

---

## Flow 3 — Highlight before saving

Once the `/read` route is wired up, the user can highlight text while reading the ephemeral article. Here's how those highlights survive the save:

```
User reading at /read (article not yet saved)
    ↓
User selects text → HighlightToolbar appears
    ↓
User clicks a color swatch
    ↓
EphemeralReader's onHighlightCreate callback fires
  → pushes { text, start_offset, end_offset, color } into ephemeralHighlights.current (a ref)
  → NO API call — the article doesn't exist in the DB yet
    ↓
[User continues reading, makes more highlights — all accumulate in the ref]
    ↓
User clicks "Save to Library"
    ↓
EphemeralReader: calls contentAPI.create({
  url,
  pre_extracted_html: html,
  pre_extracted_title: title,
  ...,
  initial_highlights: ephemeralHighlights.current   ← all captured highlights
})
    ↓
Backend: POST /content
  1. Creates ContentItem row (same as normal save)
  2. Loops over initial_highlights → creates one Highlight row per entry
  3. Both in the same DB transaction (atomic)
    ↓
Backend returns { id: "new-article-uuid", … }
    ↓
EphemeralReader: sessionStorage.removeItem("sedi_ephemeral_article")
  → redirects to /content/{id}
    ↓
Normal reader — highlights are already in the DB, show immediately
```

### What if the user highlights after saving?

Once at `/content/{id}`, it's a normal saved article. The `HighlightToolbar` calls `POST /content/{id}/highlights` directly. The ephemeral path is gone.

### What if the user saves without any highlights?

`initial_highlights` is `undefined` (not sent). The backend creates only the `ContentItem` row. Identical to a normal save.

---

## Auth model

The extension stores the JWT in `chrome.storage.local` (persisted across browser restarts). The service worker reads it on every API call.

- **Login:** user enters email + password in popup → extension POSTs to `/auth/login` directly → stores `access_token`.
- **Logout:** `clearToken` message → sets token to `null` in storage.
- **Expiry:** if the backend returns 401, the API call fails. The popup shows an error. The user must log in again (the extension doesn't do silent refresh).
- **Custom API URL:** a hidden dev panel (long-press the logo for 2 seconds) lets you point the extension at a local backend. Stored in `chrome.storage.local` as `apiBase`.

---

## Content extraction deep dive

`content/content.js` runs inside the **live tab** with full DOM access. Key steps:

### 1. Wait for content to render

For paywalled/JS-rendered sites, the article body may not be in the initial HTML. The script waits up to 3 seconds for publisher-specific selectors (Nature, Springer article bodies) to appear, then falls back to generic ones (`article`, `.article-content`, etc.).

### 2. Clone before mutating

Readability.js is destructive — it removes large portions of the DOM. The script clones the full document first so the live page is never touched.

### 3. Noise removal (before Readability)

Removed from the clone before Readability runs:
- Structural noise: `nav`, `footer`, `aside`
- Class-based patterns: `[class*="related"]`, `[class*="sidebar"]`, `[class*="newsletter"]`, ad selectors
- Hidden elements with inline `display:none` or `visibility:hidden` (small ones only — collapsed tabs with >50 words are kept)

### 4. Readability

Mozilla's Readability library extracts the main article content. Returns `{ title, byline, content }`.

### 5. Post-extraction cleanup

After Readability, the extracted HTML is cleaned further to avoid duplication in the reader:
- **Thumbnail**: removes images whose filename stem matches the og:image stem (handles CDN size variants)
- **Description**: removes leading paragraphs that match the OG description
- **Author**: removes small elements whose text matches the author name
- **Published date**: removes small elements that look like the publication date
- **h1**: if the article's `<h1>` matches the page title, it's removed (the reader renders the title in its own header)

### 6. Image inlining

Up to 15 images are fetched as base64 data URIs and embedded directly in the HTML. This ensures images display in the reader even if the original CDN has CORS restrictions or requires auth cookies. Images smaller than 80px (icons, tracking pixels) are skipped.

### 7. Paywall detection

Runs on the live DOM (before cloning) using three signals:
1. **JSON-LD**: `isAccessibleForFree: false` in structured data
2. **content_tier meta tag**: `paid`, `premium`, `subscriber`, `metered`
3. **DOM selectors**: `[class*="paywall"]`, `[id*="paywall"]`, `[data-testid*="subscribe"]`

If detected, `accessRestricted: true` is sent with the payload. The backend stores it as `pre_extracted_access_restricted`. The preview screen shows a red "access restricted" signal.

---

## Message protocol

All popup↔service-worker communication goes through `chrome.runtime.sendMessage`:

| action | direction | payload | response |
|---|---|---|---|
| `getToken` | popup → sw | — | `{ token }` |
| `setToken` | popup → sw | `{ token }` | `{ ok }` |
| `clearToken` | popup → sw | — | `{ ok }` |
| `getApiBase` | popup → sw | — | `{ apiBase }` |
| `setApiBase` | popup → sw | `{ apiBase }` | `{ ok }` |
| `saveContent` | popup → sw | `{ payload }` | `{ ok, data }` or `{ ok: false, error }` |

The service worker returns `true` from all handlers to indicate the response is asynchronous.
