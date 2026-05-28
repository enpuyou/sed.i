# Plan: Agent Harness & Architecture Improvements
Date: 2026-05-07
Status: Draft

## Goal

Make the repository the single source of truth for agents, reduce context waste, and deepen
shallow modules so bugs and changes concentrate in fewer places. Every improvement here is
justified by one of two goals: **agent reliability** (fewer failures from missing context,
type drift, or hard-to-test code) or **locality** (changes, bugs, and knowledge concentrate
in one place rather than spreading across callers).

Informed by:
- Harness Engineering Lectures 03 & 04 (Walking Labs)
- `improve-codebase-architecture` skill (Ousterhout depth/seam framework)
- Codebase audit conducted 2026-05-07

---

## Non-goals

- Rewriting working features
- Adding new user-facing functionality
- Changing the deployment pipeline or infrastructure
- Introducing a monorepo structure

---

## Vocabulary (skill framework)

| Term | Meaning |
|---|---|
| **Module** | Anything with an interface and an implementation |
| **Interface** | Everything a caller must know: types, invariants, error modes, ordering |
| **Depth** | Leverage at the interface — lots of behaviour behind a small interface |
| **Seam** | Where an interface lives; a place behaviour can change without editing inline |
| **Adapter** | A concrete thing satisfying an interface at a seam |
| **Leverage** | What callers get from depth: capability per unit of interface learned |
| **Locality** | What maintainers get: change/bugs/knowledge concentrate in one place |
| **Deletion test** | Delete the module — does complexity vanish (pass-through) or reappear at N callers (earning its keep)? |

---

## Current state snapshot

### What's already good
- `ARCHITECTURE.md` — comprehensive, kept updated, listed as required maintenance
- `CLAUDE.md` — stack, patterns, skill workflow documented
- `docs/mcp-server.md` — full OAuth + tool reference
- `frontend/tsconfig.json` — `strict: true`
- Backend schemas (Pydantic) and frontend types (TS interfaces) exist

### Gaps driving this plan
| Gap | Impact |
|---|---|
| `CLAUDE.md` is 100+ lines and will grow — "lost in the middle" degrades agent compliance | Agent misses constraints buried mid-file |
| No `CONTEXT.md` domain glossary — agents rediscover "what is a ContentItem?" every session | Discovery cost per session |
| No module-level docstrings on backend modules | Agent reads entire file to understand scope |
| Frontend `types/index.ts` hand-maintained to match backend Pydantic models | Schema drift is silent; found broken `author` field in production this week |
| `create_content_item` handler does 6 things inline (120+ lines) | Untestable without HTTP; extension path / normal path branching explodes test matrix |
| `hydrate_results` (fetch IDs → N+1 per-row query → format) duplicated in hybrid_search and MCP tools | N+1 in two places; fix must be applied twice |
| `fetchWithAuth` throws bare `Error` with no status code — callers JSON-parse strings inside strings | Fragile per-endpoint error handling; 409 workaround proves this |
| No unified dev-stack command; pyenv shim issue documented only in memory | Agent spends tokens reconstructing startup every session |
| `.env.example` missing Resend, PostHog, MCP OAuth, CORS vars | Cold-start test fails on "how do I configure this?" |

---

## Initiatives

Six initiatives, ordered from lowest to highest implementation risk. Each is independently
shippable and has explicit verification steps.

---

### Initiative 1 — CONTEXT.md + Module-level docstrings
**Priority: Do first.** Zero risk, high leverage per session.

#### 1a. Create `CONTEXT.md`

The domain glossary. The `improve-codebase-architecture` skill requires this file to name
good seams. Right now domain vocabulary is scattered across 800+ lines of ARCHITECTURE.md.

**File:** `CONTEXT.md` at repo root

**Contents (30-40 entries, one paragraph each):**

