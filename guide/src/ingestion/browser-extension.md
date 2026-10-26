---
layout: layouts/chapter.njk
title: The browser extension
part: 1
order: 4
summary: How a Chrome or Safari extension extracts an article inside the page you are looking at and sends the finished HTML to the backend.
verified: 2026-10-25
permalink: /ingestion/browser-extension/
---

## In one minute

The extension runs the article extraction in your browser tab instead of on the server. When you click Save, the popup injects Mozilla's Readability library and a script called `content.js` into the page. They run against the page you are already looking at, with your login and any paywall you have already passed, and return the article HTML, the title, author and other metadata. The popup passes that to a background script (the service worker), which sends it to `POST /content` with a Bearer token. The backend sees that the request carries `pre_extracted_html`, skips its own download, does a light clean-up and stores the row as completed.

A second button, Read, uses the same extraction but draws the article in an overlay on the current page and saves nothing. The Safari extension is a copy of the Chrome one built with Xcode. The backend and a Cloudflare Worker in front of it both allow requests from extension origins.

## The problem

**How does sed.i save a page that the server cannot download?**

The obvious design is the one [Saving a URL](/ingestion/saving-a-url/) describes: the user gives a URL, a worker downloads it and runs an extractor ([Extracting an article](/ingestion/extracting-an-article/)). That fails in three common cases.

1. **Paywalls and logins.** The server is anonymous. A page the user can read because they are subscribed returns a login wall or a 403 to the server, so the server saves the wall.
2. **JavaScript-rendered pages.** Many sites ship an empty shell and fill in the article with scripts. A plain HTTP download sees the shell. The browser tab sees the finished page.
3. **Bot blocking.** Some sites refuse requests from data-centre addresses.

The fix is to extract where the content is already correct: in the user's browser tab, after the page has rendered. The cost is a second extraction pipeline in JavaScript, a second set of code that must be kept in step with the server's, and the need for the server to trust HTML that came from a client.

## Mental model

A server-side extractor is a courier who goes to the shop, knocks, and takes whatever the door shows a stranger. The extension is you photocopying the page at your own desk, then handing the copy to the courier. The copy is what you saw, including anything behind your subscription.

| Term | Meaning here |
|---|---|
| Manifest V3 (MV3) | Chrome's extension format. Extension code runs in separate contexts that can only talk through messages. |
| Popup | The small window when you click the toolbar icon. It exists only while open. |
| Service worker | The extension's background script. It has no page, wakes on events and may be shut down when idle, so it keeps state in `chrome.storage.local`. |
| Content script | Code the extension injects into a web page so it can read that page's DOM. |
| Readability | Mozilla's algorithm (used by Firefox Reader View) that scores page elements and returns the main article. Bundled as `extension/lib/Readability.js`. |
| Bearer token | A token sent in an `Authorization: Bearer ...` header. It is attached explicitly by code, never automatically by the browser. |
| Shadow DOM | A part of the page tree whose styles are isolated from the page's CSS. The reader overlay lives in one. |

{% fig "Who talks to whom (Save)" %}
```text
  popup.js            tab (page)              service_worker.js     backend
  --------            ----------              -----------------     -------
  click Save
  executeScript(files: Readability.js) ---->  defines Readability
  executeScript(files: content.js)     ---->  defines window.__sedi...
  executeScript(func: call it)         ---->  clone DOM, Readability,
                                              meta, paywall check,
                                              inline <=15 images
          <---- { html, title, author, ... } ----
  sendMessage({action:'saveContent', payload}) -> handleSave()
                                                  POST /content  --------> row
                                                  <---- 201 + item json ---
          <---- { ok: true, data } ---------------
  button reads "sent"
```
{% endfig %}

## Walkthrough: one click on Save

The example page is an invented article at `https://example.com/espresso`. The service-worker step and the backend step were run for real: the real `service_worker.js` was loaded in Node with stubbed `chrome` and `fetch`, and the real `ContentItemCreate`, `_clean_extension_html` and `verify_csrf` were called in Python. The content script needs a browser DOM and was not run here (no browser DOM library is installed in the repo); its output object is shown from the code's `return` statement with the invented values below, and is labeled illustrative.

