---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# sed.i — Landing & Guide Page Design Spec

## Overview

Two pages. One shared component system. Different jobs.

| | Landing (`/`) | Guide (`/guide`) |
|---|---|---|
| **Job** | Show what sed.i is and make someone want it | Explain how to use it |
| **Tone** | Evocative, sparse, let the product speak | Clear, detailed, direct |
| **Demo clips** | Yes — every feature section | Selective — only complex flows |
| **Text density** | 1-2 sentences per feature | Full explanations, tips, shortcuts |
| **Navigation** | None (scroll-only) | Sticky sidebar TOC (existing) |
| **Hero** | Keep existing exactly as-is | Keep existing header |

---

## Page Structure

### Landing (`/`)

```
[existing hero — untouched]
  logo, curate/read/listen, login/signup, background decoration, scroll hint

[feature showcase — new]
  sticky-split layout, 5 features, each with a demo clip

[footer — minimal]
  Guide link | sed.i
```

### Guide (`/guide`)

```
[existing header — untouched]
  sticky top bar, logo, NowPlaying, theme toggle, Get Started

[existing sidebar TOC — untouched]
  section links, active highlight

[main content — enhanced]
  each section: SectionHeader (existing) + Feature rows (existing)
  select sections: add a DemoBlock above the feature rows
  no clip requirement — static screenshot or nothing is fine for simple sections
```

---

## Shared Component: `FeatureShowcase`

Both pages use the same sticky-split row component. Props control the variant.

### Props

```typescript
interface FeatureShowcaseProps {
  num: string;           // "01", "02", etc.
  title: string;
  description: string;
  detail?: string;       // small mono label e.g. "Reader · Highlights · Progress"
  demo: React.ReactNode; // the right-column content
  variant?: "landing" | "guide"; // default "landing"
}
```

### Layout behavior

**landing variant:**
- Left col: 340px fixed, sticky at vertical center (`position: sticky; top: 50%; transform: translateY(-50%)`)
- Right col: flex-1, demo fills it
- Min-height: 100vh so there's real scroll room
- Border-top on all but first

**guide variant:**
- Left col: narrower, ~280px, sticky at `top: 6rem` (below the header)
- Right col: flex-1, but taller text content, demo is optional/smaller
- Min-height: auto (content determines height)
- Integrate with existing `SectionHeader` and `Feature` components

---

## Demo Block Component

The right column content. For landing: full video placeholder → real clip later. For guide: optional, smaller.

```typescript
interface DemoBlockProps {
  clipSrc?: string;         // path to .mp4/.webm — if absent, show placeholder
  placeholderLabel?: string;
  placeholderContent?: React.ReactNode; // CSS animation while no clip
  aspectRatio?: "4/3" | "16/9" | "3/2"; // default "4/3" for landing
  size?: "full" | "medium"; // full = fills column, medium = max-w-sm centered
}
```

### Clip behavior
- If `clipSrc` is set: `<video autoPlay loop muted playsInline />`
- `autoPlay` is gated on IntersectionObserver — only plays when visible (no scroll-past autoplays)
- Fade in via CSS transition (same pattern as mock — `opacity: 0 → 1`)
- No controls shown

### Placeholder behavior (while no clip exists)
- Bordered box with `var(--color-bg-secondary)`
- Mono label at bottom: `"screen recording · coming soon"` or custom `placeholderLabel`
- Optional `placeholderContent` slot: CSS animation to hint at the feature (see feature specs below)

---

## Feature Sections — Landing

### 01 — Save

**Text**
```
num: "01"
title: "Save"
description: "Paste a URL. Article text, metadata, and images extracted automatically."
detail: "Chrome Extension · Paste URL · Auto-extract"
```

**Clip brief**
- Start: `/articles` with 2-3 existing items. Extension popup visible on a news article.
- Action: click Save in extension popup.
- Hard cut to: `/articles`. New article fades into top of list with title and source visible.
- End: cursor at rest, list settled.
- Length: ~5s. Loop: final frame (list) matches the visual weight of start.

