---
layout: layouts/chapter.njk
title: The frontend
part: 4
order: 4
summary: How the Next.js app talks to the API, keeps state, turns a text selection into a stored highlight, loads long lists a page at a time, and handles errors.
verified: 2026-10-25
permalink: /platform/frontend/
---

## In one minute

The frontend is a Next.js 16 app (React 19, TypeScript, Tailwind v4) in `frontend/`. Almost every page is a client component: it loads in the browser, then fetches its data from the FastAPI backend through one function, `fetchWithAuth` in `frontend/lib/api.ts`. That function sends the login cookies with every request, adds a CSRF header on requests that change data, and on a 401 response tries one token refresh before giving up. Shared state lives in five small React contexts (user, list counts, reading settings, theme, music player), and errors are shown inline next to the thing that failed, never as pop-up toasts.

The reader turns a text selection into a highlight by counting characters: it walks the article's text nodes in order and sums their lengths up to the selection boundary. Those two numbers (start and end offset) and the selected text are posted to the API. On reload the same walk runs in reverse: highlights come back from the API and a renderer wraps the characters between the two offsets in `<span>` elements. Lists of more than 50 articles load 50 at a time as you scroll. Four colour themes are CSS variables switched by a class on the `<html>` element.

## The problem

**How does a browser app that is mostly rendered on the client stay consistent with a server that owns the data, and how does it re-find a highlight inside an article a week later?**

The simple design is a server-rendered site: every click is a page load, the server draws the HTML, and there is nothing to keep consistent. That fails for a reader. Selecting text, picking a colour and seeing it appear must take milliseconds, a note editor must not lose the half-typed note when something refreshes, and a queue with hundreds of items needs filtering without a round trip. So the browser holds a working copy of the data and edits it immediately, and the cost is the problems this chapter covers: expired sessions in the middle of an action, a request that fails after the screen already changed, two responses arriving out of order, and a stored highlight that must still point at the right words when the article is drawn again.

The highlight problem is the least obvious. The article is an HTML string. A highlight must survive being stored in a database and drawn again in a different layout, a different font size, or with bionic reading turned on. Storing a DOM node is impossible, and storing an HTML fragment would break whenever the markup changes. Storing two integers, "characters 37 to 59 of the article's text", is stable across all of those, at the price that the number is only meaningful if the same text is counted the same way every time. That price is the weak point of the design and is examined below.

## Mental model

Think of the app as a clerk with a notebook standing between the user and a filing cabinet in another building (the API). The clerk keeps a copy of whatever the user is looking at in the notebook, changes the notebook first so the user sees the result at once, and then phones the cabinet. If the phone call fails, the clerk crosses out the change and tells the user next to the item that it did not work. The clerk also carries a badge (the login cookie) on every call, and when the badge is rejected, gets one new badge and repeats the call exactly once.

| Term | Meaning here |
|---|---|
| Client component | A React component marked `"use client"`: it runs in the browser and can hold state and call the API. Nearly every page here is one. |
| Context | React's way of sharing state with all components below a provider without passing props. |
| httpOnly cookie | A cookie the browser sends automatically but JavaScript cannot read. The access and refresh tokens are these. |
| CSRF token | A random value in a readable cookie that the app copies into a header on requests that change data; the server rejects a change request whose header does not match. See [Auth and security](/platform/auth-and-security/). |
| Optimistic update | Change the screen first, call the API second, undo the change if the call fails. |
| Offset | A character position counted from the start of the article's text. A highlight stores a start and an end offset. |
| Text node | The DOM node that holds a run of plain text. `<p>a <b>b</b> c</p>` has three: `a `, `b` and ` c`. |

{% fig "Where the pieces sit" %}
```text
browser
  app/ pages (client components)
    |  call
    v
  lib/api.ts  fetchWithAuth
    |  cookies + CSRF header + one refresh retry
    v
  FastAPI backend (see 4.3, 4.5)

  contexts/  Auth, Lists, ReadingSettings, Theme, Player
  hooks/     paging, hotkeys, polling, confirm, tags, TTS
  components/ Reader, ReaderArticle, HighlightRenderer, ...
```
{% endfig %}

## Walkthrough: selecting a sentence in the reader

The input is one article already saved and open at `/content/<id>`. Its `full_text` field (the extracted article as HTML, served by `GET /content/{id}/full`) is, in shape:

{% fig "The article HTML (shape only)" %}
```html
<h2>Grind size</h2>
<p>A finer grind exposes more surface area. <b>Too fine</b> and the shot runs slow.</p>
```
{% endfig %}

The user drags across "surface area. Too fine" and releases the mouse.

### Step 1: the selection events fire

`ReaderArticle` registers three document-level listeners in one effect: `selectionchange`, `mouseup` and `touchend`, all pointing at the same handler. The handler waits 100 ms (so a drag that is still changing does not recompute on every pixel) and then asks the browser for `window.getSelection()`. If the selection is empty it clears any toolbar, or, if the click landed on an existing highlight, reopens the toolbar for that highlight.