```
## ContentItem
A URL a user has saved to their library. Has a lifecycle: pending → processing →
completed (or failed). Stores the original URL (normalized), extracted metadata
(title, author, description), full article text (HTML), an embedding for semantic
search, and reading state (is_read, read_position, is_archived). Soft-deleted via
deleted_at. Never hard-deleted.

## Queue
The user's list of unread, non-archived ContentItems. The primary view of the app.

## Library
All of a user's ContentItems regardless of read/archived status. The superset.

## Ingestion
The pipeline from URL submission to a completed ContentItem. Two paths:
- Normal path: URL → pending item → Celery extraction task → completed item
- Extension path: URL + pre-extracted HTML → skip Celery → completed item immediately

## Processing
The Celery phase that fills in a ContentItem's metadata and full_text after ingestion.
Includes: metadata extraction, HTML cleaning, embedding generation, auto-tagging.

## Reader
The in-app full-article reading view. Uses Reader.tsx (with fixed navbar, progress bar,
TOC, sidebars) which embeds ReaderArticle.tsx (the article body, usable in split-pane).

## Highlight
A user's text selection within a ContentItem. Has an embedding for cross-article
connection search. Stored with character offsets for re-rendering.

## List
A user-defined named collection of ContentItems. Can be shared/public. Separate from
the Queue (which is implicit, not stored as a List).

## Draft
A piece of writing the user is working on, associated with a List. Stored as markdown
blocks via the editor system.

## Record (Vinyl)
An entry in the user's vinyl collection. Has tracks, videos, ratings, genres. Fetched
from Discogs in a Celery task.

## Hybrid Search
The search system that classifies a query and routes it to: keyword (tsvector),
filter (SQL), semantic (pgvector embedding), or RRF-fused combination. Lives in
app/core/hybrid_search.py and app/core/search_router.py.

## MCP
The Model Context Protocol server that exposes sed.i tools to AI assistants (Claude
Desktop, etc.). Two transport modes: stdio (local) and HTTP+OAuth2.1 (cloud). Tools
live in app/mcp/tools/. OAuth lives in app/mcp/oauth.py.

## Normalization (URL)
The process of stripping tracking params (UTM, fbclid, etc.), lowercasing scheme+host,
removing trailing slash, and dropping fragment before storing or comparing URLs. Lives
in normalize_url() in app/api/content.py.

## Reading Status
Computed from (is_read, read_position, is_archived):
- archived → is_archived=True
- read → is_read=True OR read_position ≥ 0.9
- in_progress → read_position > 0
- unread → otherwise
```

*(Plus entries for: InlineError, EmptyState, fetchWithAuth, Feature Flag, Processing Status,
Celery Task, Embedding, Cosine Similarity, Partial Unique Index)*

#### 1b. Module-level docstrings on backend Python files

Each Python module in `app/` currently has no module-level docstring. An agent opening
`app/core/hybrid_search.py` reads the full file to understand its scope.

**Add a 3-5 line docstring at the top of each module:**

- `app/api/content.py` → "Content item CRUD endpoints. Owns the ingestion seam: URL normalization, duplicate detection, item creation, list membership, and task dispatch."
- `app/core/hybrid_search.py` → "Unified search entry point. Classifies queries and routes to keyword (tsvector), filter (SQL), semantic (pgvector), or RRF-fused hybrid. Never raises — returns [] on failure."
- `app/core/search_router.py` → "Query classifier and filter-query parser. Detects author/tag/date operators in query strings and routes accordingly."
- `app/mcp/tools/content.py` → "MCP tool implementations for content: get_content_item, search_content, find_similar. Read-only. Uses _format_item for serialization."
- `app/tasks/extraction.py` → "Celery task for URL fetch + metadata extraction. Entry point: extract_metadata.delay(item_id). Writes back to ContentItem on completion."
- *(all other modules similarly)*

#### Verification