**Placeholder animation**
```
Fake content card sliding in from the top:
  [ ████████████████████ ]   ← title bar
  [ ████████ ]               ← source + date
  ↑ slides down + fades in on repeat
```

**Free recording tool:** QuickTime Player (built-in Mac). Record screen region. Trim in QuickTime. Export as .mov → convert to .webm via `ffmpeg -i clip.mov -c:v libvpx-vp9 -b:v 0 -crf 33 clip.webm`.

---

### 02 — Read

**Text**
```
num: "02"
title: "Read"
description: "Highlight passages, add notes, enter focus mode. Your reading, your way."
detail: "Typography · Highlights · Focus"
```

**Clip brief**
- Start: article open in reader. Clean paragraph visible.
- Action: select a sentence → highlight color picker appears → pick yellow → highlight sets.
- Then: press `f` → focus mode dims everything except current paragraph.
- End: focused view, one highlighted sentence glowing.
- Length: ~7s. No tab switch needed — single page flow.

**Placeholder animation**
```
Article lines skeleton:
  ████████████████████████████
  ████████████████
  ████████████████████ ← this one pulses yellow (highlight color)
  ████████████████████████
  ████████
```

---

### 03 — Listen

**Text**
```
num: "03"
title: "Listen"
description: "A vinyl collection from Discogs. Browse crates, queue tracks, play through YouTube."
detail: "Crates · Player · Queue"
```

**Clip brief**
- Start: `/crates` grid of vinyl covers (looks good if you have 6+ records).
- Action: click a record → gatefold detail view opens.
- Then: click a track → player bar at bottom comes alive with album art + title.
- End: player playing, track name visible.
- Length: ~7s. Single-page flow.

**Placeholder animation**
```
Spinning vinyl circle (already in mock):
  rotation: 4s linear infinite
  center hole: smaller circle, bg-primary
  faint groove rings via box-shadow
```

---

### 04 — Claude

**Text**
```
num: "04"
title: "Claude"
description: "Talk to your reading list. Summarize, create lists, send drafts — through MCP in Claude."
detail: "MCP · 13 tools · Works in Claude Desktop + claude.ai"
```

**Clip brief**
- Start: Claude chat window. App visible in background (or second take).
- Action: type (or pre-typed) `create a list called "Design Systems"` → hit enter → streaming response.
- Hard cut to: `/lists` in the app. New list highlighted/visible.
- End: list page settled with "Design Systems" list visible.
- Length: ~8s. Two-window flow — cut during the Claude streaming response zoom.

**Tip for clean two-window recording:**
- Use QuickTime "Screen Recording" on just the Claude window, then just the app window.
- Edit together in iMovie (free, built-in): place clips sequentially, use a 0.2s cross-dissolve at the cut.
- OR: record the full screen and crop the irrelevant parts in QuickTime trim.

**Placeholder animation**
```
Terminal-style prompt:
  > create a list "Design Systems"
  ▌ (blinking cursor, accent color)

  After 2s, below it fades in:
  ✓ List created.

  Loops: cursor blinks, text resets after 4s
```

---

### 05 — Write

**Text**
```
num: "05"
title: "Write"
description: "Draft alongside your sources. Markdown, focus mode, highlights as references."
detail: "Editor · Source Pane · Auto-save"
```

**Clip brief**
- Start: writing workspace open, source pane visible on right with a highlighted excerpt.
- Action: click a highlight in source pane → it scrolls into editor reference.
- Then: type a sentence in the editor.
- End: editor with text, source pane visible — dual-pane view.
- Length: ~6s. Single-page flow (writing workspace).

**Placeholder animation**
```
Dual-pane skeleton:
  Left (editor):          Right (source):
  ████████████████        ░░░░░░░░░░░░░░
  ████████                ░ highlighted ░
  ████████████████        ░   excerpt   ░
  ██████                  ░░░░░░░░░░░░░░

  Cursor blinks in left pane
  Highlight in right pane pulses (yellow, slow)
```

---

## Feature Sections — Guide (Demo additions only)

The guide keeps all existing `Feature` rows unchanged. These additions are `DemoBlock` components placed **above** the feature rows for sections where a visual helps.