### Step 1: the popup opens

`init()` asks the service worker for the stored token with `chrome.runtime.sendMessage({ action: 'getToken' })`. With no token it shows the login form. With one it calls `setupReadyView()`, which reads the active tab's title and URL from `chrome.tabs.query` and runs a tiny injected function that reads `og:` and `meta` tags, so the popup can show a preview in milliseconds before any extraction.

### Step 2: inject and extract

Clicking "Send to sed.i" starts this sequence in `popup/popup.js`:

<!--src extension/popup/popup.js-->
```javascript
    await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      files: ['lib/Readability.js'],
    });
    await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      files: ['content/content.js'],
    });
    const results = await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      func: async () => {
        if (typeof window.__sediExtractAndInlineContent === 'function') {
          return await window.__sediExtractAndInlineContent();
        }
        return { error: 'Content script not loaded.' };
      },
    });
    const extracted = results?.[0]?.result;
    if (!extracted || extracted.error) throw new Error(extracted?.error || 'Extraction failed.');

    const payload = {
      url:              _tab.url,
      html:             extracted.html,
      title:            extracted.title,
```

Three separate injections, on purpose: the first two define things; the third calls the defined function and waits for its result. Safari does not wait for a Promise returned by a `files:` injection, but does wait for one returned by `func:` (see the Safari section).

Inside the tab, `extractAndInlineContent` in `content/content.js` does this, in order:

1. **Wait for the article to exist.** For up to 2 s it waits for publisher-specific selectors (Nature, Springer), then up to 1 s each for generic ones such as `article`. This is the JavaScript-rendered-page fix.
2. **Read the live page.** Metadata (`og:description`, `og:image`, authors from meta tags or JSON-LD, publish date) and the paywall check run on the real DOM, before cloning.
3. **Clone the page.** Readability destroys the DOM it works on, so it gets `document.cloneNode(true)`. The user's page is not touched.
4. **Remove noise from the clone**: `nav`, `footer`, `aside`, and elements whose class or id contains words such as `sidebar`, `related`, `promo`, `comment`, plus small inline-hidden nodes.
5. **Run Readability** with `charThreshold: 20`, `keepClasses: false`.
6. **Remove duplicates** of what the reader shows separately: the `h1` matching the title, a subtitle, the author line, the date, a leading paragraph equal to the description.
7. **Inline images** (Save only): up to 15 images are fetched from the page and replaced by base64 `data:` URIs, skipping images smaller than 80 px. This is so the saved copy does not depend on the publisher's server or on cookies.

The function returns an object of this shape (field names are from the code; the values are illustrative):

{% fig "Return value of extractAndInlineContent (illustrative values)" %}
```json
{
  "title": "How espresso extraction works",
  "byline": "A. Writer",
  "html": "<h1>How espresso ...</h1><p>Espresso forces ...</p>",
  "url": "https://example.com/espresso",
  "wordCount": 12,
  "debugInfo": { "foundSpecific": false, "bodyTextLength": 80 },
  "accessRestricted": false,
  "description": "Why grind size changes the taste.",
  "thumbnail": "",
  "author": "A. Writer",
  "publishedDate": "2026-04-15T00:00:00Z"
}
```
{% endfig %}

The paywall check looks for three signals and returns true on the first it finds: a JSON-LD field `isAccessibleForFree: false`; a `content_tier` meta tag whose value contains `paid`, `premium`, `subscriber` or `metered`; or an element whose class, id or test id contains `paywall` or `subscribe`.

### Step 3: the message to the service worker

The popup builds a payload and sends one message. This is the object, captured by sending it to the real service worker:

{% fig "popup to service worker (captured; values invented)" %}
<!--captured node /tmp/guide_ex14/sw_capture.js-->
```json
{
  "action": "saveContent",
  "payload": {
    "url": "https://example.com/espresso",
    "html": "<h1>How espresso extraction works</h1><p>Espresso forces hot water through coffee.</p>",
    "title": "How espresso extraction works",
    "author": "A. Writer",
    "description": "Why grind size changes the taste.",
    "thumbnail": "",
    "publishedDate": "2026-04-15T00:00:00Z",
    "accessRestricted": false
  }
}
```
{% endfig %}

The popup's field names (`html`, `title`, `publishedDate`) are not the API's. The service worker translates them:

<!--src extension/background/service_worker.js-->
```javascript
async function callSaveApi(base, token, payload) {
  const body = { url: payload.url };
  if (payload.html)             body.pre_extracted_html               = payload.html;
  if (payload.title)            body.pre_extracted_title              = payload.title;
  if (payload.author)           body.pre_extracted_author             = payload.author;
  if (payload.description)      body.pre_extracted_description        = payload.description;
  if (payload.thumbnail)        body.pre_extracted_thumbnail          = payload.thumbnail;
  if (payload.publishedDate)    body.pre_extracted_published_date     = payload.publishedDate;
  if (payload.accessRestricted) body.pre_extracted_access_restricted  = true;

  return fetch(`${base}/content`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
}
```

Empty fields are left out entirely, which is why `thumbnail` and `pre_extracted_access_restricted` are missing from the captured request:

{% fig "service worker to API (captured; the token is replaced by a placeholder)" %}
<!--captured node /tmp/guide_ex14/sw_capture.js-->
```json
{
  "url": "https://api.example.test/content",
  "method": "POST",
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer <access token>"
  },
  "body": {
    "url": "https://example.com/espresso",
    "pre_extracted_html": "<h1>How espresso extraction works</h1><p>Espresso forces hot water through coffee.</p>",
    "pre_extracted_title": "How espresso extraction works",
    "pre_extracted_author": "A. Writer",
    "pre_extracted_description": "Why grind size changes the taste.",
    "pre_extracted_published_date": "2026-04-15T00:00:00Z"
  }
}
```
{% endfig %}