```bash
# 1. CONTEXT.md exists and is readable
cat /path/to/CONTEXT.md | wc -l  # should be 100-200 lines

# 2. Cold-start test: fresh agent session, ask these 5 questions from repo contents only:
#    a. What is a ContentItem?
#    b. What is the difference between Queue and Library?
#    c. What happens when a URL is submitted?
#    d. What is Hybrid Search?
#    e. What is MCP?
#    All should be answerable from CONTEXT.md alone.

# 3. Module docstrings present
grep -rL '"""' content-queue-backend/app --include="*.py" | grep -v __init__ | grep -v test
# Should return empty (every non-init module has a docstring)

# 4. No regressions
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/ -x -q
cd frontend && npx tsc --noEmit && npx eslint . --max-warnings=0
```

---

### Initiative 2 — Split CLAUDE.md into a router + topic documents

**Priority: Do second.** Zero code risk. Directly addresses "lost in the middle" failure mode.

#### Problem

Current `CLAUDE.md` is 100+ lines and growing. Per Lecture 04: a 100+ line instruction file
causes "lost in the middle" degradation — constraints buried mid-file are missed. The file
mixes: stack reference, UI patterns, error conventions, API conventions, workflow skills,
debugging notes. An agent working on a backend task loads all the frontend patterns anyway.

#### Solution

Keep `CLAUDE.md` as a 50-line router (overview, commands, ≤15 hard constraints, links).
Move topic detail to `docs/instructions/` files loaded on demand.

**New structure:**

```
CLAUDE.md                          ← 50 lines: project overview, run commands, 10 hard rules, links
docs/instructions/
  frontend-patterns.md             ← InlineError, EmptyState, fetchWithAuth, state rendering order
  backend-patterns.md              ← Error shape, exception handlers, Celery patterns, SQLAlchemy
  engineering-workflow.md          ← (already exists, already linked — keep as-is)
  testing-standards.md             ← How to run tests, what to test, coverage expectations
  mcp-patterns.md                  ← Already in docs/mcp-wiki.md — link, don't duplicate
```

**New CLAUDE.md structure:**

```markdown
# sed.i — Claude Code Instructions

## What this is
[2 sentences]

## Run commands
[backend, worker, frontend — each one line with the correct pyenv invocation]

## Hard constraints (always apply)
1. No toasts — use InlineError
2. fetchWithAuth is the only API path
3. Backend error shape: {detail: string}
4. ARCHITECTURE.md updated in same commit as feature change
5. Optimistic updates: update UI → revert on failure → show InlineError
6. Loading > Error > Empty > Data — never show two states at once
7. EmptyState for all empty data — sentence case, no emoji
8. Error tone: "Couldn't [action]. Try again."
9. No backwards-compat shims for removed code
10. Run tsc --noEmit + ruff check before committing

## When to read more
- Frontend UI work → docs/instructions/frontend-patterns.md
- Backend API / DB work → docs/instructions/backend-patterns.md
- Testing → docs/instructions/testing-standards.md
- MCP tools → docs/mcp-wiki.md
- Skill workflow → docs/skills-workflow-guide.md
```

#### Verification

```bash
# 1. CLAUDE.md line count
wc -l CLAUDE.md  # must be ≤ 60 lines

# 2. Every rule in new CLAUDE.md is a hard constraint (not a soft guideline)
# Manual check: read all 10 rules — each should be a binary pass/fail, not a suggestion

# 3. Topic docs exist and are linked
ls docs/instructions/  # frontend-patterns.md, backend-patterns.md, testing-standards.md

# 4. Cold-start test: agent given only CLAUDE.md can still answer:
#    a. How do I show an error to the user?
#    b. Where do I find frontend patterns?
#    c. What is the backend error shape?
#    d. How do I run the backend tests?

# 5. No regressions — full test suite passes
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/ -x -q
cd frontend && npx tsc --noEmit
```

---

### Initiative 3 — `ContentIngestionService`: deepen the create_content_item handler

**Priority: Third.** Moderate risk (backend only, well-tested area). Highest code-quality win.

#### Problem

`create_content_item` in `app/api/content.py` does 6 things inline across 120+ lines:
1. URL normalization
2. Duplicate detection (raises 409 with JSON payload)
3. DB insert
4. List membership loop (with per-list DB query inside — N+1 risk)
5. Extension path: HTML cleaning, metadata assignment, word count, two task dispatches
6. Normal path: single task dispatch