### Getting Started — DemoBlock
- `size="medium"`, no clip needed initially
- Shows the URL input bar + a content card appearing
- Or just a static annotated screenshot of the dashboard

### Claude Integration — DemoBlock
- `size="full"`, clip: same as landing #04 clip
- This is the most complex section — a visual really helps
- Placed above the feature rows, below the `SectionHeader`

### Reading — no DemoBlock needed
- The feature rows already describe everything clearly
- A clip would add length without clarity

### Crates, Lists, Write — optional DemoBlock
- Add later when clips exist
- Not needed for launch of the redesigned guide

---

## Recording Setup — Free Tools Only

### Tools
| Tool | Use | Cost |
|---|---|---|
| QuickTime Player | Screen recording (Mac built-in) | Free |
| iMovie | Trim, cut, cross-dissolve | Free |
| ffmpeg | Convert .mov → .webm for web | Free (brew install ffmpeg) |

### Browser setup before recording
1. Chrome, no bookmarks bar (`Cmd+Shift+B` to toggle off)
2. No extensions visible except the sed.i extension (pin it, hide others)
3. Window: exactly 1280×800. Use this to resize:
   ```
   # In browser console:
   window.resizeTo(1280, 800)
   ```
4. Zoom: 100% (`Cmd+0`)
5. Demo account with clean, real-looking data — at least:
   - 5+ articles with good headlines (no lorem ipsum)
   - 6+ vinyl records with cover art
   - 1 list with 3+ items
   - 1 draft with a few paragraphs
6. QuickTime → record a **specific screen region**, not full screen (avoids menu bar, dock, other windows)

### ffmpeg conversion
```bash
# .mov to .webm (VP9, good quality, small file)
ffmpeg -i clip.mov -c:v libvpx-vp9 -b:v 0 -crf 33 -c:a libopus clip.webm

# Check file size — target < 500KB per clip for landing page
ls -lh clip.webm
```

### Multi-window flow (Claude clip)
1. Open Claude at ~800px wide on left side of screen
2. Open the app at ~800px wide on right side (or second desktop space)
3. Record full screen with QuickTime
4. In iMovie: import, trim to just the Claude action, cross-dissolve, trim to just the app result
5. Export as 1280×800 `.mov`, then ffmpeg to `.webm`

---

## File Structure

```
frontend/
  components/
    FeatureShowcase.tsx      ← new shared component
    DemoBlock.tsx            ← new shared component (clip or placeholder)
  app/
    page.tsx                 ← landing, uses FeatureShowcase × 5
    guide/
      GuideClient.tsx        ← guide, uses DemoBlock selectively
  public/
    clips/
      01-save.webm           ← record later
      02-read.webm
      03-listen.webm
      04-claude.webm
      05-write.webm
```

---

## Build Sequence

1. Build `DemoBlock` component (placeholder mode only, clip support stubbed in)
2. Build `FeatureShowcase` component (landing variant first)
3. Update `app/page.tsx` — add feature showcase below existing hero
4. Update `app/guide/GuideClient.tsx` — add `DemoBlock` to Claude Integration section
5. Test layout at 375px (mobile), 768px (tablet), 1280px (desktop)
6. Record clips (see recording setup above)
7. Drop clips into `public/clips/`, set `clipSrc` props
8. Add guide variant to `FeatureShowcase`, apply to guide sections that benefit

---

## Open Questions (decide before building)

1. **Motion library?** Currently using CSS + IntersectionObserver on landing. Guide uses same. For sticky-split we need the text column truly sticky. CSS `position: sticky` handles this without any library — confirm we're not adding `motion` / framer-motion.

2. **Clip format?** `.webm` (VP9) is smallest and best for Chrome/Firefox. Safari needs `.mp4` fallback. Use `<source>` with both:
   ```html
   <video autoplay loop muted playsinline>
     <source src="/clips/01-save.webm" type="video/webm">
     <source src="/clips/01-save.mp4" type="video/mp4">
   </video>
   ```
   Do we want to generate both formats from each clip? ffmpeg makes this trivial.

3. **Guide DemoBlock scope at launch** — ship guide redesign with just the Claude Integration demo block, or wait until all clips exist?
