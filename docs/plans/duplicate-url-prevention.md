---
type: plan
status: archived
last_updated: 2026-05-07
consumer: agent
---

# Plan: Duplicate URL Prevention on Ingest
Date: 2026-05-07
Status: Draft

## Goal
Prevent users from accidentally saving the same URL twice. Active content blocks re-ingestion with a friendly inline message. Soft-deleted content (deleted_at IS NOT NULL) is treated as gone — re-adding is allowed. Archived-but-present content surfaces a link to the existing item instead of silently failing or creating a duplicate.

## Non-goals
- URL deduplication across users
- Canonical URL resolution (redirects, URL shorteners, AMP variants)

---

## Current state

- `create_content_item` (content.py:120) creates a row immediately with no duplicate check
- `original_url` on `ContentItem` is `Text`, not unique — no DB constraint
- `AddContentForm.tsx` submits via `contentAPI.create({ url })`, catches errors, shows `InlineError` below the input
- Soft delete uses `deleted_at` timestamp; `is_archived` is a separate boolean flag (item still present in library)

---

## State matrix

| deleted_at | is_archived | Behavior |
|---|---|---|
| IS NOT NULL | any | Item is gone → allow re-add |
| IS NULL | false | Active item → block, show "Already in your library" + link |
| IS NULL | true | Archived item → block, show "Already in your library (archived)" + link |

---

## Architecture decisions

**Decision**: Where to enforce uniqueness
**Options**:
1. DB unique constraint only — Postgres enforces it, backend catches IntegrityError
2. Python check before insert only — no migration, but weaker (race conditions)
3. Both — Python check returns clean 409 error; DB constraint is safety net
**Recommendation**: Option 3. The Python check gives us a structured response with item `id` for the frontend link. The DB constraint is the last line of defense.
**Reversibility**: Easy — constraint can be dropped, check can be removed.

**Decision**: What the 409 response body contains
**Options**:
1. `{ detail: "Already in your library" }` — simple string
2. `{ detail: "...", existing_id: "uuid", is_archived: bool }` — structured
**Recommendation**: Option 2. The frontend needs the `id` to build a `/content/:id` link. Putting it in the error body avoids a second API call.
**Reversibility**: Easy.

**Decision**: URL normalization before comparison
- Strip trailing slash: `https://example.com/article/` → `https://example.com/article`
- Lowercase scheme+host: `HTTP://Example.com` → `http://example.com`
- Strip known tracking query params (`utm_*`, `fbclid`, `gclid`, etc.)
- Preserve non-tracking query params and sort them for deterministic comparison
- Drop fragments (`#section`) because they do not change article identity
- Do NOT resolve redirects (out of scope)
**Recommendation**: Apply normalization in a shared util function called on both write and lookup paths.

---

## Phases

### Phase 1 — Backend duplicate check + migration

**Goal**: Return a structured 409 when an active URL is re-submitted.

**Changes**:

1. `app/api/content.py` — add URL normalizer util + duplicate check before insert:
   ```python
   def normalize_url(url: str) -> str:
       url = url.strip().rstrip("/")
       parsed = urlparse(url)
       return parsed._replace(scheme=parsed.scheme.lower(),
                              netloc=parsed.netloc.lower()).geturl()
   ```
   Then before `new_item = ContentItem(...)`:
   ```python
   normalized = normalize_url(item_data.url)
   existing = db.query(ContentItem).filter(
       ContentItem.user_id == current_user.id,
       ContentItem.original_url == normalized,
       ContentItem.deleted_at.is_(None),
   ).first()
   if existing:
       raise HTTPException(
           status_code=409,
           detail=json.dumps({
               "message": "Already in your library",
               "existing_id": str(existing.id),
               "is_archived": existing.is_archived,
           }),
       )
   ```
   Also normalize `item_data.url` before storing: `new_item = ContentItem(original_url=normalized, ...)`

2. New migration `add_unique_constraint_content_url.py`:
   ```python
   # First dedupe active rows per (user_id, original_url), then add index
   op.execute("DELETE ...")

   # Partial unique index: only enforce uniqueness on active (non-deleted) rows
   op.execute("""
       CREATE UNIQUE INDEX uq_content_items_user_url_active
       ON content_items (user_id, original_url)
       WHERE deleted_at IS NULL
   """)
   ```
   This correctly allows re-adding a URL after soft-delete, and is safer than a full unique constraint.

**Exit criteria**:
- [ ] POST /content with duplicate active URL returns 409
- [ ] POST /content with a previously deleted URL returns 201
- [ ] Response body contains `existing_id` and `is_archived`
- [ ] `ruff check` passes

---

### Phase 2 — Frontend: parse 409 and show contextual message

**Goal**: Show a friendly inline message with a link to the existing item instead of a generic error.

**Changes**:

1. `frontend/components/AddContentForm.tsx` — update error handling in the catch block:
   ```tsx
   // In catch:
   if (err.status === 409) {
     try {
       const body = JSON.parse(err.message ?? err.detail ?? "");
       setDuplicateInfo({ id: body.existing_id, isArchived: body.is_archived });
     } catch {
       setError("Already in your library.");
     }
   } else {
     setError("Couldn't save link. Try again.");
   }
   ```
   Add `duplicateInfo` state: `{ id: string; isArchived: boolean } | null`

   Render below the input (instead of or alongside InlineError):
   ```tsx
   {duplicateInfo && (
     <p className="text-xs text-[var(--color-text-muted)] mt-1">
       Already in your library{duplicateInfo.isArchived ? " (archived)" : ""}.{" "}
       <Link href={`/content/${duplicateInfo.id}`} className="underline">
         View it →
       </Link>
     </p>
   )}
   ```
   Clear `duplicateInfo` on new input change (same as clearing `error`).

2. No new component needed — this is a one-off inline message, not a reusable error. It's not an error per se (no retry needed), so `InlineError` doesn't fit perfectly here; a plain `<p>` with a link is more appropriate.

**Exit criteria**:
- [ ] Submitting a duplicate URL shows the contextual message with "View it →" link
- [ ] Submitting a duplicate archived URL shows "(archived)" variant
- [ ] Changing the URL input dismisses the message
- [ ] `tsc --noEmit` passes
- [ ] `eslint` passes

---

### Phase 3 — ARCHITECTURE.md update

**Goal**: Document the new constraint and dedup behavior so future sessions don't re-solve this.

**Changes**:
1. `ARCHITECTURE.md` — add note to content ingestion section: partial unique index, normalization behavior, 409 shape.

**Exit criteria**:
- [ ] ARCHITECTURE.md updated

---

## Risks

**Risk**: Existing duplicate rows in DB conflict with new partial unique index
**Likelihood**: Medium
**Impact**: Medium (migration fails)
**Mitigation**: Deduplicate active rows during migration before creating the unique index.
**Detection**: Migration logs should show deduplication and successful index creation.

**Risk**: fetchWithAuth doesn't expose HTTP status code in the error object
**Likelihood**: Medium — need to verify how 409 surfaces in the catch block
**Impact**: Low — fallback to generic error message still works
**Mitigation**: Check `fetchWithAuth` implementation before writing frontend catch logic.

---

## Open questions

None — scope is clear. Ready to implement.
