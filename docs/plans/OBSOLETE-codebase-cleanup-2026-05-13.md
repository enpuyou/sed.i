---
type: plan
status: archived
last_updated: 2026-05-13
consumer: agent
---

# Plan: Codebase Cleanup — Improvement Plan Items
Date: 2026-05-13
Status: In Progress

## Goal
Execute all P0/P1/P2 items from `docs/improvement-plan-2026-05-13.md`. No new features — only cleanup, dead code removal, duplication reduction, and convention enforcement.

## Non-goals
- ReaderArticle.tsx decomposition (P1-B) — too large for this session; tracked as a separate plan item
- Extraction layer refactor (P3)
- Any new user-facing behavior

## Phases

### Phase 1 — Backend: DatabaseTask consolidation (P0)
**Goal**: Remove 4 duplicate `DatabaseTask` class definitions; all task files import from `base.py`.

**Changes**:
1. `app/tasks/embedding.py` — delete local `DatabaseTask`, add `DatabaseTask` to import from `base`
2. `app/tasks/tagging.py` — same
3. `app/tasks/summarization.py` — same
4. `app/tasks/cleanup.py` — same (doesn't import from base at all yet)

**Exit criteria**:
- [ ] `grep -r "class DatabaseTask" app/tasks/` returns only `base.py`
- [ ] `ruff check` passes
- [ ] Backend tests pass

---

### Phase 2 — Frontend: Delete 6 dead components (P1-A)
**Goal**: Remove unreachable code that creates confusion.

**Files to delete**:
- `frontend/components/ConfirmModal.tsx`
- `frontend/components/ProfileSettings.tsx`
- `frontend/components/SettingsCarousel.tsx`
- `frontend/components/SettingsPreview.tsx`
- `frontend/components/StatsCards.tsx`
- `frontend/components/SourcePane.tsx`

**Exit criteria**:
- [ ] Files deleted
- [ ] `tsc --noEmit` passes (no broken imports)
- [ ] `eslint` passes

---

### Phase 3 — Frontend: Fix error tone (P1-D)
**Goal**: Replace all "Failed to" user-visible strings with "Couldn't [action]. Try again."

**Files**: 9 component/page files + `lib/api.ts` (3 internal errors)

**Exit criteria**:
- [ ] `grep -r '"Failed to' frontend/` (excluding node_modules, console.error) returns 0 matches
- [ ] `tsc --noEmit` passes

---

### Phase 4 — Frontend: SearchModal InlineError (P2-A)
**Goal**: Replace `error: boolean` + plain div with `error: string | null` + `<InlineError>`.

**Files**: `frontend/components/SearchModal.tsx`

**Exit criteria**:
- [ ] Error state is `string | null`
- [ ] Error renders via `<InlineError>` with dismiss + retry
- [ ] `tsc --noEmit` passes

---

### Phase 5 — Frontend: useTagEditor hook (P1-C)
**Goal**: Extract duplicated tag management logic into a shared hook.

**New file**: `frontend/hooks/useTagEditor.ts`
**Modified**: `ContentItem.tsx`, `ContentCard.tsx`

**Exit criteria**:
- [ ] Hook exists at `frontend/hooks/useTagEditor.ts`
- [ ] Both components use it
- [ ] Tag add/remove behavior unchanged
- [ ] `tsc --noEmit` + `eslint` pass

---

### Phase 6 — Frontend: useConfirmAction hook (P2-C)
**Goal**: Extract confirm-delete arm/trigger pattern.

**New file**: `frontend/hooks/useConfirmAction.ts`
**Modified**: `ContentItem.tsx`, `ContentCard.tsx`, `RecordDetail.tsx`

**Exit criteria**:
- [ ] Hook exists
- [ ] All 3 components use it
- [ ] `tsc --noEmit` passes

---

### Phase 7 — Frontend: Split settings/page.tsx (P2-B)
**Goal**: Extract 4 inline sub-components into separate files.

**New files**: `frontend/components/settings/ReadingSection.tsx`, `FeatureVisibilitySection.tsx`, `PublicProfileSection.tsx`, `DangerZone.tsx`
**Modified**: `frontend/app/settings/page.tsx`

**Exit criteria**:
- [ ] `settings/page.tsx` under 200 lines
- [ ] `tsc --noEmit` + `eslint` + jest pass

---

## Commit strategy
One commit per phase. Each commit leaves the code in a working state. All commits go on the current branch (`enhancement/extension-reader-polish`) unless it gets merged before we finish — in which case start a new `improvement/cleanup` branch.

## Verification
- After each phase: `cd frontend && npx tsc --noEmit` (fast gate)
- After Phases 1–4: commit
- After Phases 5–7: `make lint` (full ruff + tsc + eslint) before committing