### Step 2: the selection becomes two numbers

For a non-empty selection the handler calls `getTextOffsets()`. It finds the container `#article-content`, then counts the characters that come before the selection start and the selection end, using a tree walker over text nodes:

{% fig "components/ReaderArticle.tsx: offset of a point inside the article" %}
<!--src frontend/components/ReaderArticle.tsx#1032-1048-->
```typescript
const calculateOffset = (node: Node, offsetInNode: number): number => {
  const walker = document.createTreeWalker(
    contentEl,
    NodeFilter.SHOW_TEXT,
    walkerFilter,
  );
  let totalOffset = 0;
  let currentNode: Node | null;

  while ((currentNode = walker.nextNode())) {
    if (currentNode === node) {
      return totalOffset + offsetInNode;
    }
    totalOffset += (currentNode.textContent || "").length;
  }
  return -1;
};
```
{% endfig %}

The browser tells the code two things about each end of the selection: which text node it is in (`range.startContainer`) and how many characters into that node (`range.startOffset`). The walker visits every text node in document order and adds up their lengths until it reaches that node. `walkerFilter` skips text inside `.heading-anchor` links, which are the small "#" links added to headings; they hold no text of their own, so the filter is a safeguard.

To check the arithmetic on the sample HTML, the same loop was run in jsdom (a DOM implementation for Node), outside the app. This is a re-implementation of the loop above, run on the sample; it is captured output, not the app's own run:

{% fig "Captured: offsets for the sample selection (re-implemented loop in jsdom)" %}
```text
text nodes: ["Grind size","A finer grind exposes more surface area. ",
             "Too fine"," and the shot runs slow."]
selected:    "surface area. Too fine"
start_offset: 37   end_offset: 59
joined.slice: "surface area. Too fine"
```
{% endfig %}

The heading counts: "Grind size" is 10 characters, "A finer grind exposes more " is 27, so the selection starts at 10 + 27 = 37 and is 22 characters long. The offsets are positions in the concatenation of all text nodes, not in the HTML string. Tags contribute nothing, which is why the same two numbers survive a change of markup. They are also not positions in `full_text` itself, even though the field is called that: `full_text` holds HTML, and the offsets ignore its tags.

### Step 3: the toolbar appears

The handler stores `{ text, startOffset, endOffset, position }` in component state, and `HighlightToolbar` draws a row of five colour swatches (`yellow`, `green`, `blue`, `pink`, `purple`) and a Note button under the selection. Nothing has been sent to the server yet.

### Step 4: the user picks a colour, and the screen changes first

Clicking a swatch runs `handleHighlight(color)` in the toolbar. Before any network call it runs `onOptimisticCreate`, which `ReaderArticle` supplies:

{% fig "components/ReaderArticle.tsx: the optimistic highlight" %}
<!--src frontend/components/ReaderArticle.tsx-->
```typescript
onOptimisticCreate={(color) => {
  if (selection) {
    setHighlights((prev) => [
      ...prev,
      {
        id: `temp-${Date.now()}`,
        text: selection.text,
        start_offset: selection.startOffset,
        end_offset: selection.endOffset,
        color,
      },
    ]);
  }
}}
```
{% endfig %}

The highlight list now contains an entry with a made-up id (`temp-` plus the current time in milliseconds), and the renderer draws it straight away.

### Step 5: the POST

Then the toolbar calls the API client:

{% fig "components/HighlightToolbar.tsx: create on the server" %}
<!--src frontend/components/HighlightToolbar.tsx-->
```typescript
} else {
  const newHighlight = await highlightsAPI.create(contentId, {
    text: selection.text,
    start_offset: selection.startOffset,
    end_offset: selection.endOffset,
    color,
  });
  highlightId = newHighlight.id;
}
```
{% endfig %}

`highlightsAPI.create` goes through `fetchWithAuth`, so the request is a POST with the cookies attached and the `X-CSRF-Token` header added. The request body for the sample, and the response, in shape (illustrative: the values are made up, the field names are from the `HighlightCreate` and `HighlightResponse` schemas in `content-queue-backend/app/schemas/highlight.py`):

{% fig "POST /content/{id}/highlights (illustrative values)" %}
```json
// request
{ "text": "surface area. Too fine", "start_offset": 37,
  "end_offset": 59, "color": "yellow" }

// response, 201 Created
{ "id": "b1f0...", "content_item_id": "7c3e...", "user_id": "52aa...",
  "text": "surface area. Too fine", "note": null,
  "start_offset": 37, "end_offset": 59, "color": "yellow",
  "created_at": "2026-10-25T09:14:03Z" }
```
{% endfig %}

The server checks that the content item exists and belongs to the user (404 otherwise), then inserts a row. It does not check that the offsets fit the article, that the end is after the start, or that the text matches what sits at those offsets: the schema only requires `start_offset >= 0` and `end_offset > 0`. The chapter [Data model and Postgres](/platform/data-model/) covers the `highlights` table. Highlight embeddings, used for connections, are not created by this request; a separate batch task (`generate_highlight_embeddings_batch` in `content-queue-backend/app/tasks/embedding.py`) does that later.

