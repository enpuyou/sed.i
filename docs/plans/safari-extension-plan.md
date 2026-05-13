# sed.i Safari Extension — Local Build Plan
Status: Ready to execute
Date: 2026-05-12
Scope: Port the Chrome MV3 extension to Safari for local macOS testing. No App Store submission.

---

## 1) Why this document exists

The Chrome extension is feature-complete and submitted for review. We want the same
Save + ephemeral Read experience available in Safari, loadable as an unsigned local
extension without going through any store.

---

## 2) Current state (Chrome extension)

Files in `extension/`:

| File | Role |
|---|---|
| `manifest.json` | MV3, `activeTab` + `scripting` + `storage` permissions |
| `background/service_worker.js` | Auth token storage, API calls |
| `popup/popup.{html,css,js}` | 300 px popup: login → ready (Save + Read) |
| `content/content.js` | Readability extraction, image inlining |
| `content/reader-overlay.js` | Ephemeral reader injected into the live tab |
| `lib/Readability.js` | Mozilla Readability, bundled |
| `icons/icon{16,48,128}.png` | Extension icons |

APIs used:
- `chrome.runtime.sendMessage` / `onMessage`
- `chrome.storage.local`
- `chrome.scripting.executeScript` (injecting JS files + inline functions)
- `chrome.tabs.query`
- `window.close()` (popup self-close)
- `fetch()` (in service worker and content script)

Safari's WebExtensions API implements all of the above under the `browser.*` namespace.
Chrome's `chrome.*` namespace is also aliased by Safari — meaning the existing code works
without any API rewrites.

---

## 3) Safari WebExtensions compatibility

Safari 14+ supports MV2; **Safari 15.4+ supports MV3** including service workers.
macOS 15 (Sequoia) / macOS 26 (current) ships Safari 18+, which has full MV3 support.

Known compatibility notes for this extension:

| Feature | Chrome | Safari | Action needed |
|---|---|---|---|
| `chrome.*` namespace | Native | Aliased to `browser.*` | None — Safari exposes both |
| MV3 `service_worker` background | ✓ | ✓ (Safari 15.4+) | None |
| `chrome.scripting.executeScript` | ✓ | ✓ (Safari 15.4+) | None |
| `chrome.storage.local` | ✓ | ✓ | None |
| `chrome.tabs.query` | ✓ | ✓ | None |
| `fetch()` in service worker | ✓ | ✓ | None |
| Inline `func:` in executeScript | ✓ | ✓ (Safari 15.4+) | None |
| `files:` in executeScript | ✓ | ✓ | None |
| `window.close()` in popup | ✓ | ✓ | None |
| CSP in popup HTML | Strict | Strict | None (no inline scripts) |
| `"web_accessible_resources"` | N/A | N/A | Not used |

**No API namespace rewrites are required** (Safari aliases `chrome.*` to `browser.*`). One small JS adaptation was needed: `content.js` exposes the extractor as `window.__sediExtractAndInlineContent` so `popup.js` can call it via `executeScript({ func })`, which Safari properly awaits (unlike `files:` injections). The only remaining work is:
1. Run the Apple converter to scaffold the Xcode project.
2. Build and sign the app with a personal (free) Apple developer certificate.
3. Enable unsigned extensions in Safari Developer settings.

---

## 4) Phased execution steps

### Phase 1 · Scaffold the Xcode project (15 min)

**Step 1.1 — Set active developer directory to Xcode.app**

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

Verify:
```bash
xcrun --find safari-web-extension-converter
# → /Applications/Xcode.app/Contents/Developer/usr/bin/safari-web-extension-converter
```

**Step 1.2 — Run the converter**

```bash
/Applications/Xcode.app/Contents/Developer/usr/bin/safari-web-extension-converter \
  /Users/enpuyou/cmpsc/projects/content-queue/extension \
  --project-location /Users/enpuyou/cmpsc/projects/content-queue/safari-extension \
  --app-name "sed.i" \
  --bundle-identifier "com.sedi.safari" \
  --macos-only \
  --copy-resources \
  --no-open \
  --no-prompt
```