**Deletion test:** delete the handler — the logic reappears across... nothing. It's trapped
with no seam. There's already an `app/services/` directory with an empty `__init__.py` —
this was anticipated but never populated.

#### Solution

Extract `ingest_content(url, user, db, list_ids, pre_extracted)` into `app/services/content.py`.
The HTTP handler becomes a thin adapter: parse HTTP input → call service → return response.

```python
# app/services/content.py
def ingest_content(
    url: str,
    user: User,
    db: Session,
    *,
    list_ids: list[str] | None = None,
    pre_extracted: PreExtractedData | None = None,
) -> ContentItem:
    """
    Create a new ContentItem from a URL.

    Raises DuplicateContentError if an active item with this URL exists.
    Dispatches extraction task(s) as a side effect (unless pre_extracted).
    """
    ...
```

The seam is `ingest_content()`'s interface. The HTTP handler and any future ingest path
(email, API, extension) all call it. Tests call it directly with a real DB (local-substitutable
dependency — no FastAPI needed).

**New `DuplicateContentError`** replaces the inline `HTTPException(409, ...)`:
- Service raises `DuplicateContentError(existing_id, is_archived)`
- Handler catches it and maps to 409
- MCP `add_content` tool catches it and returns a structured error message
- No more JSON-inside-detail hacks

**List membership:** batch-load all lists in one query (not per-list), then insert. Removes
the N+1 inside the handler.

#### Files changed
- `app/services/__init__.py` — export `ingest_content`, `DuplicateContentError`
- `app/services/content.py` — new file, owns ingestion logic
- `app/api/content.py` — handler becomes ~20 lines, imports from services
- `app/mcp/tools/write.py` — `add_content` tool catches `DuplicateContentError` instead of generic Exception
- Tests: new `tests/test_content_service.py` testing the service function directly

#### Verification

```bash
# 1. Handler is now thin
grep -c "def create_content_item" content-queue-backend/app/api/content.py  # still 1
# Count lines in handler body — should be < 25
# (Manual: read the function, it should only: parse → call service → return)

# 2. Service file exists and has tests
ls content-queue-backend/app/services/content.py  # exists
ls content-queue-backend/tests/test_content_service.py  # exists

# 3. All existing content API tests still pass
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/test_content_api.py tests/test_content_extended.py -v

# 4. New service tests pass (covers: normal path, extension path, duplicate active URL,
#    duplicate deleted URL allowed, list attachment, invalid list ID ignored)
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/test_content_service.py -v

# 5. Full test suite passes
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/ -x -q

# 6. Ruff clean
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run ruff check app/
```

---

### Initiative 4 — Deepen `hydrate_results`: fix N+1 and eliminate duplication

**Priority: Fourth.** Low risk, isolated to search + MCP modules.

#### Problem

Three places do the same thing: fetch a list of item IDs from a query, then load each
ContentItem one-by-one in a loop, then call `_format_item()`:

1. `keyword_search()` in `app/core/hybrid_search.py` (lines ~145-155)
2. `_semantic_search()` in `app/core/hybrid_search.py` (lines ~311-317)
3. `find_similar()` in `app/mcp/tools/content.py` (lines ~135-160)

Each fires N+1 queries for N results. **Deletion test:** delete the per-row loop in one
place — it reappears in the other two. The pattern is earning its keep but at the wrong seam.

The search bug fixed earlier in this session (`str(item.id)` vs `UUID`) was a symptom of
this: the bulk-load in `app/api/search.py` was added to fix N+1 but introduced a type mismatch
because it was duplicating logic that already existed elsewhere.

#### Solution

A single `hydrate_items(rows: list[dict], db: Session) -> list[dict]` function in
`app/core/hybrid_search.py` (or a new `app/core/search_utils.py`):