### Step 6: refresh from the server

On success the toolbar calls `onHighlightCreated`, which is `refreshHighlights` in `ReaderArticle`. It re-fetches the whole list with `GET /content/{id}/highlights` and replaces the state, so the `temp-` entry is replaced by the stored row with its real id. The toolbar then clears the browser selection and closes.

### Step 7: how the highlight is drawn, now and on reload

Drawing is the same code path on first view, after a refresh and on a reload a week later: `HighlightRenderer` receives `html` (the article) and `highlights` (the list from the API) and runs an effect that rebuilds the markup. It parses the article HTML, walks its text nodes recording each node's start and end position, and for every node finds the highlights that overlap it:

{% fig "components/HighlightRenderer.tsx: which highlights touch this text node" %}
<!--src frontend/components/HighlightRenderer.tsx#163-165-->
```typescript
const overlappingHighlights = activeHighlights
  .filter((h) => h.start_offset < endChar && h.end_offset > startChar)
  .sort((a, b) => a.start_offset - b.start_offset);
```
{% endfig %}

For the sample, the text node "A finer grind exposes more surface area. " covers positions 10 to 52; the highlight covers 37 to 59, so it overlaps; and the node "Too fine" covers 52 to 60, so it overlaps too. Each overlapping node is split into up to three pieces (before, highlighted, after) and the highlighted piece is wrapped in a span with data attributes:

{% fig "components/HighlightRenderer.tsx: the wrapper span" %}
<!--src frontend/components/HighlightRenderer.tsx#245-258-->
```typescript
const span = document.createElement("span");
span.dataset.highlightId = segment.highlight.id;
span.dataset.highlightColor = segment.highlight.color;
span.dataset.highlightNote = segment.highlight.note || "";

if (segment.isFirst) {
  span.dataset.highlightIsFirst = "true";
}
if (segment.isLast) {
  span.dataset.highlightIsLast = "true";
}

span.innerHTML = contentHtml;
fragment.appendChild(span);
```
{% endfig %}

A highlight that crosses an element boundary (here the `<b>`) becomes several spans with the same id; `isFirst` and `isLast` mark the ends so that the connection indicator is drawn once at the start and the note control once at the end. The same jsdom check, applying the wrap to the sample:

{% fig "Captured: result of the wrap (re-implemented loop in jsdom)" %}
```html
<h2>Grind size</h2><p>A finer grind exposes more <span data-highlight-id="H1"
data-highlight-is-first="true">surface area. </span><b><span
data-highlight-id="H1" data-highlight-is-last="true">Too fine</span></b> and
the shot runs slow.</p>
```
{% endfig %}

(Line breaks added to fit the page.) Then two more steps run before anything reaches the screen. `addHeadingAnchors` adds the "#" links. The resulting HTML string goes through `sanitizeArticleHtml`, which is `DOMPurify.sanitize(html)` from the `dompurify` library: it removes event-handler attributes, `javascript:` links, inline scripts and similar, while keeping `data-` attributes, ids, classes and links. Finally `html-react-parser` turns the cleaned string into React elements, and a `transform` function replaces each `span[data-highlight-id]` with an `InlineHighlight` component, which paints the colour (from the `--highlight-*` CSS variables) and carries the note editor. The sanitize step matters because the article HTML comes from arbitrary web pages, and `html-react-parser` copies attributes onto React elements instead of removing dangerous ones.

The order of the work is the reason the offsets stay valid: positions are counted over the article's own text nodes before the heading anchors are added, and the anchors are empty (`anchor.textContent = ""`), so adding them does not shift any count.

## How it is built here

| File | Role |
|---|---|
| `frontend/lib/api.ts` | `fetchWithAuth`, `APIError`, and one object per resource (`contentAPI`, `listsAPI`, `highlightsAPI`, `searchAPI`, `draftsAPI`, and others) |
| `frontend/contexts/` | `AuthContext`, `ListsContext`, `ReadingSettingsContext`, `ThemeContext`, `PlayerContext` |
| `frontend/hooks/` | `usePagedListContent`, `useInfiniteSentinel`, `useHotkeys`, `useProcessingPolling`, `useConfirmAction`, `useTagEditor`, `useTTS` |
| `frontend/components/Reader.tsx` | The reader shell: progress bar, side panels, keyboard shortcuts |
| `frontend/components/ReaderArticle.tsx` | The article body, selection handling, highlights state (2,022 lines) |
| `frontend/components/HighlightRenderer.tsx` | Offsets to spans, sanitize, parse |
| `frontend/components/InlineError.tsx` | The one error component |
| `frontend/lib/flags.ts` | Feature flags |
| `frontend/app/globals.css` | Theme variables and all styles |

### Routes

