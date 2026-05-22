# sed.i — Claude Code Instructions

## What this is

A read-it-later app. Users paste URLs; the backend extracts content; the frontend provides
a reader, highlights, search, and a writing workspace. Domain vocabulary: **CONTEXT.md**.

---

## Cold start

Before doing anything else, orient without searching:

1. **Check for a handoff doc** — `ls -lt docs/handoffs/ 2>/dev/null | head -3`. If one exists, read the most recent one for session continuity before touching any code.
2. **Use the file map below** — don't grep/find to locate common files.
3. **Git state is in your system context** — use it, don't re-fetch.

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
| API routes | `content-queue-backend/app/api/<name>.py` (public-only routes: `app/api/endpoints/public.py`) |
| DB models | `content-queue-backend/app/models/<name>.py` |
| Celery tasks | `content-queue-backend/app/tasks/<name>.py` |
| Search engine | `content-queue-backend/app/core/hybrid_search.py`, `content-queue-backend/app/api/search.py` |
| DB migrations | `content-queue-backend/alembic/versions/` |
| Feature flags | `frontend/lib/flags.ts` |
| API client | `frontend/lib/api.ts` |
| Reading settings | `frontend/contexts/ReadingSettingsContext.tsx` |

---

## Run commands

```bash
# Start everything
make dev

# Individual services
make backend    # FastAPI on :8000
make worker     # Celery worker + beat
make frontend   # Next.js on :3000

# Infra (Postgres + Redis)
docker compose up -d

# Test, lint, migrate
make test
make lint
make migrate
```

> Backend commands require pyenv. The Makefile handles this — always use `make` targets
> rather than running poetry directly. See `docs/instructions/backend-patterns.md` if
> you need to run commands manually.

---

## Hard constraints

These apply in every session, every file, no exceptions:

1. **No toasts.** All errors use `InlineError` component — inline, near the action.
2. **`fetchWithAuth` only.** Never call `fetch()` directly in the frontend.
3. **Backend error shape: `{ detail: string }`.** Global handlers sanitize 422/500.
4. **`ARCHITECTURE.md` updated in the same commit as any feature change.**
11. **Feature doc written for every customer-facing change.** Create or update `docs/features/<feature-name>.md` — written as a user workflow guide, not internal notes. Covers: what it does, where to find it, how to test it. Goes in the same commit as the feature.
5. **Loading → Error → Empty → Data.** Never render two states at once.
6. **Optimistic updates** — update UI immediately, revert on failure, show InlineError.
7. **EmptyState for all empty data** — sentence case, no emoji.
8. **Error tone: "Couldn't [action]. Try again."** — no jargon, no "Failed to".
9. **`make lint` passes before committing** — ruff + tsc + eslint all clean.
10. **No backwards-compat shims** for removed code. Delete unused code.

---

## Skill workflow

Use skills in this order for significant work:

| Phase     | Skill                    | When                                               |
| --------- | ------------------------ | -------------------------------------------------- |
| Plan      | `/plan <goal>`           | Before any feature — analyze, design, phase        |
| Build     | `/pre-commit-dev`        | After a complete logical unit of work — checkpoint |
| Debug     | `/diagnose`              | Bug reported or test failing unexpectedly          |
| Finalize  | `/finalize`              | **Mandatory before every PR** — full audit + ship  |
| Retro     | `/retro`                 | After merging — extract process improvements       |
| Improve   | `/improve`               | Between features — duplication, god components     |
| Perf      | `/perf-audit`            | App feels slow — bundle, rendering, data fetching  |
| Handoff   | `/handoff`               | **Auto-triggered** when context is getting long or session is ending mid-work — do not wait for user to ask |
| Zoom      | `/zoom-out`              | Unfamiliar code — get a module map before editing  |

Plans → `docs/plans/`. Retros → `docs/retros/`. **Handoffs → `docs/handoffs/`** (read these at cold start for session continuity).

### Automatic handoff rule

If a session has completed significant work and either:
- the context is getting long (many tool calls, large diffs read, multiple files edited), **or**
- the user signals they are done or stepping away

…invoke `/handoff` proactively without waiting to be asked. The goal is to pack context before the window exhausts so the next session resumes cleanly rather than cold-starting from a summarized snapshot.

## Commit discipline

