# Product Quality Execution Plan

Status: Draft v2 -- revised after full codebase audit (2026-03-30).
Scope: Backend error consistency, frontend UX/state reliability, design consistency, inline feedback, and mobile highlight notes.

---

## 1) Why this document exists

This plan translates the codebase audit into an execution roadmap that is:

- explicit about current broken behavior,
- explicit about the desired end state,
- split into small approval gates so changes can be approved one-by-one.

This is not an implementation PR. It is the execution contract for upcoming PRs.

---

## 2) Current broken behavior (baseline)

### 2.1 Backend error handling

1. **Rate limit returns wrong error shape**
   - Rate limit middleware returns `{error, message, detail}` while every other endpoint returns `{detail}`.
   - User impact: frontend error normalization must handle two shapes; easy to miss one.

2. **Rate limit CORS is hardcoded to localhost:3000**
   - 429 responses bypass the app-level CORS config and hardcode `Access-Control-Allow-Origin: http://localhost:3000`.
   - User impact: rate limit errors fail CORS in production -- users see a network error instead of "slow down" message.

3. **No `Retry-After` header on 429 responses**
   - User impact: frontend cannot tell the user when to retry.

4. **Embedding task marks failures as "completed"**
   - `processing_status` is set to `"completed"` even when embedding generation fails, with the error stored in a separate `processing_error` field.
   - User impact: frontend "still processing" detection logic can never fire for these items; user sees a completed item that silently has no embeddings.

5. **No global exception handler**
   - Unhandled SQLAlchemy errors (IntegrityError, OperationalError) return raw 500s that can leak internal details.
   - Pydantic validation errors expose internal type names (`value_error.email_invalid`).
   - User impact: unpredictable, sometimes technical, error messaging.

6. **Inactive user returns 400 instead of 403**
   - `get_current_user` raises `HTTPException(status_code=400)` for inactive accounts.
   - User impact: frontend 401-redirect logic does not catch this edge case.

### 2.2 Reader connections/highlights behavior

1. **Connections panel can show stale data after article switch**
   - `hasFetched` flag can prevent refetch when content changes in certain toggle sequences.
   - User impact: sees old connection results after navigation.

2. **Connections panel can show conflicting states**
   - Error and empty-state rendering conditions are not mutually exclusive.
   - User impact: cannot tell if there are truly no connections or if loading failed.

3. **Highlights operations fail silently**
   - Edit, delete, copy, and note-save failures are logged to console with no visible in-product feedback.
   - User impact: perceived "button did nothing" behavior.

4. **No highlights loading state**
   - When switching articles, highlights fetch runs silently -- no skeleton or loader.
   - User impact: brief flash of old highlights before new ones appear.

5. **Clipboard copy can fail silently**
   - "Copy all highlights" uses `navigator.clipboard.writeText()` with no error handling.
   - User impact: user clicks "Copy", sees "Copied" feedback, but clipboard may be empty.

### 2.3 Search behavior

1. **Search API failure is indistinguishable from no results**
   - If `searchAPI.semantic()` throws, the results array is emptied. User sees "No results found" -- same as a legitimate empty result.
   - User impact: cannot tell if query had no matches or if the system is down.

### 2.4 Writing/list workspace behavior

1. **Add-to-list failure is invisible**
   - List count is optimistically incremented, then silently decremented on API failure. No error message shown.
   - User impact: user thinks content was added; it wasn't.

2. **Remove-from-list failure is invisible**
   - 800ms "Removing..." animation plays, item vanishes, then silently reappears on failure. No error message.
   - User impact: confusing item reappearance with no explanation.

3. **Tag update failure is invisible**
   - Tags optimistically update, then silently revert on API failure.
   - User impact: user thinks tags were saved.

4. **Source pane load failure appears as empty data**
   - Failed fetches are console-only in key paths.
   - User impact: misinterprets backend/network failure as empty list.

5. **Sidebar list fetch can get stuck in loading state**
   - If the lists API call fails, sidebar shows "Finding your lists..." forever with no error or retry.

### 2.5 Vinyl/Crates silent failures

1. **Delete, status toggle, and cover update have empty catch blocks**
   - Not even `console.error` -- truly silent.
   - User impact: button appears to do nothing.

### 2.6 Design & styling inconsistencies