The app uses the Next.js App Router: a folder under `frontend/app/` is a URL, and the `page.tsx` file in it is the page. Verified by listing the directory:

| Route | File | What it is |
|---|---|---|
| `/` | `app/page.tsx` | Landing page |
| `/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify-email` | one `page.tsx` each | Auth pages |
| `/dashboard` | `app/dashboard/page.tsx` and `DashboardClient.tsx` | The queue (`ContentList`) |
| `/content/[id]` | `app/content/[id]/page.tsx` | The reader; fetches `GET /content/{id}/full` |
| `/read` | `app/read/page.tsx` | Reader for an article sent by the extension without saving it |
| `/lists`, `/lists/[id]` | `app/lists/page.tsx`, `app/lists/[id]/page.tsx` | List index; one list with its writing workspace |
| `/crates` | `app/crates/page.tsx` | The vinyl collection, behind a flag |
| `/settings`, `/themes`, `/guide`, `/privacy` | one each | Preferences and static pages |
| `/[username]`, `/[username]/content/[id]` | `app/[username]/` | Public profile and public reader |
| `/mockups/failed-ingest` | a page under `app/mockups/` | A design exploration page that ships with the app |

The reader page is a client component that fetches in an effect, so the server never renders article HTML. This matters for the sanitizer, which returns its input unchanged when there is no `window` (server side); because the article is only rendered after a browser-side fetch, that branch is not reached for article content (inferred from `app/content/[id]/page.tsx` and `HighlightRenderer.tsx`).

### Talking to the API

`fetchWithAuth` is about 80 lines. The three decisions in it:

{% fig "lib/api.ts: the CSRF header goes on changes only" %}
<!--src frontend/lib/api.ts#135-141-->
```typescript
const method = (options.method || "GET").toUpperCase();
if (method !== "GET" && method !== "HEAD") {
  const csrfToken = getCsrfToken();
  if (csrfToken) {
    headers[CSRF_HEADER_NAME] = csrfToken;
  }
}
```
{% endfig %}

The code comment gives the reason: sending a custom header on every request forces the browser to make an extra preflight (OPTIONS) request even for GETs, and anything in front of the API that does not allow the header would then break reads. `getCsrfToken` reads the `sedi_csrf_token` cookie, which is deliberately not httpOnly. Every request also sets `credentials: "include"` so that cookies cross from the frontend's domain to the API's.

{% fig "lib/api.ts: one refresh, then one retry" %}
<!--src frontend/lib/api.ts#150-158-->
```typescript
if (response.status === 401 && !isRetry) {
  // Access token expired or invalid — try refreshing via the httpOnly
  // refresh cookie before giving up and sending the user to /login.
  const refreshed = await refreshAccessToken();
  if (refreshed) {
    return fetchWithAuth(url, options, true, suppressRedirect);
  }

  if (typeof window !== "undefined" && !suppressRedirect) {
```
{% endfig %}

On a 401 the client calls `POST /auth/refresh`, and if that succeeds repeats the original request once (`isRetry` stops a loop). If several requests get a 401 at the same moment they share one refresh through the module-level `refreshInFlight` promise, because the server revokes the old refresh token on use and a second concurrent refresh would fail. If the refresh fails the browser is sent to `/login`, except for the single call that runs on every page load to ask "who am I" (`suppressRedirect`), where a 401 only means the visitor is not logged in.

Errors are turned into one type, `APIError(status, detail, body)`. `detail` is taken, in order, from a parsed `message`, the response's `detail` string, a plain-text body, a fixed 429 message ("Too many requests. Please slow down."), or "Request failed (status)". When `detail` is itself a JSON string (the backend uses this for structured 409 bodies), it is parsed and exposed as `err.body`.

The backend's rate limiter sends a `Retry-After` header on 429 responses (`content-queue-backend/app/middleware/rate_limit.py`). The frontend does not read it: a search of `frontend/` for `Retry-After` finds nothing. The user sees the fixed message and no countdown.

### State

Five contexts, mounted in this order in `app/layout.tsx`: `ReadingSettingsProvider` outermost, then `ThemeProvider`, `AuthProvider`, `ListsProvider`, `PlayerProvider`.

| Context | Holds | Persistence |
|---|---|---|
| `AuthContext` | the current user; `login`, `register`, `logout`; calls `GET /auth/me` on mount | none in the browser: the session is the cookie |
| `ListsContext` | list item counts for the sidebar, with increment and decrement helpers | none |
| `ReadingSettingsContext` | theme, font, size, width, spacing, bionic reading, two visibility toggles | `localStorage` key `sedi-reading-settings`, loaded after mount to avoid a server/browser mismatch |
| `ThemeContext` | `theme`, `setTheme`, `toggleTheme`; delegates to `ReadingSettingsContext` | via the above |
| `PlayerContext` | the music queue and the YouTube player handle for the Crates feature | `localStorage` |

