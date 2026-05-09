# Codebase Health Sprint — 2026-05-08

A single session of structural improvements across backend, frontend, and developer
tooling. No user-facing features changed. Six initiatives shipped.

---

## 1. Repository orientation (agent harness)

**Intention:** The repository was not self-contained. Agents and new developers needed
tribal knowledge (pyenv shim workaround, undocumented env vars, scattered run commands)
to get started. Every session began with expensive orientation reads across multiple docs.

**What changed:**
- Created `CONTEXT.md` — domain glossary (Queue vs Library, ingestion pipeline, MCP layer)
- Added module-level docstrings to all backend Python modules so scope is visible at a glance
- Split `CLAUDE.md` from one 300-line file into a 93-line router + focused topic docs
  (`docs/instructions/backend-patterns.md`, `docs/instructions/frontend-patterns.md`,
  `docs/instructions/testing-standards.md`)
- Added `Makefile` at repo root with `make dev`, `make test`, `make lint`, `make migrate`,
  `make generate-types` — encoding the pyenv shim workaround so it's not tribal knowledge
- Completed `.env.example` and added `frontend/.env.local.example` — all 14 backend and
  7 frontend env vars documented with inline comments, none previously absent

**Impact:**
- All 5 foundational cold-start questions (what is this, how do I run it, what do I configure)
  are now answerable from the repo alone without prior context
- `make dev` replaces 4 manual commands previously spread across two docs
- Zero undocumented config — email, MCP OAuth, Discogs, PostHog, feature flags all now in `.env.example`

---

## 2. Search hydration N+1 fix

**Intention:** Every search path fired one database query per result row. At 50 results
(the cap), a single search triggered 51 queries. The pattern was duplicated independently
across keyword search, semantic search, and the `find_similar` MCP tool.

**What changed:**
- Added `hydrate_items()` to `app/core/hybrid_search.py` — bulk-fetches all result
  ContentItems in a single `WHERE id IN (...)` query, applies formatting, merges scores
- Replaced the N+1 loop in `keyword_search()`, `_semantic_search()`, and `find_similar()`
  (in `app/mcp/tools/content.py`) with calls to `hydrate_items()`
- Removed duplicated `_format_item` import from three call sites

**Impact:**
- Query count per search: O(n) → O(1)
  - 10 results: 11 queries → 2 queries
  - 50 results: 51 queries → 2 queries
- ~20 lines of duplicated loop logic replaced by one 35-line shared function
- Backend search test suite: 91 passed, 2 skipped, 0 failed

---

## 3. ContentIngestionService

**Intention:** The logic for saving a URL (normalize, deduplicate, create the DB row,
dispatch extraction) existed in two places — the HTTP API handler and the MCP write
tool. The MCP tool worked around this by importing private functions directly from the
HTTP handler, a layering violation. Adding any new ingestion rule required updating both.

**What changed:**
- Created `app/services/content.py` with:
  - `normalize_url()` — strips tracking params, lowercases scheme/host, removes fragment
  - `find_existing_active_item()` — indexed exact match + legacy normalization fallback
  - `DuplicateContentError` — typed exception carrying `existing_id` and `is_archived`
  - `ingest_url()` — single authoritative function: normalize → dedup → create → dispatch
- Updated `app/api/content.py` to delegate to `ingest_url()`, catch `DuplicateContentError`,
  and handle the extension path cleanly via `dispatch_extraction=False`
- Updated `app/mcp/tools/write.py` to import from `app.services.content` instead of the
  HTTP layer — layering violation eliminated
- Fixed 10 test `@patch` decorators that were patching `app.api.content.extract_metadata`
  (a module-level import that no longer exists) → now correctly patch
  `app.tasks.extraction.extract_metadata`

**Impact:**
- Layering violation eliminated: MCP tools no longer depend on HTTP handlers
- One place to change for any new normalization or deduplication rule
- Mock target corrected — tests were patching a non-existent attribute after the refactor
- Backend test suite: 262 passed, 1 xfailed, 0 failed

---

## 4. Type-safe API (APIError + generated TypeScript types)

**Intention:** Two independent type-safety gaps in the frontend caused silent failures and
brittle workarounds. Error handling required manual string parsing to recover structured
data from 409 responses. Frontend types were hand-maintained and had drifted from the
backend schema without any automated signal.

**What changed:**
- Added `APIError` class to `frontend/lib/api.ts` — carries `status`, `detail`, and `body`
  (the full parsed response); `fetchWithAuth` now throws `APIError` instead of `Error`
- Updated `AddContentForm` to use `err instanceof APIError && err.status === 409` instead
  of `JSON.parse(err.message)` for duplicate detection
- Updated `settings/page.tsx` to use `err.detail` instead of a regex against `err.message`
- Added `openapi-typescript` dev dependency + `generate-types` script to `package.json`
- Rewrote `types/index.ts` as a 9-line re-export wrapper over `types/generated.ts`
  (auto-generated from `GET /openapi.json`)
- Fixed two schema drifts caught by the generator:
  - `List.is_public` removed from frontend (never existed in `ListResponse`)
  - `content_vertical` + `vertical_metadata` added to backend `ContentItemResponse`
    (were in the DB model and update schema but excluded from the response — frontend
    was silently reading `undefined`)
- Updated `StatusIndicator` prop to accept `string` (generated schema gives `string`,
  not a union literal, for computed fields)
- Updated test mocks to use `jest.requireActual` so `instanceof APIError` works correctly

**Impact:**
- 2 fragile string-parsing hacks eliminated
- 2 schema drift bugs surfaced and fixed (one backend, one frontend)
- `types/index.ts` can never silently drift again — `npm run generate-types` (or
  `make generate-types`) regenerates it; TypeScript surfaces all affected call sites
- Frontend: 120 passed, 0 failed | Backend: 262 passed, 1 xfailed, 0 failed
