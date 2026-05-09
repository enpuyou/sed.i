# Frontend Patterns

Load this when doing any frontend (Next.js / React / TypeScript) work.

---

## Error feedback — InlineError only

**No toasts.** All errors use the `InlineError` component — inline, contextual, near the
action that failed. Import from `@/components/InlineError`.

```tsx
<InlineError
  message="Couldn't save. Try again."
  onDismiss={() => setError("")}
  onRetry={handleSubmit}   // optional
/>
```

Error message tone: **"Couldn't [action]. Try again."** — concise, no jargon, no "Failed to...".

---

## Empty states — EmptyState only

All empty-data states use the `EmptyState` component. Sentence case. No emoji.

```tsx
<EmptyState
  message="No articles saved yet"
  description="Paste a URL above to get started."
  actionLabel="Browse examples"   // optional
  onAction={handleBrowse}         // optional
  variant="bordered"              // "inline" | "bordered"
/>
```

---

## State rendering order — exclusive states

**Loading → Error → Empty → Data.** Never show two states at once.

```tsx
if (loading) return <RetroLoader />;
if (error)   return <InlineError message={error} onDismiss={() => setError("")} />;
if (!items.length) return <EmptyState message="Nothing here yet" />;
return <ItemList items={items} />;
```

---

## Optimistic updates

1. Update state immediately (before API call).
2. On failure: revert state, show `InlineError`.
3. Never block the UI waiting for a response on mutations.

---

## API calls — fetchWithAuth only

**All HTTP calls go through `fetchWithAuth` via the typed API helpers in `lib/api.ts`.**
Never call `fetch()` directly. The API helpers (e.g. `contentAPI.create()`,
`highlightsAPI.create()`) handle token injection, 401 redirect, and error shape.

```ts
// ✓ correct
const item = await contentAPI.create({ url });

// ✗ wrong — bypasses auth + error handling
const res = await fetch('/content', { ... });
```

---

## Backend error shape

All backend errors follow `{ detail: string }`. `fetchWithAuth` extracts `detail` and
throws it as `err.message`. Some endpoints return structured JSON in `detail` — parse with
`JSON.parse(err.message)` and check for expected keys before using.

---

## Image component

Always use `next/image` (`Image` from `'next/image'`), never `<img>`. Exception: cases
where the domain is unknown at build time and can't be added to `next.config.js`.

---

## Styling conventions

- CSS classes: `rounded-none` (not `rounded`), `var(--color-*)` for all colors
- Font: `font-serif` for headings, `font-mono` for UI/code elements
- Tailwind v4 — no `@apply`, no arbitrary values where a CSS var works
- Dark/light theme: always use `var(--color-bg-primary)`, `var(--color-text-primary)` etc.

---

## Feature flags

Gates for incomplete features — check before rendering optional sections:

```ts
import { SHOW_FOR_YOU, SHOW_CRATES } from '@/lib/flags';
if (SHOW_FOR_YOU) { ... }
```

Flags: `SHOW_FOR_YOU`, `SHOW_HIGHLIGHT_CONNECTIONS`, `SHOW_CRATES`, `SHOW_EDIT_ARTICLE`.