Everything else is component state or a hook. There is no global store and no data-fetching library: each page fetches in an effect and keeps the result in `useState`. The hooks that carry real logic are `usePagedListContent` and `useInfiniteSentinel` (below), `useHotkeys` (shortcuts), `useProcessingPolling` (re-checks items still being extracted), `useConfirmAction` (click once to arm, again to confirm a delete) and `useTagEditor` (tags with optimistic updates).

### Errors

The convention, written in the project's instructions and followed in the code: errors appear as an `InlineError` next to the thing that failed, never as a toast, and every call goes through `fetchWithAuth`.

{% fig "components/InlineError.tsx: the props" %}
<!--src frontend/components/InlineError.tsx#3-8-->
```typescript
interface InlineErrorProps {
  message: string;
  onDismiss?: () => void;
  onRetry?: () => void;
  className?: string;
}
```
{% endfig %}

The component is a 47-line box with a red left border and `role="alert"` so screen readers announce it. Optimistic updates with rollback follow one pattern, shown here in the list page's delete handler: remember the item and its position, remove it from the screen, call the API, and on failure put it back at the same index and set a message.

{% fig "app/lists/[id]/page.tsx: optimistic delete with rollback" %}
<!--src frontend/app/lists/[id]/page.tsx#366-381-->
```typescript
const handleDelete = async (id: string) => {
  const deletedIndex = contents.findIndex((c) => c.id === id);
  const deletedItem = contents[deletedIndex];
  try {
    setContents((prev) => prev.filter((content) => content.id !== id));
    adjustTotal(-1);
    await contentAPI.delete(id);
  } catch (err) {
    console.error("Failed to delete content:", err);
    setContents((prev) => reinsertItem(prev, deletedItem, deletedIndex));
    adjustTotal(1);
    setActionError("Couldn't delete item. Try again.");
  }
};
```
{% endfig %}

Note the functional updates (`setContents((prev) => ...)`): a page of results may arrive while the delete is in flight, and a rollback written as `setContents(oldArray)` would throw those rows away. The page-level test covers exactly this case.

### Keyboard shortcuts

`useHotkeys` takes a map from key combinations to handlers and attaches one `keydown` listener on `window`. It ignores key presses whose target is an input, a textarea or an editable element, so typing a note never triggers a shortcut. A combination such as `ctrl+k` is matched first, then the bare key.

| Where | Keys |
|---|---|
| Reader (`components/Reader.tsx`) | `esc` back to the dashboard, `h` highlights panel, `c` connections panel (wide screens, when enabled), `f` focus mode, `?` shortcut list |
| Article (`components/ReaderArticle.tsx`) | `n` highlight the selection and open its note, `b` bold, `i` italic |
| Queue (`components/ContentList.tsx`) | `j`, `k` move, `enter` open |

The handler map is a new object on every render, and the hook lists it as an effect dependency, so the listener is removed and re-added on every render of those components (inferred from `hooks/useHotkeys.ts`). It works, and costs little, but it is not what the dependency array looks like it intends.

### Lists, the writing workspace and paging

A list page (`app/lists/[id]/page.tsx`, 712 lines) shows the list's articles. Opening the writing workspace splits the screen: the list or an open article on the left, a Markdown editor (`components/MarkdownEditor.tsx`, built on TipTap) on the right. The editor autosaves the draft to `PATCH /lists/{id}/draft` 1,500 ms after the last keystroke (`debounceRef` in the editor).

Until October 2026 the page fetched every item of a list at once, which for a 2,000-item list cost 641 MB on the server (measured; see [Memory: investigation and fixes](/reliability/memory/)). Now the first request asks for 50 and the rest load as you scroll. The behaviour is documented from the user's side in `docs/design/product/list-infinite-scroll.md`. Three small pieces build it.

`usePagedListContent` owns the loaded rows, the total and the paging state. Its `loadMore` skips by the number of rows currently loaded, not by a page counter, so local deletions and server positions stay aligned:

{% fig "hooks/usePagedListContent.ts: load the next page" %}
<!--src frontend/hooks/usePagedListContent.ts-->
```typescript
const loadMore = useCallback(async () => {
  if (inFlightRef.current) return;
  inFlightRef.current = true;
  const generation = generationRef.current;
  setLoadingMore(true);
  setLoadMoreError(null);
  try {
    // Skip by what is loaded now: local removals already shifted the
    // server's positions the same way.
    const next: ContentItem[] = await listsAPI.getContent(listId, {
      skip: loadedRef.current,
      limit: pageSize,
    });
    if (generation !== generationRef.current) return;
    // Items added elsewhere meanwhile can shift a row into two pages.
    setLoaded((prev) => {
      const seen = new Set(prev.map((c) => c.id));
      return [...prev, ...next.filter((c) => !seen.has(c.id))];
    });
    if (next.length < pageSize) setExhausted(true);
  } catch (err) {
```
{% endfig %}