Flag rationale:
- `--macos-only` — we only need Safari on Mac, keeps the project simpler
- `--copy-resources` — copies extension files into the Xcode project (safer than referencing the original directory since future Chrome edits won't silently break the Safari build)
- `--no-open` — we'll open Xcode manually after reviewing the output
- `--no-prompt` — non-interactive (script-friendly)

This generates a directory `safari-extension/` with structure:
```
safari-extension/
  sed.i/                    ← macOS app target
    AppDelegate.swift
    ViewController.swift
    Assets.xcassets/
    Info.plist
    sed.i.entitlements
  sed.i Extension/          ← WebExtension target
    manifest.json           ← copy of extension/manifest.json
    Resources/              ← all extension files (copied)
  sed.i.xcodeproj/
```

**Step 1.3 — Verify the generated manifest**

Open `safari-extension/sed.i Extension/Resources/manifest.json` and confirm it matches
the source. The converter may add a `"browser_specific_settings"` key — that is fine.

---

### Phase 2 · Configure signing (10 min)

Open the project in Xcode:
```bash
open /Users/enpuyou/cmpsc/projects/content-queue/safari-extension/sed.i.xcodeproj
```

**Step 2.1 — Set team for both targets**

In Xcode → Project navigator → `sed.i.xcodeproj` → select the **sed.i** target:
- Signing & Capabilities tab
- Team: select your personal Apple ID (free account — "Personal Team")
- Bundle identifier: `com.sedi.safari` (or change to avoid conflicts)

Repeat for the **sed.i Extension** target with:
- Bundle identifier: `com.sedi.safari.Extension`

A free Apple ID creates a locally-signed ("Sign to Run Locally") certificate.
No paid developer account is required for local testing.

**Step 2.2 — Confirm entitlements**

The converter generates `sed.i.entitlements`. For local use it should contain only:
```xml
<key>com.apple.security.app-sandbox</key><true/>
<key>com.apple.security.network.client</key><true/>
```

The `network.client` entitlement is required so the extension can reach the API.
If it is missing, add it: Signing & Capabilities → `+` → Network → Outgoing Connections (Client).

---

### Phase 3 · Build and install (5 min)

**Step 3.1 — Build**

In Xcode: Product → Build (⌘B). Both targets must compile with no errors.

If you get "No such module 'SafariServices'" — check that the macOS SDK is selected
(not iOS): Project → Build Settings → Base SDK → macOS.

**Step 3.2 — Run**

Product → Run (⌘R). This launches the **sed.i.app** wrapper (a minimal macOS app).
The app itself is just a host — the important thing is that launching it registers the
extension with Safari.

**Step 3.3 — Enable in Safari**

1. Safari → Settings → Advanced → check "Show features for web developers"
2. Safari → Develop → Allow Unsigned Extensions
   *(This prompt resets every time you restart Safari — you must re-allow after each restart.)*
3. Safari → Settings → Extensions → find **sed.i** → enable it
4. In the extension permissions dialog: "Allow on All Websites" (needed for `activeTab`)

The toolbar icon appears. Click it on any article to test.

---

### Phase 4 · Sync future Chrome changes to Safari (ongoing)

Because `--copy-resources` was used, the Safari project has its own copy of the extension files in `safari-extension/sed.i Extension/Resources/`.

**When the Chrome extension changes, update Safari:**

```bash
# From repo root
cp -r extension/* "safari-extension/sed.i Extension/Resources/"
```

Then rebuild in Xcode (⌘B + ⌘R).

Alternatively, **skip `--copy-resources`** on the initial conversion — the Xcode project will reference the original `extension/` directory directly, and Chrome changes are automatically reflected on the next build. The trade-off is that `extension/` must stay at that exact path.

To switch to the live-reference approach, re-run the converter without `--copy-resources`:
```bash
/Applications/Xcode.app/Contents/Developer/usr/bin/safari-web-extension-converter \
  /Users/enpuyou/cmpsc/projects/content-queue/extension \
  --project-location /Users/enpuyou/cmpsc/projects/content-queue/safari-extension \
  --app-name "sed.i" \
  --bundle-identifier "com.sedi.safari" \
  --macos-only \
  --no-open \
  --no-prompt \
  --force
```

---

## 5) Known Safari gotchas for this extension

**None require code changes today**, but document them for future reference:

1. **Service worker lifetime** — Safari may suspend the service worker more aggressively
   than Chrome. The current extension uses the service worker only during active saves
   (request/response cycle), so this should not cause problems. If it does, the fix is
   to call `chrome.runtime.sendMessage` instead of holding a port.

2. **`executeScript` with `func:`** — Requires Safari 15.4+. macOS 26 ships Safari 18,
   so this is fine. On older Safari, you'd need to fall back to injecting a file.

3. **CSP in popup HTML** — Safari enforces MV3 CSP (`script-src 'self'`). The popup
   already has no inline scripts, so this passes.

4. **`window.close()` in popup** — Works in Safari. No change needed.

5. **`chrome.storage.local` vs `browser.storage.local`** — Safari bridges both.
   No change needed.

6. **Sandbox + network** — The app sandbox in the macOS host app must have
   "Outgoing Connections (Client)" checked or the fetch() calls in the service worker
   will silently fail. Covered in Phase 2.2 above.

7. **"Allow Unsigned Extensions" resets on Safari restart** — This is expected Safari
   behavior for locally-signed (non-notarized) extensions. Must re-enable after each
   Safari restart. For permanent local use, a paid Apple Developer account can notarize
   the app, but that's out of scope here.

---

## 6) .gitignore for safari-extension/

The Xcode project should be committed (it's not regenerated automatically), but build
artifacts should not. Add to `.gitignore`:

```
# Safari extension Xcode build
safari-extension/build/
safari-extension/*.xcodeproj/xcuserdata/
safari-extension/*.xcodeproj/project.xcworkspace/xcuserdata/
safari-extension/DerivedData/
```

---

## 7) Success criteria

- [ ] `sed.i` icon appears in Safari toolbar
- [ ] Login flow works (credentials saved, token persisted via `chrome.storage.local`)
- [ ] Popup shows article title, description, favicon on any `https://` tab
- [ ] "Save to sed.i" sends article to the API and shows `sent ✓`
- [ ] "Read" injects the reader overlay into the live tab
- [ ] Dark/light theme toggle persists across popup close/reopen
- [ ] Dev mode (2s logo long-press) reveals API URL override field

---

## 8) What is NOT in scope

- iOS Safari (can be added by removing `--macos-only` and adding an iOS target)
- App Store distribution (requires a paid Apple Developer account + notarization)
- Safari-specific UI customization (toolbar badge, context menus)
- JavaScript changes (none required — the Chrome code works as-is)