```python
def hydrate_items(rows: list[dict], db: Session) -> list[dict]:
    """
    Bulk-load ContentItems for search result rows and apply _format_item.

    rows: list of dicts each containing at least {"id": str, "score": float, ...}
    Returns the same list with full item fields merged in. Rows with no matching
    ContentItem are silently dropped.
    """
    from app.mcp.tools.content import _format_item
    ids = [r["id"] for r in rows]
    items_by_id = {
        str(item.id): item
        for item in db.query(ContentItem).filter(ContentItem.id.in_(ids)).all()
    }
    result = []
    for row in rows:
        item = items_by_id.get(row["id"])
        if item:
            d = _format_item(item, include_full_text=False)
            d.update({k: v for k, v in row.items() if k not in d})
            result.append(d)
    return result
```

All three callers replace their loop with `hydrate_items(rows, db)`.

#### Files changed
- `app/core/hybrid_search.py` — add `hydrate_items()`, replace loops in `keyword_search` and `_semantic_search`
- `app/mcp/tools/content.py` — replace loop in `find_similar` with `hydrate_items`
- `app/api/search.py` — replace the inline bulk-load block with `hydrate_items`

#### Verification

```bash
# 1. No per-row ContentItem query loops remain in search code
grep -n "db.query(ContentItem).filter(ContentItem.id ==" \
  content-queue-backend/app/core/hybrid_search.py \
  content-queue-backend/app/mcp/tools/content.py \
  content-queue-backend/app/api/search.py
# Should return 0 matches (only hydrate_items does this now)

# 2. hydrate_items exists
grep -n "def hydrate_items" content-queue-backend/app/core/hybrid_search.py  # found

# 3. Search API returns results (manual test)
curl -s "http://localhost:8000/search/semantic?query=test&limit=5" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -20
# Should return results array, not []

# 4. MCP find_similar returns results (manual test via Claude Desktop or MCP inspector)

# 5. All search tests pass
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/test_search_api.py -v

# 6. Full test suite
cd content-queue-backend && PYENV_VERSION=3.11.12 poetry run pytest tests/ -x -q
```

---

### Initiative 5 — Type-safe API: generated TS types + typed error shape

**Priority: Fifth.** Moderate frontend risk. Eliminates the whole class of schema-drift bugs.

#### Problem

`frontend/types/index.ts` is hand-maintained to match backend Pydantic schemas. They drift
silently. The `author` field missing from the search response (found and fixed this session)
is a recent example. The `fetchWithAuth` error handling requires `JSON.parse(err.message)` —
a three-layer unwrap that breaks if the error shape changes.

#### Solution (two parts)

**Part A — Generate TS types from OpenAPI**

FastAPI already emits `/openapi.json`. Use `openapi-typescript` to generate types:

```bash
# Add to frontend package.json scripts:
"generate-types": "openapi-typescript http://localhost:8000/openapi.json -o src/generated/api.ts"
```

`types/index.ts` becomes a re-export file that re-exports from `generated/api.ts` with
any local aliases needed. The generated file is committed to the repo (not gitignored) so
agents can read it without running the backend.

When the backend schema changes, run `pnpm generate-types` and the TS compiler immediately
surfaces all affected call sites.

**Part B — Typed `APIError`**

Replace `throw new Error(string)` in `fetchWithAuth` with:

```typescript
class APIError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly data?: unknown,
  ) {
    super(detail);
  }
}
```

The 409 catch in `AddContentForm` becomes:
```typescript
} catch (err) {
  if (err instanceof APIError && err.status === 409) {
    const body = err.data as { existing_id: string; is_archived: boolean };
    setDuplicateInfo({ id: body.existing_id, isArchived: body.is_archived });
  } else {
    setError(err instanceof APIError ? err.detail : "Couldn't add link. Try again.");
  }
}
```

No more `JSON.parse(err.message)`.

#### Files changed
- `package.json` — add `openapi-typescript` dev dependency + `generate-types` script
- `frontend/src/generated/api.ts` — new generated file (committed)
- `frontend/types/index.ts` — becomes thin re-export wrapper
- `frontend/lib/api.ts` — `fetchWithAuth` throws `APIError`, exports `APIError` class
- `frontend/components/AddContentForm.tsx` — use `APIError` instanceof check
- Any other component catch blocks that currently use `err.message` directly