Four protections are visible here. `inFlightRef` makes a second call while one is running do nothing. `generationRef` is bumped by `reset()` (a fresh first page), so a response that belongs to the old list is dropped when it arrives. The `seen` set removes a row that appears in two pages because something was added in between. A page shorter than the limit marks the list as finished.

`useInfiniteSentinel` calls a function when a marker element comes within 400 px of the visible area, using the browser's `IntersectionObserver`:

{% fig "hooks/useInfiniteSentinel.ts: observe the footer" %}
<!--src frontend/hooks/useInfiniteSentinel.ts-->
```typescript
useEffect(() => {
  if (!enabled || !node || !supported) return;
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        onVisibleRef.current();
      }
    },
    { root, rootMargin: "400px 0px" },
  );
  observer.observe(node);
  return () => observer.disconnect();
}, [enabled, node, root, supported, rearmKey]);
```
{% endfig %}

`rearmKey` is the number of loaded rows: when a page lands and the footer is still on screen (a tall window, a short page) the observer is recreated, which makes it report the footer again and pull the next page. In writing mode the list scrolls inside its own panel, so the page passes that panel as `root`. `ListLoadMore` draws the footer ("50 of 213", a skeleton while loading, an `InlineError` with Retry on failure) and a plain Load more button where `IntersectionObserver` does not exist.

The page-level test (`frontend/__tests__/app/ListPage.infiniteScroll.test.tsx`, six tests) renders the real list page with the API module and most child components replaced by mocks, and a fake `IntersectionObserver` that the test fires by hand. It proves the wiring: the first request is `{ limit: 50 }`, the header shows the total (120) rather than 50, firing the observer requests `skip: 50` and then `skip: 100`, the footer disappears at the end, a delete lowers the total without refetching, a failed page shows Retry and keeps rows, a failed delete restores the row without losing pages loaded meanwhile, and a short list never creates an observer. It does not prove that scrolling in a real browser triggers the observer, that the backend honours `skip` and `limit` (the backend has its own tests), or anything about layout.

### Feature flags

Seven booleans in `frontend/lib/flags.ts`, each read from a `NEXT_PUBLIC_` environment variable:

| Flag | Variable | On when |
|---|---|---|
| `SHOW_FOR_YOU` | `NEXT_PUBLIC_SHOW_FOR_YOU` | not set to `"false"` |
| `SHOW_HIGHLIGHT_CONNECTIONS` | `NEXT_PUBLIC_SHOW_HIGHLIGHT_CONNECTIONS` | not set to `"false"` |
| `SHOW_CRATES` | `NEXT_PUBLIC_SHOW_CRATES` | not set to `"false"` |
| `SHOW_EDIT_ARTICLE` | `NEXT_PUBLIC_SHOW_EDIT_ARTICLE` | set to `"true"` |
| `SHOW_READING_THEMES` | `NEXT_PUBLIC_SHOW_READING_THEMES` | set to `"true"` |
| `SHOW_DRAFT_READS` | `NEXT_PUBLIC_SHOW_DRAFT_READS` | set to `"true"` |

(Three flags are on unless disabled; three are off unless enabled. `ARCHITECTURE.md` section 14 says all default to true and lists four; see the discrepancy note.)

The important property: **these values are fixed when the app is built, not when it runs.** Next.js replaces every `process.env.NEXT_PUBLIC_*` reference in code that ships to the browser with the literal value found at build time; the Next.js documentation (Environment Variables guide, "Bundling Environment Variables for the Browser") states that the inlining happens at build time and that a changed value needs a new build. In practice, changing a flag in the Vercel dashboard does nothing until the frontend is redeployed with a fresh build, and a redeploy that reuses a cached build can keep the old value. The same applies to `NEXT_PUBLIC_API_URL`. This is a documented framework behaviour that I did not re-test here (no network).

### Design tokens

The colours are CSS custom properties in `frontend/app/globals.css`, defined once per theme and used everywhere as `var(--color-...)`. The light theme is the default on `:root`; the others are classes on `<html>` that redefine the same names. Tailwind v4 is loaded by `@import "tailwindcss"` and a `@theme` block. There are four themes, not three: `ThemeContext` cycles light, dark and true-black, and sepia is chosen in settings.

| Token | light (`:root`) | `.dark` | `.sepia` | `.true-black` |
|---|---|---|---|---|
| `--color-bg-primary` | `#fffef7` | `#0d0d0d` | `#f5f0e8` | `#000000` |
| `--color-text-primary` | `#1a1a1a` | `#e0e0e0` | `#433422` | `#e5e5e5` |
| `--color-accent` | `#3d46c2` | `#6b73e8` | `#8b5e3c` | `#6b73e8` |

Each theme also sets secondary and faint text, border, status and five highlight colours (`--highlight-yellow` and so on), so a yellow highlight is a translucent amber on paper and a stronger one on black. A small inline script (`app/theme-script.tsx`) adds the theme class before React loads, to avoid a flash of the wrong theme, and `ReadingSettingsContext` keeps the class in sync afterwards. A `prefers-contrast: more` block darkens the accent and muted text.