The host is the API base stored in `chrome.storage.local` (a placeholder host here; the extension's built-in default points at production, and a developer can override it by long-pressing the logo for two seconds).

### Step 4: the backend

The request arrives with a Bearer header, so `get_current_active_user` reads the token from it rather than from a cookie. CSRF protection (explained below) lets it through. `create_content_item` in `api/content.py` then does the following.

<!--src content-queue-backend/app/api/content.py-->
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
```

The presence of `pre_extracted_html` is the only thing that selects this path. `ingest_url` normalizes the URL, rejects a duplicate with HTTP 409 (see [Saving a URL](/ingestion/saving-a-url/)), inserts a row with status `pending`, and, because `dispatch_extraction` is false, does not queue the download task.

Then the handler cleans the HTML. `_clean_extension_html` removes any `h1` equal to the title, any `p` equal to the description, and any image whose file name equals the thumbnail's, because the reader shows those separately. Run on the captured body:

{% fig "_clean_extension_html on the captured body (captured)" %}
<!--captured python /tmp/guide_ex14/backend_pure.py-->
```text
parsed fields: ['pre_extracted_author', 'pre_extracted_description',
  'pre_extracted_html', 'pre_extracted_published_date',
  'pre_extracted_title', 'url']
pre_extracted_access_restricted default: False
html in : <h1>How espresso extraction works</h1><p>Espresso forces hot water through coffee.</p>
html out: <p>Espresso forces hot water through coffee.</p>
```
{% endfig %}

The handler then copies title, author, description, thumbnail and published date onto the row, sets `processing_status = "completed"`, stores `word_count` and `reading_time_minutes` (words divided by 200), and, if `pre_extracted_access_restricted` is true, sets `processing_error` to "Content appears restricted by a paywall or source access controls". It commits and queues two Celery tasks: `extract_metadata` and `generate_embedding`.

The database step was inferred from the code and not executed: running it needs a database to write to, and none was created for this guide. The row ends up with `submitted_via = 'extension'`, `status = 'completed'`, the cleaned HTML in `full_text`, and the 1-line paragraph counted as 6 words, so `reading_time_minutes = 1`.

Even for an extension save, `extract_metadata` downloads the page server-side with the size-capped fetch (see [Saving a URL](/ingestion/saving-a-url/)). It fills missing metadata and compares the downloaded text with the extension's HTML to flag a likely truncated extraction (`_detect_limited_extraction_reason`). It does not replace `full_text`. If that fetch fails, it logs a warning and moves on.

### Step 5: the answer

The service worker returns `{ ok: true, data }` to the popup, which changes the button to "sent". A 409 from the backend becomes `{ ok: false, error: 'ALREADY_SAVED', code: 'ALREADY_SAVED', isArchived }`. The overlay reads `code` and shows "Already in library". The popup does not: it passes `error` to `parseApiError`, which cannot parse the bare string and returns it unchanged (checked by running the function), so the popup shows the literal text `ALREADY_SAVED`. The answer to the popup on success:

{% fig "Reply to the popup (captured)" %}
<!--captured node /tmp/guide_ex14/sw_capture.js-->
```json
{"ok":true,"data":{"id":"ITEM_ID","processing_status":"completed"}}
```
{% endfig %}

## How it is built here

| File | Role | Size |
|---|---|---|
| `extension/manifest.json` | MV3 manifest: permissions `activeTab`, `scripting`, `storage`; no host permissions. | 23 lines |
| `extension/popup/popup.js` | Popup controller: login, ready view, Read, Save. | 486 lines |
| `extension/background/service_worker.js` | Token storage, refresh, the API call, the message router. | 191 lines |
| `extension/content/content.js` | Extraction in the page. Exposes `window.__sediExtractAndInlineContent`. | 622 lines |
| `extension/content/reader-overlay.js` | The in-page reader, in a shadow DOM. | 882 lines |
| `extension/content/vendor/purify.min.js` | DOMPurify 3.4.12, vendored. | 29 KB |
| `extension/lib/Readability.js` | Mozilla Readability, bundled. | 2,812 lines |
| `cloudflare-worker/worker.js` | Edge proxy that answers CORS for the API. | 115 lines |

The manifest has no `host_permissions`. The extension reads a page only through `activeTab`, which grants access to the tab you invoked it on, at the time you click. It cannot read other tabs and cannot read a page in the background.

### The three flows

| Flow | What happens | Where the code is |
|---|---|---|
| Save | Extract with images inlined, send to `POST /content`. | `popup.js` Save handler, `service_worker.js` |
| Read without saving | Extract with image inlining skipped, draw an overlay on the page, save nothing unless the overlay's own Save button is pressed. | `popup.js` Read handler, `reader-overlay.js` |
| Highlight before saving | The frontend page `/read` keeps highlights in memory and sends them as `initial_highlights` with the save. | `frontend/app/read/page.tsx`, `frontend/components/EphemeralReader.tsx` |

**Read.** The popup sets `window.__SEDI_SKIP_IMAGE_INLINE = true` in the tab so extraction does not wait or fetch images (the page already shows them), runs the same three-step extraction, reads how far down the page you were, stores the article in `window.__sediArticle__` through an injected function, then injects `purify.min.js` and `reader-overlay.js` and closes the popup.

The overlay builds a `div` with `attachShadow({ mode: 'open' })`, appends it to the page body, and adds one style rule that hides every other child of `body`. Pressing Esc removes the host, which is instant. Because styles inside a shadow root cannot be matched by page CSS, the reader looks the same on every site. All sizes are in pixels because `rem` units resolve against the page's own root font size even inside a shadow root.

The overlay has its own Save button, which sends the same `saveContent` message to the service worker. It does not forward `accessRestricted`, so a save from the overlay never flags a paywalled page; the popup's Save does.

**Highlight before saving.** This flow is implemented in the web app, not in the extension. `/read` takes an article from the URL hash or from `sessionStorage`, and `EphemeralReader` collects highlights (`text`, `start_offset`, `end_offset`, `color`, optional `note`) in a React ref with no API call. On Save it sends them in the `initial_highlights` array. The backend then creates one `Highlight` row per entry. In the current extension code nothing opens `/read` and the overlay has no highlighting, so this flow is reachable only by passing an article to that page directly.

### Message protocol

All popup to service worker traffic is `chrome.runtime.sendMessage({ action, ...fields })`. The service worker returns `true` from the listener to keep the reply channel open for an asynchronous answer.

| action | Fields | Reply |
|---|---|---|
| `saveContent` | `payload` | `{ ok: true, data }` or `{ ok: false, error, code, isArchived }` |
| `getToken` | none | `{ token }` (null when logged out) |
| `setTokenPair` | `token`, `refreshToken` | `{ ok: true }` |
| `setToken` | `token` | `{ ok: true }` (older path without a refresh token) |
| `clearToken` | none | `{ ok: true }` |
| `logout` | none | `{ ok: true }`; also calls `POST /auth/logout` with the refresh token, best effort |
| `getApiBase` / `setApiBase` | `apiBase` | `{ apiBase }` / `{ ok: true }` |
| `getFrontendBase` | none | `{ frontendBase }` |

Between the popup and the page there are no messages, only `executeScript` calls and three window globals: `__SEDI_SKIP_IMAGE_INLINE` (a flag), `__sediExtractAndInlineContent` (the extractor) and `__sediArticle__` (the article for the overlay).

### The auth model

The user types an email and password into the popup. `popup.js` itself (not the service worker) posts them to `/auth/login` as a form and gets back an access token (24 hours) and a refresh token (90 days). It hands both to the service worker with `setTokenPair`, which saves them in `chrome.storage.local`.

On every save the service worker reads the token and sends it as a Bearer header. If the API answers 401 it exchanges the refresh token for a new pair and retries once:

<!--src extension/background/service_worker.js-->
```javascript
  let response = await callSaveApi(base, token, payload);

  // Silent refresh + single retry on 401
  if (response.status === 401) {
    const newToken = await silentRefresh(base);
    response = await callSaveApi(base, newToken, payload);
  }
```

`silentRefresh` stores a single in-flight promise so two concurrent 401s share one refresh and the rotated refresh token is not used twice. If refresh fails, both tokens are cleared and the popup shows the login form.

**Why CSRF does not apply.** Cross-site request forgery works because browsers attach cookies automatically: a hostile page can make your browser send a request that carries your cookie. A Bearer header is attached only by code that holds the token, so a hostile page cannot cause one. The server's check reflects that:

<!--src content-queue-backend/app/core/auth_cookies.py-->
```python
    if request.method not in _CSRF_PROTECTED_METHODS:
        return True
    if request.url.path in _CSRF_EXEMPT_PATHS:
        return True
    if request.headers.get("authorization"):
        return True
    if ACCESS_TOKEN_COOKIE not in request.cookies:
        return True

    cookie_value = request.cookies.get(CSRF_COOKIE)
    header_value = request.headers.get(CSRF_HEADER)
    return (
        bool(cookie_value)
        and bool(header_value)
        and secrets.compare_digest(cookie_value, header_value)
    )
```

Captured results of calling `verify_csrf` on constructed requests:

{% fig "verify_csrf on four requests (captured)" %}
<!--captured python /tmp/guide_ex14/backend_pure.py-->
```text
Bearer header, no cookie    -> True
auth cookie, no csrf header -> False
auth cookie + matching hdr  -> True
auth cookie + Bearer header -> True
```
{% endfig %}

The web app authenticates with a cookie, so it must send the matching `X-CSRF-Token` header (row 3). The extension has no cookie and sends a Bearer header (row 1), so the check does not apply to it.

### The DOMPurify step

The page HTML that Readability returns came from a third-party site, so it can contain scripts, event-handler attributes and other active content. The overlay renders it into the user's current page, so it must be cleaned first. `reader-overlay.js` calls `window.DOMPurify.sanitize(article.html)` before assigning it to the DOM, and the popup injects `content/vendor/purify.min.js` immediately before the overlay for that reason.

<!--src extension/content/reader-overlay.js-->
```javascript
  const sanitizedHtml = (typeof window.DOMPurify !== 'undefined')
    ? window.DOMPurify.sanitize(article.html)
    : article.html;
  const _tmp = document.createElement('div');
  _tmp.innerHTML = sanitizedHtml;
```

DOMPurify runs only in the overlay. The Save path sends the HTML as extracted. The backend runs it through BeautifulSoup in `_clean_extension_html`, which does not sanitize, and the main web reader sanitizes at render time (`sanitizeArticleHtml` in `frontend/lib/bionicReading.ts`). So the stored HTML is not guaranteed clean; the guarantee is that every reader cleans it before showing it.

### CORS and the Cloudflare Worker

Cross-origin rules (CORS) say a page or extension at one origin may read an answer from another only if the server's headers allow it. The extension has no host permissions, so its calls to the API are cross-origin calls from an origin that looks like `chrome-extension://<id>` or `safari-web-extension://<id>`. Two places allow that origin.

The FastAPI app uses `CORSMiddleware` with `allow_origin_regex=r"(chrome|moz)-extension://.*|safari-web-extension://.*"`. In front of it, the Cloudflare Worker answers the browser's preflight (`OPTIONS`) request itself, strips the `Origin` header before forwarding to the origin host (a comment in the Worker says the host's edge blocks unknown origins), and adds CORS headers to every response:

<!--src cloudflare-worker/worker.js-->
```javascript
const ALLOWED_ORIGINS = new Set([
  "http://localhost:3000",
  ...
  "https://content-queue.vercel.app",
  "https://claude.ai",
  "https://claude.com",
]);

const ALLOWED_ORIGIN_REGEX = /^(chrome|moz)-extension:\/\/.*|^safari-web-extension:\/\/.*/;
```

The Worker also lists the web app and `claude.ai` (for the MCP connector, see [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/)). The extension IDs are not pinned: any installed extension matches the regex.

### Safari

`safari-extension/sed.i/sed.i Extension/Resources/` holds a copy of `extension/`, built by an Xcode project and run through a small macOS app. Safari 15.4 and later runs the same MV3 code with `chrome.*` aliased to `browser.*`, so `manifest.json`, `popup.js`, `service_worker.js`, `Readability.js` and DOMPurify are byte-identical to the Chrome copy (checked with `diff`). Three Safari behaviors shaped the code, from the 2026-05-12 changelog:

- **`executeScript` and Promises.** Safari does not wait for a Promise returned from a `files:` injection. `content.js` therefore defines a global function instead of returning a value, and the popup calls it in a separate `func:` injection, which Safari does wait for. Chrome accepts both, so the single pattern is used in both browsers.
- **Closing the reader.** Putting the page's `body` back after swapping it out took about 3 s in Safari. A shadow-DOM overlay removed with `host.remove()` is instant, so both browsers use it.
- **`rem` units.** Inside a shadow root `rem` still follows the page's root font size, so the overlay uses `px`.