#### Verification

```bash
# 1. Generated types exist and are fresh
ls frontend/src/generated/api.ts  # exists
# Run generator and check no diff (means backend and frontend are in sync):
cd frontend && pnpm generate-types
git diff --stat src/generated/api.ts  # should be empty on a clean repo

# 2. TypeScript compiles cleanly with new types
cd frontend && npx tsc --noEmit  # 0 errors

# 3. APIError is exported and used
grep -n "APIError" frontend/lib/api.ts  # class definition present
grep -rn "instanceof APIError" frontend/components/  # at least AddContentForm

# 4. No raw JSON.parse(err.message) patterns remain
grep -rn "JSON.parse(err.message\|JSON.parse(error.message" frontend/  # 0 results

# 5. ESLint clean
cd frontend && npx eslint . --max-warnings=0

# 6. Full frontend type check
cd frontend && npx tsc --noEmit

# 7. Manual test: submit duplicate URL in UI → "Already in your library" message appears
# Manual test: submit URL with network error → "Couldn't add link. Try again." appears
```

---

### Initiative 6 — Makefile + complete `.env.example`

**Priority: Last.** Zero code risk. Pays back every session and CI run.

#### Problem A — No unified dev-stack command

Starting the dev stack requires 4 commands spread across two docs, plus the pyenv shim
issue documented only in agent memory. Per Lecture 03: "information that doesn't exist in
the repo doesn't exist for the agent."

#### Solution A — Makefile at repo root

```makefile
.PHONY: dev backend worker frontend migrate test test-backend test-frontend generate-types lint

PYENV_RUN = cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run

# Start all services (requires tmux or runs sequentially in bg)
dev:
	$(MAKE) -j3 backend worker frontend

backend:
	$(PYENV_RUN) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYENV_RUN) celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat

frontend:
	cd frontend && pnpm dev

migrate:
	$(PYENV_RUN) alembic upgrade head

test: test-backend test-frontend

test-backend:
	$(PYENV_RUN) pytest tests/ -x -q

test-frontend:
	cd frontend && npx jest --ci --passWithNoTests

lint:
	$(PYENV_RUN) ruff check app/
	cd frontend && npx tsc --noEmit && npx eslint . --max-warnings=0

generate-types:
	cd frontend && pnpm generate-types
```

Update `CLAUDE.md` to reference `make test`, `make migrate`, `make lint` instead of inline
commands. MEMORY.md pyenv note becomes redundant (the Makefile encodes it).

#### Problem B — Incomplete `.env.example`

Current `.env.example` is missing ~8 required vars. A cold-start agent (or new developer)
cannot configure the app from the example alone.

#### Solution B — Complete `.env.example`

Add all missing vars with placeholder values and comments:

```bash
# === Database ===
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/content_queue

# === Cache / Queue ===
REDIS_URL=redis://localhost:6379/0

# === Auth ===
SECRET_KEY=your-secret-key-replace-this
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# === OpenAI ===
OPENAI_API_KEY=sk-your-openai-api-key

# === Email (Resend) — optional, email features disabled if absent ===
RESEND_API_KEY=re_your_resend_api_key
FROM_EMAIL=noreply@yourdomain.com

# === Analytics (PostHog) — optional ===
POSTHOG_API_KEY=phc_your_posthog_key
POSTHOG_HOST=https://us.i.posthog.com

# === MCP OAuth ===
MCP_CLIENT_ID=your-mcp-client-id
MCP_CLIENT_SECRET=your-mcp-client-secret
MCP_REDIRECT_URI=http://localhost:8000/mcp/callback

# === CORS ===
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001

# === Feature flags ===
DEBUG=True
```

Also add a frontend `.env.local.example`:
```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_POSTHOG_KEY=phc_your_posthog_key
NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
```

#### Verification