### Tests

`frontend/__tests__/` holds 13 test files with 165 `it`/`test` cases, 3,713 lines in all (counted by `grep` and `wc`). Jest runs them in a jsdom environment with Testing Library; `next/jest` loads the Next config. The files cover the add-URL form, content cards, the queue list, the filter dropdown, the highlight toolbar, the highlights panel, inline highlights, bionic text, the API client (CSRF header, credentials, one refresh retry, no infinite loop, paging query string) and the paging code. Not covered: the reader's selection and offset code, `HighlightRenderer`, any context, any page other than the list page, and any end-to-end flow.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Client-rendered pages calling the API | Server components that fetch on the server; server actions | The reader needs instant local edits and the API is a separate FastAPI service with its own auth, so a thin client is the shortest path. Cost: no server-rendered content, and each page owns its loading and error states. |
| Highlights as character offsets over the article's text | Store an XPath or DOM range; store the quote and search for it on render; a W3C-style anchor with the quote plus context | Offsets are two integers, cheap to store and to overlap-test. The cost is fragility (see "What is weak"). Storing the quote as well (this code does store `text`) leaves room to repair a highlight later; nothing does yet. |
| httpOnly cookies, CSRF header copied from a readable cookie | Tokens in `localStorage`; a bearer header | A script that gets onto the page cannot read the session tokens. Details in [Auth and security](/platform/auth-and-security/). |
| Inline errors only | Toasts | An error stays next to the item and has a Retry; a toast disappears and does not say which row failed. |
| Optimistic updates with rollback | Wait for the server before changing the screen | Edits feel instant. The cost is the rollback code, and it is not applied everywhere (see below). |
| Skip by rows loaded, not page number | Page counter; cursor | Local deletions keep the next request aligned with the server. A cursor would be more robust against concurrent inserts but needs a backend change; the de-duplication covers the common case. |
| `NEXT_PUBLIC_` flags | A runtime config endpoint or a flag service | Zero infrastructure. The price is a rebuild per change, so flags are release switches, not live switches. |
| CSS variables per theme | Tailwind `dark:` variants only; CSS-in-JS | One set of names, four themes, no component edits to add a theme. |

## How it fails

| Failure | Cause | What happens | What was done |
|---|---|---|---|
| Session expires mid-action | Access token timed out | First request returns 401 | One refresh, one retry; concurrent 401s share one refresh; redirect to login if it fails |
| Refresh token already rotated | Two refreshes at once | Second refresh fails and logs the user out | `refreshInFlight` makes concurrent callers share one |
| Delete fails after the row vanished | Network or server error | Row would be gone locally but present on the server | Re-insert at the old index, restore the total, show an inline message |
| A page loads while a delete is pending | Scrolling during a slow request | A rollback written with a captured array would discard the new page | Functional state updates; covered by a test |
| A stale page lands after the list was reloaded | Navigation or reset during a request | Old rows appended to the new list | `generationRef` drops the response |
| A row appears in two pages | An item added between requests shifts positions | Duplicate row on screen | `seen` set removes duplicates |
| Highlight POST fails | Network error, 404, 429 | The temporary highlight stays on screen, no message (see below) | Not handled; logged to the console only |
| Render error in a component | A bug, or odd article HTML | Default Next.js error page | Not handled: none of the Next.js special files `error`, `global-error` or `not-found` exist under `frontend/app/` (verified by `find`) |

## What is weak or missing

Ranked by how likely each is to matter.