`make safari-sync` runs `rsync` to copy the Resources folder into a separate repository checked out under the home directory (`sedi-safari`), where the app is built. Build and signing happen in Xcode with a developer team set locally.

The two copies have drifted. `content.js` differs in 25 lines and `reader-overlay.js` in 65 lines (measured with `diff`). The Safari `content.js` lacks the content-based subtitle removal that the Chrome one has, and the overlay differs in progress-bar rendering, the zoom cursor and which object receives the Esc key listener.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Extract in the tab with Readability | Send the URL only; send the whole page HTML and extract on the server | Only the tab has the logged-in, rendered page. Sending the whole page would work too and keep one extractor, but costs more bandwidth and server memory (trafilatura and BeautifulSoup peak at about 37 times the page size, per `core/fetch.py`). |
| Readability in the extension, trafilatura on the server | One extractor for both | Readability is the established browser-side choice. Two extractors can give different results for the same page, and the cleaning rules are duplicated by hand ("Mirrors the approach in the backend's `xml_to_html()`"). |
| `activeTab` and `scripting`, injected on demand | A content script on every page with host permissions | Reads a page only when the user clicks. The store review is simpler and the extension cannot read pages in the background. |
| Bearer token in `chrome.storage.local` | Reuse the web app's cookie | The extension runs on other sites and cannot share the app's httpOnly cookie. A Bearer token also makes CSRF irrelevant. |
| Access token 24 h plus refresh token 90 d | Long-lived single token | A stolen access token expires; refresh tokens rotate and can be revoked server-side (see [Auth and security](/platform/auth-and-security/)). |
| Shadow-DOM overlay for reading | Open a new tab; iframe; swap the body | No navigation, no cross-origin copy of a large HTML string, page CSS cannot leak in. Swapping the body was slow in Safari. |
| Inline up to 15 images as data URIs on Save | Keep remote image URLs | The saved copy still shows images when the publisher's server blocks hotlinks or needs cookies. The price is a larger JSON body and database row. |
| `pre_extracted_html` as the switch | A separate endpoint | One endpoint and one duplicate check serve all clients. The downside is that the same field is accepted from any client, not only the extension. |

