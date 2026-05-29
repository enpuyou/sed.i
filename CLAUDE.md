# sed.i — Claude Code Instructions

## Working principles (Karpathy)

1. **Think first** — state assumptions explicitly; ask rather than guess; push back when a simpler approach exists; name confusion before proceeding into it
2. **Simplicity** — minimum code that solves the problem; no unrequested features, abstractions, error handling for impossible cases, or configurability
3. **Surgical** — touch only what the task requires; match existing style; mention pre-existing dead code but don't delete it unless asked
4. **Verify** — define "done" as a testable, observable outcome before writing any code; loop until it passes

---

## What this is

A read-it-later app. Users paste URLs; backend extracts content; frontend provides
reader, highlights, search, and writing workspace. See **CONTEXT.md** for vocabulary,
**ARCHITECTURE.md** for system state, **docs/decisions/** before any architectural decision.

## Cold start

1. `ls -lt docs/handoffs/ 2>/dev/null | head -3` — read the most recent handoff if one exists
2. Use the key file paths table below — do not grep to orient
3. Check `docs/decisions/` before any architectural decision

## Key file paths

| Working on | Files |
|------------|-------|
| Reader / article view | `frontend/components/Reader.tsx`, `frontend/components/ReaderArticle.tsx` |
| Dashboard / queue | `frontend/app/dashboard/page.tsx`, `frontend/components/ContentList.tsx`, `frontend/components/ContentItem.tsx` |
| Extension | `extension/content/content.js`, `extension/popup/popup.js`, `extension/background/service_worker.js` |
| Extension reader overlay | `extension/content/reader-overlay.js` |
| Safari extension | `safari-extension/sed.i/sed.i Extension/Resources/` (mirror of `extension/`) |
| Connections panel | `frontend/components/ConnectionsPanel.tsx` |
| Shared UI components | `frontend/components/InlineError.tsx`, `frontend/components/EmptyState.tsx`, `frontend/components/Navbar.tsx` |
| Auth & current user | `content-queue-backend/app/core/deps.py`, `content-queue-backend/app/api/auth.py` |
| API routes | `content-queue-backend/app/api/<name>.py` |
| DB models | `content-queue-backend/app/models/<name>.py` |
| Celery tasks | `content-queue-backend/app/tasks/<name>.py` |
| Search engine | `content-queue-backend/app/core/hybrid_search.py`, `content-queue-backend/app/api/search.py` |
| DB migrations | `content-queue-backend/alembic/versions/` |
| Feature flags | `frontend/lib/flags.ts` |
| API client | `frontend/lib/api.ts` |

## Run commands

```bash
make dev          # all services
make backend      # FastAPI :8000
make worker       # Celery worker + beat
make frontend     # Next.js :3000
make test         # full suite (backend + frontend)
make lint         # ruff + tsc + eslint
make install-hooks  # install pre-push git hook
```

---

## IMPORTANT — YOU MUST follow these every session

These are project-specific conventions Claude cannot infer from reading the code.

1. **ARCHITECTURE.md** updated in the same commit as any feature change — CI enforces this
2. **Feature doc** (`docs/design/product/<name>.md`) for every customer-facing change, same commit
3. **`make lint` passes** before committing — ruff + tsc + eslint — hook enforces this
4. **`/finalize` before every PR** — no exceptions; runs full audit + doc check
5. **One feature = one commit** — conventional commit format required (see below); use `/pre-commit-dev` as checkpoint
6. **Acceptance criteria first** — before any code: "done when [user action] → [observable result]"
7. **`/pre-commit-dev` after each feature unit** — includes PoC detection + targeted tests
8. **`InlineError` for all errors, never toasts** — `fetchWithAuth` only, never bare `fetch()`
9. **Backend errors: `{ detail: string }`** — frontend reads `err.detail`; `err.body` for structured payloads

---

## Skill workflow

| Phase    | Skill             | When                                                    |
|----------|-------------------|---------------------------------------------------------|
| Plan     | `/plan <goal>`    | Before any feature — design before touching code        |
| Build    | `/pre-commit-dev` | After each logical unit — checkpoint before commit      |
| Debug    | `/diagnose`       | Bug reported or test failing unexpectedly               |
| Finalize | `/finalize`       | **Mandatory before every PR** — full audit + ship prep  |
| Retro    | `/retro`          | After merging — extract lessons                         |
| Improve  | `/improve`        | Between features — duplication, complexity              |
| Perf     | `/perf-audit`     | App feels slow — bundle, rendering, queries             |
| Handoff  | `/handoff`        | Auto-triggered when context is long or session ending   |
| Zoom     | `/zoom-out`       | Unfamiliar area — get a module map before editing       |

Plans → `docs/plans/`. Retros → `docs/retros/`. Handoffs → `docs/handoffs/`.

## Commit format — conventional commits + semver

Every commit: `<type>(<scope>): <description>`

| Type | Semver | When |
|------|--------|------|
| `feat` | MINOR | New user-facing feature, new endpoint |
| `fix` | PATCH | Bug fix, including `fix(security):` |
| `perf` | PATCH | Performance improvement |
| `feat!` or `BREAKING CHANGE:` footer | MAJOR | Removed/renamed endpoint, auth change |
| `refactor`, `ci`, `docs`, `chore`, `test` | none | Internal only |

Scope is optional but recommended: `(search)`, `(auth)`, `(mcp)`, `(worker)`, `(ext)`, `(ui)`.
`/finalize` determines the version bump and updates `VERSION`, `pyproject.toml`, `package.json`.

## Trigger-based actions — do these without being asked

- **Context long** (>3 features committed or >5 files edited) → invoke `/handoff` proactively
- **User asks to open a PR** → run `/finalize` first, then let user create the PR
- **Non-trivial feature starts** → state acceptance criteria before writing code
- **`/pre-commit-dev` invoked** → runs PoC grep + code-reviewer subagent if >1 file changed

## When to read more

| Working on | Read |
|------------|------|
| Frontend UI / components | `docs/instructions/frontend-patterns.md` |
| Frontend page or design | `docs/instructions/design-language.md` |
| Backend API / DB / Celery | `docs/instructions/backend-patterns.md` |
| Testing conventions | `docs/instructions/testing-standards.md` |
| Workflow, TDD, subagents | `docs/instructions/workflow.md` |
| MCP tools or OAuth | `docs/design/systems/mcp-wiki.md` + `docs/design/systems/mcp-server.md` |
| How a subsystem works | `docs/design/systems/` |
| How a feature works (user) | `docs/design/product/` |
| Domain vocabulary | `CONTEXT.md` |
| Architecture decisions | `ARCHITECTURE.md` + `docs/decisions/` |
