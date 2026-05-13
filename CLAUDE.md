# sed.i — Claude Code Instructions

## What this is

A read-it-later app. Users paste URLs; the backend extracts content; the frontend provides
a reader, highlights, search, and a writing workspace. Domain vocabulary: **CONTEXT.md**.

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

Plans → `docs/plans/`. Retros → `docs/retros/`.

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