1. **Error presentation varies across surfaces**
   - Red left-bordered div (AddContentForm, ListModal), red inline text (ConnectionsPanel), status bar indicator (MarkdownEditor), nothing at all (highlight ops, vinyl ops).
   - User impact: inconsistent visual language -- hard to recognize errors.

2. **Empty state style/tone varies**
   - Some use emoji (connections panel), some use UPPERCASE ("NO TAGS FOUND"), some use friendly sentence case ("No content yet").
   - User impact: feels like different apps stitched together.

3. **Processing state is easy to miss**
   - Gray "Processing..." text blends with surrounding card content.
   - User impact: user may not realize an article is still being ingested.

4. **Auth page error type assertion is wrong**
   - Login/register pages cast errors to an Axios-style `{response.data.detail}` shape, but the app uses `fetch`. The path never matches; falls through to `error.message` by accident.

### 2.7 Frontend API layer inconsistencies

1. **Delete operations bypass `fetchWithAuth`**
   - `contentAPI.delete`, `highlightsAPI.delete`, and `vinylAPI.delete` each reimplement auth/error handling with direct `fetch()` calls.
   - User impact: no rate-limit checking on deletes, duplicated/divergent error logic.

2. **No typed error class**
   - All API errors are thrown as raw `Error` objects with string messages. Components must parse strings to distinguish error types.

---

## 3) End goal (target behavior)

By the end of this plan, the product should feel deterministic:

1. **Backend returns a consistent error shape**
   - All error responses (including rate limits, validation, 500s) use `{detail: string}`.
   - Rate limit responses include CORS headers from app config and a `Retry-After` header.

2. **Exactly one visible state per surface**
   - Loading, Error, Empty, Success are mutually exclusive everywhere.

3. **Inline feedback on every failed user action**
   - No toasts. Feedback appears contextually near the action that failed: small status text, inline error message, or action-area banner.
   - Pattern: follow the MarkdownEditor's "Saved" / "Save failed" approach consistently.

4. **Consistent visual language for errors and empty states**
   - One shared `InlineError` component for all error messages.
   - One shared empty-state pattern: muted text, sentence case, optional CTA. No emoji, no UPPERCASE for empty states.

5. **Reliable connection/highlight behavior**
   - Connections always reflect current article.
   - Highlights actions visibly confirm success/failure.

6. **Mobile highlight notes**
   - Users can add/view notes on highlights via bottom sheet on mobile.
   - Highlight summary accessible on mobile (not the full panel).

---

## 4) What "done" looks like in practice

### 4.1 User-visible acceptance snapshots

- **Connections panel**
  - Switching to article B while panel is open shows B's loading state first, then B's results.
  - If request fails, only error state is shown (with retry), not "No connections yet".

- **Highlights panel**
  - Failed note save shows inline error near the note field (e.g., "Couldn't save -- try again").
  - Failed delete shows inline error on the highlight card.
  - Copy-all failure shows feedback in the copy button area.

- **Search**
  - "No results found" is visually distinct from "Search failed -- try again".
  - Error state shows retry affordance.

- **List workspace**
  - Failed add-to-list shows error in the modal before closing.
  - Failed remove shows inline error where the item reappeared.
  - Failed tag update shows brief inline error near the tag area.

- **Sidebar**
  - Failed list fetch shows "Couldn't load lists" with a retry link, not infinite "Finding your lists...".

- **Vinyl/Crates**
  - Failed delete/toggle/update shows inline error near the action.

- **Error styling**
  - Every error message uses the same shared component: left red border, muted background, concise text.
  - Every empty state uses the same tone: sentence case, muted text, optional CTA.

- **Processing state**
  - Items being processed have a distinct visual treatment (subtle pulsing or background tint) so they stand out from loaded items.

- **Mobile highlights**
  - Tapping a highlight opens a bottom sheet with the note editor.
  - A "N highlights" summary is accessible from the reader on mobile.

### 4.2 Engineering acceptance criteria