```bash
# 1. Makefile exists and make lint passes
make lint  # should run ruff + tsc + eslint, all pass

# 2. make test runs both suites
make test  # both backend and frontend tests pass

# 3. Cold-start test for env vars
# Fresh checkout — can a new developer configure the app using only .env.example?
# Check: every var referenced in app/core/config.py has a corresponding entry in .env.example
grep "os.getenv\|settings\." content-queue-backend/app/core/config.py | \
  grep -oP '(?<=getenv\(")[^"]+' | sort > /tmp/config_vars.txt
grep -oP '^[A-Z_]+(?==)' content-queue-backend/.env.example | sort > /tmp/example_vars.txt
diff /tmp/config_vars.txt /tmp/example_vars.txt  # ideally empty or explained gaps

# 4. make generate-types runs without error
make generate-types

# 5. MEMORY.md pyenv note can be retired (the Makefile encodes it)
# (optional cleanup — don't break existing sessions)
```

---

## Phasing and sequencing

```
Week 1 (no code risk):
  Initiative 1 — CONTEXT.md + module docstrings       [~3 hours]
  Initiative 2 — Split CLAUDE.md                       [~2 hours]
  Initiative 6 — Makefile + .env.example               [~1 hour]

Week 2 (backend refactor):
  Initiative 4 — hydrate_items (isolated, low risk)    [~2 hours]
  Initiative 3 — ContentIngestionService               [~4 hours]

Week 3 (frontend schema):
  Initiative 5 — Generated types + APIError            [~3 hours]
```

Each initiative is independently committable. Do not bundle them.

---

## Cross-cutting verification (run after each initiative and after all)

```bash
# Backend full suite
cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/ -x -q

# Backend lint
cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check app/

# Frontend type check
cd frontend && npx tsc --noEmit

# Frontend lint
cd frontend && npx eslint . --max-warnings=0

# Frontend tests
cd frontend && npx jest --ci --passWithNoTests

# Manual smoke test after any backend change:
# 1. POST /content with a new URL → 201, item appears in list
# 2. POST /content with same URL again → 409, "Already in your library" in UI
# 3. Search for a word from an article → results appear
# 4. Open an article in Reader → article renders, scroll progress saves
```

---

## Risk register

| Initiative | Risk | Mitigation |
|---|---|---|
| 1 — CONTEXT.md | Vocabulary diverges from actual code over time | Add to ARCHITECTURE.md update rule: "if you rename a concept, update CONTEXT.md" |
| 2 — Split CLAUDE.md | Topic doc not loaded when needed, agent misses constraint | Each topic doc starts with: "Load this when working on [X]". Cross-link from CLAUDE.md. |
| 3 — ContentIngestionService | Existing tests couple to HTTP layer and break | Write service-layer tests first. Keep HTTP tests as integration tests (they still work). |
| 4 — hydrate_items | Search results silently empty if hydrate logic has bug | Verify manually with curl after each search path is updated. Existing search tests catch regressions. |
| 5 — Generated types | Generated file drifts if developer forgets to regenerate | Add `make generate-types && git diff --exit-code src/generated/api.ts` as a CI check |
| 6 — Makefile | pyenv path differs on CI (Railway uses system Python) | Makefile is for local dev only. CI (Railway start.sh) unchanged. |

---

## Open questions

1. **Initiative 5 — generate-types:** Does the backend need to be running to generate types,
   or should we commit a static `openapi.json` snapshot? Running backend is simpler; static
   snapshot works offline. Recommend: static snapshot committed at `docs/openapi.json`,
   regenerated with `make generate-types` which starts the backend temporarily.

2. **Initiative 3 — DuplicateContentError:** Should this live in `app/services/exceptions.py`
   or inline in `app/services/content.py`? Recommend: inline until there are 3+ custom
   exceptions, then extract.

3. **Initiative 2 — Split CLAUDE.md:** The current `CLAUDE.md` has a "Debugging Notes"
   section (console errors normal, check backend logs, rate limiting resets on restart,
   CORS headers in error responses). Does this belong in `docs/instructions/backend-patterns.md`
   or a separate `docs/instructions/debugging.md`? Recommend: backend-patterns.md.