## How it fails

| Failure | Cause | Result | Handling |
|---|---|---|---|
| "Extraction failed" | Readability returns nothing (page is a feed, a login wall, or not article-shaped) | `{ error: 'Could not extract article content from this page.' }` | Message shown in the popup |
| Unsupported page | `chrome://`, file or PDF viewer URLs | The popup checks that the scheme is http or https | Error "Save only works on http/https pages" |
| Not logged in or expired | Access token expired, refresh token expired or revoked | 401, refresh fails, tokens cleared | The popup shows the login form |
| Already saved | Same normalized URL in the library | HTTP 409 with a JSON string in `detail` | The overlay says "Already in library"; the popup shows the raw text `ALREADY_SAVED` (its message parser never sees the JSON) |
| Paywalled page saved | The user sees the full article but a signal fired, or the signal is missing | Flag set, `processing_error` text on the row | Reader shows a restricted-content signal; missing signals are not caught |
| Safari "Extraction failed" on every page | The `files:` Promise was not awaited | Nothing extracted | Fixed on 2026-05-12 by the global-function pattern |
| Reader close took about 3 s in Safari | Restoring a large detached `body` | Visible freeze | Fixed by the shadow-DOM overlay |
| Reader typography varied by site | `rem` units follow the page's root font size | Wrong sizes | Fixed by using px |
| CSRF gaps | Cookie auth was added for the web app | Mutating cookie requests need a matching header | `verify_csrf`; the extension's Bearer path is exempt by design |

## What is weak or missing

Ranked by how likely each is to matter.

1. **The Safari copy has drifted from Chrome.** `make safari-sync` does not copy `extension/` into the Safari folder at all: it copies the Safari Resources folder onward to another repository. Nothing in the repo keeps the two in step, and they differ today (25 and 65 lines). Fix: make one folder the source and generate the other in the build.
2. **Stored HTML is not sanitized at ingest.** `POST /content` accepts any HTML in `pre_extracted_html`, from any authenticated client, with no sanitization and no size limit that the code enforces. Readers sanitize on render, so display is protected, but other consumers of `full_text` (search indexing, the LLM tasks) receive it raw. Fix: sanitize on the server and cap the body size.
3. **The `/read` highlight flow is not connected.** The web page exists and works with data it is given, but the extension never opens it. Either the extension should hand the article over, or the docs should stop describing this as an extension flow.
4. **Extension IDs are not pinned.** The CORS regex accepts any extension origin, in both the API and the Worker. Requests still need a valid Bearer token, so this widens the surface rather than opening it; pinning the published extension IDs would close it.
5. **Tokens sit in `chrome.storage.local` in clear text.** That is the platform norm for extensions, and only the extension itself can read it, but a browser profile compromise exposes a 90-day refresh token.
6. **Two extractors with duplicated cleaning rules** that must be kept in step by hand, with no test comparing their outputs on the same page.
7. **No automated tests in the repository for `content.js`** (inferred from the file layout: the extension folder has no test files).
8. **The overlay's DOMPurify fallback is weaker than its comment says.** If DOMPurify were missing, the code assigns the raw HTML to an element's `innerHTML` first and filters afterwards. The popup always injects the library first, so this should not happen; the safe fallback is to refuse to render.
9. **The overlay's Save drops the paywall flag**, so a paywalled page saved from the overlay is not marked.
10. **The popup shows `ALREADY_SAVED` for a duplicate.** The service worker already returns the right code and archived flag; the popup should read `code` as the overlay does.