- Backend: all error responses use `{detail: string}`, including rate limits and validation.
- Backend: global exception handler catches unhandled errors and returns sanitized `{detail}`.
- Backend: embedding task uses `processing_status = "failed"` on error.
- Frontend: shared `InlineError` component used by all surfaces.
- Frontend: shared empty-state pattern applied consistently.
- Frontend: delete APIs route through `fetchWithAuth`.
- Frontend: auth page error handling fixed (no Axios-style type assertion).
- State rendering conditions are exclusive and testable.
- Existing tests pass; new targeted tests added for fixed behaviors.

---

## 5) Phased execution plan (approve one-by-one)

Each phase is intended to be one focused PR unless complexity requires a split.

### Phase A -- Error & feedback foundation (P0)

#### Broken behavior addressed

- Backend: rate limit shape, CORS, Retry-After, global exception handler, embedding status.
- Frontend: delete API path divergence, auth page type assertion, no shared error/empty components.

#### Planned changes

**Backend:**
1. Standardize rate limit response to `{detail: "Too many requests. Please try again later."}`.
2. Fix rate limit middleware to read CORS origins from `ALLOWED_ORIGINS` env var.
3. Add `Retry-After` header to 429 responses.
4. Add global exception handler: catch `SQLAlchemyError` -> 500 with sanitized detail; catch `RequestValidationError` -> 422 with simplified detail.
5. Fix embedding task to set `processing_status = "failed"` (not `"completed"`) on error.
6. Change inactive-user error from 400 to 403.

**Frontend:**
7. Route all delete APIs through `fetchWithAuth` (remove duplicated fetch logic in contentAPI, highlightsAPI, vinylAPI).
8. Fix auth page error handling: remove Axios-style type assertion, use `err instanceof Error ? err.message : "..."` pattern.
9. Create shared `InlineError` component: left red border, muted bg, concise text, optional dismiss, optional retry.
10. Create shared empty-state pattern component: muted text, sentence case, optional icon, optional CTA.

#### End result preview

- Components receive stable error messages regardless of endpoint.
- No behavior mismatch between delete and non-delete flows.
- Every surface has access to consistent error and empty-state components.

#### Approval checkpoint

- Approve once API-layer diff is reviewed and smoke-tested with: one delete flow, one rate-limited flow, one validation error, and one forced 500.

---

### Phase B -- Reader side panels & search reliability (P0)

#### Broken behavior addressed

- Stale connections data, conflicting panel states, silent highlight failures, clipboard failure, search error indistinguishable from no results.

#### Planned changes

1. Reset connections fetch state (`hasFetched`, `connections`, `error`) when `contentId` changes.
2. Enforce exclusive state rendering order: loading > error > empty > data (in both ConnectionsPanel and HighlightsPanel).
3. Add inline feedback for highlight edit/delete/copy failures using `InlineError`.
4. Add loading skeleton/indicator when highlights are being fetched on article switch.
5. Add retry affordance for connections load errors.
6. Handle clipboard API failure in copy-all (show error in button area).
7. Add search error state distinct from no-results: "Something went wrong -- try again" with retry, vs "No results for {query}".

#### End result preview

- Opening connections always reflects the current article.
- Users always understand whether system failed or no data exists.
- Search failures are visible and retryable.

#### Approval checkpoint

- Approve after reader QA checklist passes on 2+ articles with one forced API failure scenario, and search tested with forced backend error.

---

### Phase C -- Writing/lists workspace & sidebar clarity (P1)

#### Broken behavior addressed

- Silent add-to-list, remove-from-list, tag update failures. Source pane ambiguity. Sidebar stuck loading. Vinyl silent failures.

#### Planned changes

1. Add inline error in AddContentToListModal when API call fails (show before closing modal).
2. Add inline error on remove-from-list failure where the item reappears.
3. Add inline error near tag area on tag update failure.
4. Add source pane visible error state + retry (using `InlineError`).
5. Fix sidebar: show "Couldn't load lists" with retry link on fetch failure, not infinite loading.
6. Fix vinyl/crates: add inline error feedback for delete, status toggle, and cover update failures (replace empty catch blocks).
7. Align all empty-state copy to shared pattern (sentence case, no emoji, no UPPERCASE).

#### End result preview

- Users can distinguish "no content" from "failed to load content" on every surface.
- Failed actions provide immediate inline explanation.
- Empty states feel cohesive across the app.

#### Approval checkpoint

- Approve after manual QA for: add-to-list failure, remove failure, tag failure, source-pane network failure, sidebar failure, vinyl delete failure.