1. **Highlight offsets can drift, and nothing detects it.** The offsets are only valid against exactly the text that was on screen when they were computed. If the article text changes (the user edits the article, a re-extraction replaces `full_text`, or the bionic/heading code ever adds visible text), stored highlights point at the wrong words. The server stores the quoted `text` beside the offsets but never compares them, and the renderer never checks that the characters at an offset match the quote. A check on load, with a search for the quote when it does not match, would repair most drift.
2. **Highlighted text that contains angle brackets is re-parsed as HTML.** The renderer builds each highlighted piece with `span.innerHTML = contentHtml`, where the piece came from a text node's `textContent`. A text node containing the characters `<b>x</b>` (shown literally on the page) comes back as real markup. Run in jsdom on a text node `use a<b>x</b> tags`, the span's text becomes `use ax tags`: six characters shorter, so every later offset in that article is off. DOMPurify runs afterwards, so script cannot execute from this, but the text changes. Assigning `span.textContent` (or escaping) instead fixes it. Only highlighted pieces and bionic-reading mode use this path; plain pieces use `createTextNode`.
3. **No error boundaries.** A thrown error in the reader shows the framework's default error page. The Sentry client file `frontend/sentry.client.config.ts` exists, but there is no `instrumentation` file in `frontend/` and I did not verify that the SDK initialises under Next 16 (not verified).
4. **Highlight creation is optimistic without rollback or message.** If the POST fails, the `temp-` highlight stays in state, the error goes to `console.error`, and the user sees a highlight that is not saved until something re-fetches the list. This breaks the project's own rule (errors shown with `InlineError`). The `n` shortcut has the same handling.
5. **Two code paths compute offsets.** The toolbar path uses `getTextOffsets` (tree walker, the end offset taken from the range). The `n` shortcut computes `startOffset` from `preSelectionRange.toString().length` and sets `endOffset = start + trimmed text length`. They agree for ordinary selections and can differ when the selection starts or ends with whitespace or crosses hidden text (inferred from the code, not reproduced).
6. **The server accepts any offsets.** `end_offset` may be below `start_offset` and either may exceed the article length; the schema only checks `>= 0` and `> 0`. The fix is a check against the item's text length, which the endpoint can read.
7. **The queue loads one page of 50.** `ContentList` calls `contentAPI.getAll()` with no `skip` or `limit`, and the backend's `GET /content` defaults to `limit=50`. Filtering and sorting are client-side over those rows, and the header shows "n / total". For a library over 50 items, older items are reachable through search and lists but not by scrolling the queue. Inferred from the code (`components/ContentList.tsx` line 250 and `app/api/content.py`); not run against a large library.
8. **Images are plain `<img>` tags.** There are 18 `<img` occurrences under `app/` and `components/`, 15 with an ESLint disable for `@next/next/no-img-element`, and no use of `next/image`. Article images come from arbitrary sites, so the optimiser would need allow-listed hosts; the cost is no resizing, no lazy-loading policy beyond the browser's, and full-size downloads on mobile.
9. **Thin tests.** 13 test files and 3,713 test lines against 109 TypeScript files and 26,195 lines under `app/`, `components/`, `contexts/`, `hooks/`, `lib/` and `types/` (22,466 without the generated API types file `types/generated.ts`). No end-to-end tests. The reader, the part with the most subtle logic, has none for offsets or rendering.
10. **`ReaderArticle.tsx` is 2,022 lines.** Selection, summary, metadata editing, similar articles, image zoom and scroll restore live in one component, which makes the offset code hard to test in isolation.
11. **The retry message ignores `Retry-After`.** See above; minor.

{% say %}
The frontend is a Next.js app where nearly every page runs in the browser and calls the API through one function. That function sends the login cookies, adds a CSRF header on anything that changes data, and on a 401 refreshes the session once and retries once. State is a handful of React contexts plus local component state, and errors show up inline next to whatever failed, with changes applied on screen first and rolled back if the call fails. When you highlight text, the app walks the article's text nodes, adds up their lengths to find start and end character offsets, and posts those with the quoted text; on every load the renderer takes the offsets from the API and wraps those character ranges in spans, after DOMPurify cleans the HTML. Long lists load fifty at a time using an intersection observer. The weak spots are that offsets are only valid for exactly the same text and nothing checks them, there are no error boundaries, and test coverage is thin where it matters most, in the reader.
{% endsay %}

## Check yourself

{% qa "A highlight is stored as start and end offsets. What exactly are they offsets into, and why not into the HTML string?" %}
They are positions in the concatenation of the article's text nodes, in document order. Tags contribute no characters, so the numbers do not change if markup around the same text changes (headings get anchors, bold is added). Offsets into the HTML string would break on any markup change.
{% endqa %}

{% qa "Why does the CSRF header go only on non-GET requests?" %}
A custom header on a cross-origin request makes the browser send a preflight OPTIONS request first. Doing that for every read adds latency and breaks reads if anything in front of the API does not allow the header. GET and HEAD do not change data, so the server does not require the token for them.
{% endqa %}

{% qa "Three requests get a 401 at the same moment. How many refresh calls are made, and why?" %}
One. The first caller starts the refresh and stores its promise in `refreshInFlight`; the others await the same promise. The server revokes a refresh token when it is used, so a second refresh with the old cookie would fail and log the user out.
{% endqa %}

{% qa "You change NEXT_PUBLIC_SHOW_CRATES in the hosting dashboard. Nothing happens. Why, and what do you do?" %}
`NEXT_PUBLIC_` values are inlined into the JavaScript bundle at build time, so the running build still contains the old value. Trigger a new build and deploy; a redeploy of the old build is not enough.
{% endqa %}

{% qa "The user deletes a list item, and while the DELETE is slow, scrolls and a new page loads. The DELETE then fails. What should be on screen, and what in the code makes it so?" %}
The deleted item back at its old position, and the newly loaded page still there. The rollback uses functional updates (`setContents((prev) => reinsertItem(...))`) instead of restoring a saved copy of the array, and the next-page request skips by rows currently loaded. A page-level test covers this case.
{% endqa %}

{% qa "What would you change first to make highlights trustworthy over time?" %}
On load, compare the text at each highlight's offsets with its stored `text`; when they differ, search for the quote near the old position and either move the highlight or mark it as detached. Also fix the `innerHTML` assignment so the rendered text matches the counted text, and have the server reject offsets outside the article length.
{% endqa %}
