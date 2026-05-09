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

| Phase    | Skill             | When                                               |
| -------- | ----------------- | -------------------------------------------------- |
| Plan     | `/plan <goal>`    | Before any feature — analyze, design, phase        |
| Build    | `/pre-commit-dev` | During dev — write tests, run checks, commit       |
| Finalize | `/finalize`       | Branch ready to merge — full audit + ship          |
| Retro    | `/retro`          | After merging — extract process improvements       |
| Improve  | `/improve`        | Between features — find duplication, god components|
| Perf     | `/perf-audit`     | App feels slow — bundle, rendering, data fetching  |

Plans → `docs/plans/`. Retros → `docs/retros/`.

**After every implementation, write an impact report** in `docs/plans/` named
`<feature>-impact-report.md`. Executive level only: what measurably changed
(query count, test count, config coverage, etc.) and why it matters. No
implementation details, no invented benchmarks — only claims you can back with
a number from the actual run.

---

## When to read more

| Working on                          | Read                                       |
| ----------------------------------- | ------------------------------------------ |
| Any frontend UI / component work    | `docs/instructions/frontend-patterns.md`   |
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