---

### Phase D -- Design consistency & processing state (P1)

#### Broken behavior addressed

- Inconsistent error/empty styling, processing state hard to spot.

#### Planned changes

1. Replace all ad-hoc error displays with shared `InlineError` component across: AddContentForm, ConnectionsPanel, ListModal, ContentList, RecommendedSection, and anywhere else identified.
2. Replace all ad-hoc empty states with shared empty-state pattern.
3. Ensure filter dropdown uses sentence case ("No matches" not "NO MATCHES", "No tags found" not "NO TAGS FOUND").
4. Add distinct visual treatment for processing items: subtle pulsing background or left-border accent so they stand out in the list.
5. Audit error message copy for consistent tone: concise, no jargon, action-oriented where possible.

#### End result preview

- Every error looks the same. Every empty state looks the same.
- Processing items are visually distinct.
- The app feels like one product, not surfaces stitched together.

#### Approval checkpoint

- Approve after visual comparison: screenshot every error state, every empty state, every processing state. Confirm they all use shared components.

---

### Phase E -- Mobile highlight notes (P2)

#### Broken behavior addressed

- Mobile users can create highlights but cannot add or view notes.

#### Planned changes

1. Add bottom sheet component for mobile highlight interaction (tap highlight -> sheet slides up with note editor + color picker).
2. Add "N highlights" summary accessible from reader toolbar on mobile.
3. Remove `isMobile` early-return in InlineHighlight click handler; route mobile taps to bottom sheet instead.
4. Ensure HighlightToolbar "Note" button is visible on mobile (remove `hidden sm:block`), wired to open bottom sheet.

#### What stays hidden on mobile (intentional)

- Connections panel: discovery is a desktop task; not enough screen real estate.
- TOC sidebar: article is short enough to scroll on mobile.
- Full highlights panel: replaced by bottom sheet + summary.

#### End result preview

- Mobile users can tap a highlight, add/edit a note, pick a color, and dismiss.
- A small "highlights" badge in the reader toolbar shows count and opens summary.

#### Approval checkpoint

- Approve after testing on iOS Safari and Chrome Android: create highlight, add note, edit note, delete highlight.

---

### Phase F -- Validation & guardrails (P2)

#### Planned changes

1. Add/adjust targeted tests for panel state exclusivity and error rendering.
2. Add integration checks for API error normalization (backend returns consistent shape).
3. Add test for search error vs no-results distinction.
4. Update `ARCHITECTURE.md` for: error handling conventions, InlineError usage, empty-state pattern, mobile bottom sheet.

#### Approval checkpoint

- Approve after CI green and test coverage confirms corrected behaviors.

---

## 6) Suggested PR slicing

1. **PR-1: Phase A backend** -- rate limit fix, global handler, embedding status, inactive-user status code.
2. **PR-2: Phase A frontend** -- delete API consolidation, auth error fix, InlineError + empty-state components.
3. **PR-3: Phase B** -- reader side panels + search error state.
4. **PR-4: Phase C** -- lists/writing/sidebar/vinyl error feedback + empty-state unification.
5. **PR-5: Phase D** -- design consistency pass (replace all ad-hoc error/empty displays).
6. **PR-6: Phase E** -- mobile highlight notes (bottom sheet).
7. **PR-7: Phase F** -- tests + docs.

Each PR should include:

- scope statement,
- before/after behavior notes,
- validation commands run,
- risks and follow-ups.

---

## 7) Review checklist template (for each phase)

Use this checklist at each approval gate:

- [ ] Broken behavior is reproducible in baseline branch.
- [ ] Proposed fix is visible and testable in PR branch.
- [ ] Error, empty, loading, success states are mutually exclusive.
- [ ] Feedback appears inline near the action (no floating toasts).
- [ ] Message copy is concise, actionable, and consistent with other surfaces.
- [ ] Shared `InlineError` and empty-state components used (no ad-hoc styling).
- [ ] No unrelated behavior changes introduced.
- [ ] Tests/lint/build pass for touched areas.

---

## 8) Decision needed now

Approve **Phase A** first (backend + frontend foundation).

Reason: all downstream UX fixes (reader, lists, search, vinyl) become simpler and more reliable once error shape, shared components, and delete-path consistency are established.
