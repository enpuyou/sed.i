---
type: plan
status: archived
last_updated: 2026-05-08
consumer: agent
---

# Plan: Strip full_text from List Response
Date: 2026-05-08
Status: Draft

## Goal
Remove `full_text` from the `GET /content` list response. The queue view never renders article bodies — sending them on every navigation wastes bandwidth and slows dashboard load, especially for users with many long articles.

## Non-goals
- Does not change pagination, filtering, or sort behavior
- Does not add caching (SWR/React Query) — that's a separate initiative
- Does not affect the reader page; `GET /content/{id}` continues returning full_text

## Current state
`ContentItemResponse` (the schema for both list and single-item responses) includes `full_text: str | None`. `ContentItemDetail` exists as a subclass but adds nothing — it's `class ContentItemDetail(ContentItemResponse): pass`. The reader calls `GET /content/{id}` which returns `ContentItemResponse` with full_text. `contentAPI.getFullById` hits `/content/{id}/full` and is never called anywhere.

---

## Architecture decisions

**Decision**: How to split the response schema
**Options considered**:
1. Add a new `ContentItemSummary` schema (no full_text) for the list endpoint, keep `ContentItemResponse` unchanged for single-item
2. Move `full_text` out of `ContentItemResponse` into `ContentItemDetail`; migrate reader to call `/full` endpoint
**Recommendation**: Option 2 — `ContentItemDetail` was designed for this. Migrate the reader to call `/full`. Clean separation with no new type proliferation.
**Reversibility**: Easy — a one-line schema change and a one-line frontend API call change.

---

## Phases

### Phase 1 — Backend schema split (Priority: P0)

**Goal**: `ContentItemResponse` loses `full_text`; `ContentItemDetail` gains it explicitly.

**Entry criteria**: Nothing — standalone change.

**Changes**:
1. `content-queue-backend/app/schemas/content.py`: Remove `full_text` from `ContentItemResponse`. Add `full_text: str | None` explicitly to `ContentItemDetail`.
2. `content-queue-backend/app/api/content.py:436` (`GET /content/{id}`): Change `response_model` from `ContentItemResponse` to `ContentItemDetail`. (The `/full` endpoint at line 466 already uses `ContentItemDetail` — no change needed there.)
3. Verify: `GET /content` list no longer includes `full_text`; `GET /content/{id}` and `GET /content/{id}/full` still include it.

**Exit criteria**:
- [ ] `GET /content` response payload does not contain `full_text` for any item
- [ ] `GET /content/{id}` response still contains `full_text`
- [ ] `make lint` passes (ruff, no type errors in backend)

**Risks**: None — additive schema split, no data loss.
**Estimated scope**: ~10 lines changed across 2 files.

---

### Phase 2 — Frontend migration (Priority: P0)

**Goal**: Reader calls the correct endpoint that returns `full_text`.

**Entry criteria**: Phase 1 merged and types regenerated.

**Changes**:
1. `frontend/app/content/[id]/page.tsx:83`: Change `contentAPI.getById(id)` to `contentAPI.getFullById(id)`. (The `getFullById` method already exists in `lib/api.ts:222`.)
2. `frontend/types/generated.ts`: Regenerate via `make generate-types` (or equivalent npm script) — `ContentItemResponse` will no longer have `full_text`, which is correct.
3. `frontend/types/index.ts`: If `ContentItem` re-exports `ContentItemResponse`, it now represents the list shape (no `full_text`). Add `ContentItemDetail` export for the reader if needed by TS types.

**Exit criteria**:
- [ ] Reader page loads and renders article body correctly
- [ ] `npx tsc --noEmit` passes with zero errors
- [ ] Network tab in browser shows `GET /content` responses without `full_text` field

**Risks**: If any component other than the reader accesses `item.full_text` from list data, it will get `undefined`. Grep confirms `ContentList.tsx` never touches `full_text`. The only consumer is the Reader path.
**Estimated scope**: ~5 lines changed, 1 file regenerated.

---

## Verification

### Automated
- `make lint` must pass (ruff + tsc + eslint)
- `pytest tests/test_content_api.py -x -q` — list endpoint tests should still pass; assert `full_text` not in response

### Manual
- Open dashboard: load time noticeably faster on a library with 20+ long articles
- Open any article in reader: renders correctly
- Open reader, press Escape back to queue: queue loads, reader navigates correctly

---

## Open questions
- None. Scope is narrow and risk is low.
