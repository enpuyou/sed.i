# Design Language

Load this when building any new page, component, or UI section. The goal is
consistency with existing pages — not generic "clean" UI. The aesthetic is
**editorial, monospaced-functional, ink-on-paper**: warm cream backgrounds, tight
borders, no rounded corners, serif headings, mono UI text.

---

## The aesthetic in one sentence

Are.na meets a magazine index page: structured grid, quiet colors, serif for
editorial content, mono for UI chrome, borders instead of shadows, no decorative
elements.

---

## Color — always use CSS vars, never raw hex

```tsx
// bg
bg-[var(--color-bg-primary)]       // page background (warm cream / deep black)
bg-[var(--color-bg-secondary)]     // cards, inputs, navbar
bg-[var(--color-bg-tertiary)]      // hover states, thumbnail backgrounds

// text
text-[var(--color-text-primary)]   // headings, labels, primary content
text-[var(--color-text-secondary)] // body text, descriptions
text-[var(--color-text-muted)]     // metadata, timestamps, counts
text-[var(--color-text-faint)]     // very quiet labels, disabled

// borders
border-[var(--color-border)]       // default border
border-[var(--color-border-subtle)] // very light separator
hover:border-[var(--color-accent)]  // interactive hover

// accent (are.na blue)
text-[var(--color-accent)]
border-[var(--color-accent)]
```

Never use Tailwind color classes like `text-gray-500`, `bg-white`, `border-gray-200`.
Every color must go through a CSS var. Dark/sepia/true-black modes rely on this.

---

## Typography

| Use case           | Class                     | Notes                          |
| ------------------ | ------------------------- | ------------------------------ |
| Page headings      | `font-serif text-xl`      | Normal weight (`font-normal`)  |
| Card titles        | `font-serif text-base`    |                                |
| UI labels, buttons | `font-mono text-xs`       | Lowercase or sentence case     |
| Body / description | `text-sm` (default sans)  |                                |
| Metadata / counts  | `font-mono text-[11px]`   | Muted color                    |
| Nav links          | `font-mono text-xs`       | `tracking-widest uppercase` for mobile menu |

Rules:
- `font-serif` for anything editorial or heading-like.
- `font-mono` for UI chrome: buttons, labels, counts, nav, metadata.
- **Never** `font-bold` on `font-mono` labels — use `font-normal` or omit.
- `text-xs` is the default UI text size. Don't go larger for labels.

---

## Borders and shape

```tsx
rounded-none          // always. no rounded corners anywhere
border border-[var(--color-border)]   // standard card/input border
hover:border-[var(--color-accent)]    // interactive element hover
```

No `shadow-*`. No `rounded-*`. Borders define boundaries, not shadows.

---

## Buttons

The canonical button pattern from the Navbar and filters:

```tsx
<button className="compact-touch text-xs px-2 py-0.5 leading-none rounded-none border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors font-mono">
  Label
</button>
```

- `compact-touch` class ensures mobile tap targets.
- `px-2 py-0.5 leading-none` — tight. Not padded like Bootstrap buttons.
- `rounded-none` — always.
- Accent border on hover, not accent background.
- Destructive actions: `hover:border-red-400 hover:text-red-400` (no background fill).

---

## Inputs

```tsx
<input
  className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent)] transition-colors rounded-none"
  placeholder="..."
/>
```

No `rounded-*`, no focus ring, accent border on focus.

---

## Cards

Cards use border, no shadow, bg-secondary:

```tsx
<div className="border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:border-[var(--color-accent)] transition-colors">
  <div className="p-4">...</div>
</div>
```

- Hover: only border-color changes — no scale, no lift, no background color change.
- Thumbnail backgrounds: `bg-[var(--color-bg-tertiary)]`.
- Image: always `next/image`, never `<img>`.

---

## Page layout pattern

Every authenticated page follows this shell:

```tsx
<div className="min-h-screen bg-[var(--color-bg-primary)]">
  <Navbar />
  <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
    {/* content */}
  </main>
</div>
```

- `max-w-6xl` is the standard content width.
- `px-4 sm:px-6` for responsive horizontal padding.
- `py-8` top padding below navbar.
- Section headings: `font-serif text-xl font-normal text-[var(--color-text-primary)] mb-6`.

---

## State rendering — always in this order

```tsx
if (loading) return <RetroLoader />;
if (error)   return <InlineError message={error} onDismiss={() => setError(null)} />;
if (!items.length) return <EmptyState message="Nothing here yet" variant="bordered" />;
return <ItemList items={items} />;
```

Never two states at once. `RetroLoader` (not a spinner or skeleton) for loading.

---

## Dropdowns and menus

```tsx
<div className="absolute right-0 top-full mt-1 z-50 bg-[var(--color-bg-primary)] border border-[var(--color-border)] min-w-[110px]">
  <button className="block w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-accent)] transition-colors">
    Option
  </button>
</div>
```

No rounded corners on dropdowns. Border matches standard card style.

---

## What NOT to do

- No `rounded-lg`, `rounded-md`, `rounded-full` anywhere.
- No `shadow-*` of any kind.
- No Tailwind raw colors (`text-blue-500`, `bg-white`, `border-gray-200`).
- No decorative icons used as visual filler.
- No "hero" sections, gradient text, or glassmorphism.
- No inline `style={{ color: '#xxx' }}` — use CSS vars.
- No emoji in UI copy.
- Never use `font-bold` on mono UI labels.
- Never reach for a `toast` — use `InlineError`.

---

## Reference components

When building something new, read these first to match the pattern:

| What                  | Where                                               |
| --------------------- | --------------------------------------------------- |
| Card with hover       | `frontend/components/ListBlockCard.tsx`             |
| Page with list + empty state | `frontend/app/lists/page.tsx`                |
| Form with error       | `frontend/components/AddContentForm.tsx`            |
| Nav buttons           | `frontend/components/Navbar.tsx` (NavLink function) |
| Settings page layout  | `frontend/app/settings/page.tsx`                    |
| Inline error          | `frontend/components/InlineError.tsx`               |
| Empty state           | `frontend/components/EmptyState.tsx`                |
