# Changelog — 2026-05-12: Safari Extension Port + Reader Overlay Refactor

## 1. Safari extension (local build)

**Intention:** Bring the same Save + ephemeral Read experience to Safari on macOS without App Store submission. Users who prefer Safari should not need to switch to Chrome.

**What changed:**
- Scaffolded `safari-extension/` via `safari-web-extension-converter`. Xcode project targets macOS only (`--macos-only`), copies extension files into `safari-extension/sed.i/sed.i Extension/Resources/`.
- Fixed bundle ID mismatch after conversion: extension target must use `com.sedi.sed-i.Extension` (prefixed with parent app `com.sedi.sed-i`).
- Added `ENABLE_OUTGOING_NETWORK_CONNECTIONS = YES` entitlement so the extension's `fetch()` can reach the API through the macOS app sandbox.
- Removed hardcoded `DEVELOPMENT_TEAM` from committed `project.pbxproj`; contributors set their own team in Xcode.
- Added `make safari-sync` (rsync --delete, mirrors `extension/` → Safari Resources) and `make safari-open` targets.
- Updated `.gitignore` to exclude `DerivedData/` and Xcode user state.

**Impact:** Safari users can load the extension locally. `make safari-sync` + Xcode rebuild (⌘B → ⌘R) keeps the Safari copy in sync with Chrome changes.

---

## 2. Content extraction Safari fix (`executeScript` Promise awaiting)

**Intention:** The "Read" and "Save" buttons in the Safari extension were failing with "Extraction failed." on every page.

**Root cause:** Safari does not await a `Promise` returned from `executeScript({ files: [...] })`. The old pattern injected `content.js` as a file and expected the module's top-level async IIFE to return an extraction result — that works in Chrome but not Safari.

**What changed:**
- `content.js`: converted from an auto-executing async IIFE that returned a value, to a sync IIFE that exposes `window.__sediExtractAndInlineContent` as a global function. Guard against re-declaration on re-injection.
- `popup.js` (both Read and Save paths): inject Readability.js and content.js via `executeScript({ files })` (no awaited return value), then call the global function via a separate `executeScript({ func: async () => window.__sediExtractAndInlineContent() })`. Safari properly awaits `func:` results.
- Read path: resets `window.__SEDI_SKIP_IMAGE_INLINE = false` after extraction so a subsequent Save on the same tab gets full image inlining.
- Read path: passes `accessRestricted` from extraction result through to the article object given to the reader overlay.

**Impact:** Extraction now works on both Chrome and Safari.

---

## 3. Reader overlay: shadow DOM + instant close

**Intention:** Two compounding problems on Safari: (a) closing the reader had ~3 s of latency; (b) the reader's typography (font size, line width, spacing) varied across pages.

**Root cause (latency):** `document.body.replaceWith(savedBody)` — re-rendering a large detached DOM tree takes ~3 s in Safari's engine. `setTimeout` and double-`requestAnimationFrame` fallbacks didn't help because the delay was in `replaceWith` itself.

**Root cause (typography variation):** The overlay div was appended to the existing body, placing it inside the live page DOM. Page CSS rules (e.g. `p { font-size: 18px }`) matched reader elements. Shadow DOM isolation blocks external *selectors*, but `rem` units still resolve against the document's `html` element font-size — a page using `html { font-size: 62.5% }` made all `rem`-based sizes 62.5% of intended.

**What changed:**
- `reader-overlay.js`: replaced body-swap with `host.attachShadow({ mode: 'open' })`. All reader DOM and CSS live inside the shadow root; no external stylesheet can match shadow elements.
- All CSS length values converted from `rem` to `px` so the document root font-size has no effect.
- Closing the reader is now `host.remove()` — instant, no DOM re-render.
- Scroll tracking moved from `window` to the overlay `host` element (which has `overflow-y: auto`).
- Animation fallback changed from double-rAF (~33 ms, fires before the 0.12 s close animation) to `setTimeout(finish, 150)`.
- A minimal `#__sedi_vars__` style tag in `document.head` hides original body children (`body > :not(#__sedi_reader__) { display:none }`) while the reader is active; removed on close.

**Impact:** Reader opens and closes with correct animations and no latency. Typography is identical on every page regardless of the site's CSS.

---

## 4. Alembic merge-head migration

**Intention:** Railway deployment was crashing with "Multiple head revisions are present" because two branches each added a migration without merging.

**What changed:**
- Added `f76159fa41d4_merge_heads.py` to merge the two diverged heads (`009_content_chunks` and `5534fbac2811`).

**Impact:** `alembic upgrade head` on Railway succeeds; deployment unblocked.
