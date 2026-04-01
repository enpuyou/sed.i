---
name: perf-audit
description: "Performance audit: bundle size, rendering, data fetching, images, code splitting."
user-invokable: true
argument-hint: "<area: bundle | rendering | data | images>"
---

# /perf-audit — Performance Audit

Systematic performance analysis producing measurable findings. Every finding
must include a before state, a proposed fix, and an expected improvement.
No hand-wavy "consider optimizing" — specific files, specific problems, specific
solutions.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

**Invoke**: `/perf-audit` (full audit) or `/perf-audit <area>` (e.g., `bundle`, `rendering`, `data`)

---

## Phase 0 · Baseline measurement

Before analyzing anything, capture the current state:

```bash
cd frontend && npx next build 2>&1 | grep -A 50 "Route (app)"
```

Record:
- Total build output size
- Largest routes/chunks
- Any build warnings

This gives concrete numbers to compare against after fixes are applied.

---

## Phase 1 · Bundle analysis

### 1a. Dependency weight
- Read `package.json` dependencies.
- Identify heavy libraries (>50KB gzipped): date-fns, tiptap, etc.
- Check if heavy libraries are tree-shaken properly (named imports vs default).
- Check for duplicate functionality (multiple libraries doing the same thing).

### 1b. Code splitting
- List all pages in `frontend/app/`.
- For each page, identify which heavy components are imported statically.
- Flag components that should use `next/dynamic`:
  - Components only visible after user interaction (modals, panels, editors).
  - Components behind feature flags.
  - Components only visible on certain routes.
- Check if `React.lazy` / `Suspense` is used appropriately.

### 1c. Next.js configuration
Read `next.config.ts` and assess:
- Image optimization settings (domains, formats, sizes).
- Compression settings.
- Experimental features that could help (optimizePackageImports, etc).
- Custom webpack configuration if any.

---

## Phase 2 · Rendering efficiency

### 2a. Re-render analysis
For each major component (>200 lines):
- Count `useState` calls. More than 5 in one component suggests state splitting.
- Check if state updates cause unnecessary re-renders of children.
- Look for inline object/array creation in JSX props (breaks shallow comparison).
- Look for missing `useCallback` on functions passed as props to children.
- Look for missing `useMemo` on expensive computations.

### 2b. Component memoization
- List components that receive complex props and render expensively.
- Check if `React.memo` is used where it would help.
- Do NOT recommend `React.memo` everywhere — only where profiling data or
  component tree analysis suggests it would help.

### 2c. Context optimization
For each React Context:
- What state does it hold?
- How many components consume it?
- Does updating one field re-render consumers that only need a different field?
- Would splitting the context reduce re-renders?

---

## Phase 3 · Data fetching

### 3a. Fetch inventory
List every `useEffect` that fetches data:
- Which component?
- What endpoint?
- When does it fire? (mount, dependency change, user action)
- Is the response cached? For how long?
- Could it be deduplicated with another fetch?

### 3b. Waterfall detection
- Are there sequential fetches that could be parallel? (`Promise.all`)
- Are there child components that fetch data that the parent already has?
- Are there fetches that block rendering when they could be streamed?

### 3c. Caching assessment
- Is there any client-side cache? (SWR, React Query, custom)
- Are API responses cached in the browser? (Cache-Control headers)
- Could stale-while-revalidate patterns reduce perceived load time?

### 3d. Optimistic update audit
- List all optimistic updates.
- Do they all have proper rollback on failure?
- Do they all show error feedback on failure?
- Are there mutations that should be optimistic but aren't?

---

## Phase 4 · Asset optimization

### 4a. Image audit
- Search for `<img` tags. These should generally be `<Image>` (next/image).
- Check for images without explicit dimensions (causes layout shift).
- Check for images without lazy loading.
- Check for large images served without responsive sizing.

### 4b. Font loading
- How are fonts loaded? (next/font, CSS @import, <link>)
- Are fonts preloaded?
- Is there a FOUT/FOIT flash?
- Are only needed weights/subsets loaded?

### 4c. CSS analysis
- Is Tailwind properly purging unused styles?
- Are there custom CSS files that could be converted to Tailwind?
- Are there large inline styles that could be extracted?

---

## Phase 5 · Prioritized findings

For each finding:

```
### [Severity] [Title]

**File(s)**: [Specific paths]
**Current state**: [What's happening now — measurable if possible]
**Proposed fix**: [Specific code change]
**Expected improvement**: [What gets better and by roughly how much]
**Effort**: S/M/L
**Risk**: Low/Medium/High (does this change behavior?)
```

### Severity levels:
- **Critical**: Measurably degrades user experience (>3s load, visible jank).
- **High**: Significant waste (large unused bundles, request waterfalls).
- **Medium**: Optimization opportunity (memoization, lazy loading).
- **Low**: Marginal improvement, nice-to-have.

---

## Phase 6 · Quick wins

Separately list fixes that are:
- Under 10 lines of code.
- Zero risk of behavior change.
- Immediately measurable.

These can be applied in the current session without a separate branch.

---

## What this skill does NOT do

- It does not run Lighthouse or Web Vitals (no browser automation).
- It does not profile runtime performance (no React DevTools).
- It analyzes code statically and proposes changes based on known patterns.
- It does not implement large refactors (use `/improve` to plan those).

---

## Cross-references

- For quick wins (Phase 6), apply them directly and ship with `/finalize`.
- For larger optimizations, plan the work with `/plan <optimization>`.
- For structural issues found during analysis, use `/improve` instead.
- After optimizations are shipped, run `/retro` to verify impact.