- **One feature = one commit.** Each commit is a single working unit: one feature, one bug fix, one refactor. Never bundle multiple independent features into one commit.
- **`/pre-commit-dev` after each feature unit.** Do not accumulate uncommitted work across features.
- **`/finalize` before every PR — no exceptions.** It checks ARCHITECTURE.md, runs lint/types/tests, and does a self-review. Review comments that appear after a PR was opened are a sign `/finalize` was skipped.
- **Only the user creates PRs.** Never run `gh pr create` or push to a PR-ready branch without explicit user request.
- **Impact reports** go in `docs/changelog/` as `YYYY-MM-DD-<topic>.md`. Write one only when the user asks, or when prompted after all planned work for a session is done. Never write mid-session.

## Test discipline

- **Run tests when**: fixing a bug (confirm it's fixed), before `/finalize`, when a PR is being prepared.
- **Skip full test suite during**: regular feature development unless a test broke. Use targeted file-level runs (`pytest tests/test_foo.py`) to check only what changed.
- **Full suite always runs in `/finalize`** — no exceptions.

---

## AI-assisted development practices

These apply to all feature work, not just any one feature.

### TDD — vertical slices only

One test → one implementation → repeat. Never write all tests first then all code (horizontal slicing produces tests that describe imagined behavior and break on refactors). Tests verify observable behavior through public interfaces only. See `docs/instructions/testing-standards.md` for test conventions.

### Subagents — when to spawn vs. work inline

Spawn a subagent when:
- Codebase exploration spans > 5 files (use `subagent_type: Explore`)
- Writing + running a test suite (`unit-test-runner` agent)
- Running a code review (`code-reviewer` agent)
- Frontend design decisions (`taste-skill` or `redesign-skill`)
- Two independent workstreams can run in parallel (e.g. backend schema + frontend component)

Work inline for: reading 1-2 known files, single-file edits, targeted grep, writing a known function.

**Cost rule**: subagents start cold — they pay a full context-load cost every spawn. Only spawn when the scoped work justifies it.

### Parallel agents

When a phase has independent backend and frontend work, launch both agents in the same message. Each agent's prompt must be fully self-contained — it has no access to the other agent's context. Include the API response schema explicitly in any agent whose work depends on another agent's output.

### Feature flags

All new user-facing features ship behind an env flag (`NEXT_PUBLIC_SHOW_X=false`). Enable manually to test; disable instantly to roll back. No code change required.

### Build sequence per phase

For every non-trivial feature unit, follow this loop in order:

```
1. TaskCreate for the phase / feature
2. /plan <goal>            → blueprint before touching any code
3. TDD loop (inline):
   a. RED:   write one failing test for one behavior
   b. GREEN: write minimal code to pass it
   c. Repeat for each prioritized behavior
   d. Refactor after all green — never while RED
4. /pre-commit-dev         → lint + types + tests + ARCHITECTURE.md
5. Single commit           → one feature = one commit
6. TaskUpdate → completed
7. If context is long (> 3 features committed or > 5 files edited) → /handoff
```

### Parallel agent execution gate

When parallel agents produce work where B depends on A's API contract:
- Launch A and B simultaneously only if B's prompt includes A's full output schema explicitly
- Otherwise, run A first, extract the schema, then launch B with it embedded in the prompt
- Never assume Agent B can infer Agent A's output — they share no context

### Context management

- **Task tracking**: `TaskCreate` at the start of each phase; `TaskUpdate` as each behavior completes. The next session resumes from task state.
- **Handoff trigger**: after > 3 features committed in one session, or after editing > 5 files — invoke `/handoff` proactively. The next session reads the handoff and `ARCHITECTURE.md`; it does not read conversation history.
- **ARCHITECTURE.md**: must reflect the system after every commit. It is the canonical state document for cold-start sessions.

---

## When to read more

| Working on                          | Read                                       |
| ----------------------------------- | ------------------------------------------ |
| Any frontend UI / component work    | `docs/instructions/frontend-patterns.md`   |
| Any frontend page or component      | `docs/instructions/design-language.md`     |
| Any backend API / DB / Celery work  | `docs/instructions/backend-patterns.md`    |
| Writing or running tests            | `docs/instructions/testing-standards.md`   |
| MCP tools or OAuth                  | `docs/mcp-wiki.md` and `docs/mcp-server.md`|
| Architecture decisions              | `ARCHITECTURE.md`                          |
| Domain vocabulary                   | `CONTEXT.md`                               |
| Skill usage details                 | `docs/skills-workflow-guide.md`            |

---

## Communication style

- Tutorial approach — explain what each step does, not just the code.
- Don't paste entire file contents in chat. Show snippets, outlines, key changes.
- Use `file_path:line_number` links when referencing specific code locations.