{% say %}
Some pages can't be downloaded by a server, because they sit behind a login or only render in a browser. So the extension does the extraction inside the tab. When I click Save, the popup injects Mozilla's Readability and my content script into the page, which clones the document, strips the junk, pulls out the article and its metadata, and inlines up to fifteen images. The popup sends that to the background service worker, which posts it to the API as pre-extracted HTML with a Bearer token. The backend sees that field, skips its own download, cleans out the title and description duplicates, and stores the row as completed. A Bearer token has to be attached by code, so CSRF doesn't apply to it. Read mode uses the same extraction but shows it in a shadow-DOM overlay and cleans it with DOMPurify. The Safari extension is a copy built in Xcode, and it has drifted from the Chrome one.
{% endsay %}

## Check yourself

{% qa "Why can the extension save a paywalled article that the server's own extractor cannot?" %}
Because the extraction runs in the user's tab, on the page as the browser rendered it with the user's session. The server downloads anonymously and gets the login wall. The extension sends the finished HTML and metadata, and the backend skips its own download when `pre_extracted_html` is present.
{% endqa %}

{% qa "The popup sends `html` and `publishedDate`. What does the backend receive, and where is that translated?" %}
`pre_extracted_html` and `pre_extracted_published_date`. `callSaveApi` in `service_worker.js` maps the popup's names to the API's and leaves out empty fields. That is why the captured request had no `thumbnail` and no `pre_extracted_access_restricted`.
{% endqa %}

{% qa "A cross-site page tries to forge a save request in the user's browser. Why does it fail against the extension's path but would matter for the web app?" %}
The web app authenticates with a cookie the browser attaches automatically, so a hostile page could trigger a request that carries it, which is why cookie requests must also send a matching `X-CSRF-Token`. The extension authenticates with an `Authorization` header that only code holding the token can add, so there is nothing for the hostile page to ride on, and `verify_csrf` passes any request that has that header.
{% endqa %}

{% qa "Why does the popup use three separate `executeScript` calls instead of one?" %}
Safari does not wait for a Promise returned by a `files:` injection, but does wait for one returned by a `func:` injection. So the two files only define things (Readability, then `content.js`, which sets a global), and a third `func:` call invokes the global and returns its result. Chrome would accept a single call, but one code path works in both.
{% endqa %}

{% qa "Where is the article HTML sanitized, and where is it not?" %}
It is sanitized by DOMPurify in the reader overlay before display and by `sanitizeArticleHtml` in the web reader at render. It is not sanitized when saved: the extension sends it raw and the backend only parses it with BeautifulSoup to remove duplicated title, description and thumbnail. Every display path cleans it, but other consumers of the stored text see the raw HTML.
{% endqa %}

{% qa "You change `content.js` and run `make safari-sync`. Does Safari get the change?" %}
Not from this repository's `extension/` folder. The Makefile target copies the Safari Resources folder to the separate `sedi-safari` checkout; it never copies `extension/` into the Safari folder. The two copies of `content.js` already differ in 25 lines. You would have to copy the change into the Safari Resources folder yourself, then sync and rebuild in Xcode.
{% endqa %}
