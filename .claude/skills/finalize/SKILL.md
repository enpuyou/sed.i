---
name: finalize
description: "Pre-merge branch finalization: audit, verify, fix, review, document, then hand off for manual ship."
user-invokable: true
argument-hint: "[--skip-tests]"
---

# /finalize — Branch Finalization Workflow

Run the complete pre-merge pipeline for the current feature branch. This is the
last thing you do before creating a PR. Every step must pass or be explicitly
acknowledged before proceeding. Shipping actions (`pre-commit`, `git commit`,
`git push`, PR creation) are executed manually by the user.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

## Current branch state

!`git diff main...HEAD --stat`

!`git log main..HEAD --oneline`

!`git status`

**Invoke**: `/finalize` or `/finalize --skip-tests` (if tests are known-broken and tracked)

---

## Phase 0 · Backwards compatibility (will prod survive the deploy?)

Before reviewing anything else, check whether changes are safe to ship to a live system.

### 0a. API surface changes
- List any new fields added to API response schemas. Additive = safe. Removed/renamed = breaking.
- List any removed or renamed fields. If any exist, check every call site in the frontend.
- List any changed field types. Widening (string → string | null) is usually safe. Narrowing is not.

### 0b. Deploy order risk
- If the backend gains new fields the frontend depends on, note the required deploy order (backend first).
- If the frontend can crash when a field is missing (e.g. `obj.newField.method()` without null guard), add a guard and note it.
- Check: are null guards in place for every new field accessed without optional chaining?

### 0c. Optional prop additions
- New React props should always be optional (`prop?: Type`) with a sensible default.
- Verify no existing call sites pass a now-removed prop (TypeScript will catch this, but confirm).

### 0d. Feature flags
- Is the change behind a feature flag? If yes: is the flag documented in the feature doc and in ARCHITECTURE.md?
- If not behind a flag and it's a significant user-facing change: should it be?

### 0e. Database / migration
- Any schema changes? If yes: is there a migration file? Does it run safely on existing data (no NOT NULL without default on large tables, no missing indexes, no data loss)?

**Output**: Backwards compat verdict per category:
```
| Area          | Status | Notes |
|---------------|--------|-------|
| API fields    | Safe   | Two new additive fields; null-guarded |
| Deploy order  | BE→FE  | Frontend crashes if BE is old without guard |
| Props         | Safe   | All new props are optional with defaults |
| Feature flags | N/A    | Behind SHOW_HIGHLIGHT_CONNECTIONS |
| DB/migrations | N/A    | No schema changes |
```

---

## Phase 1 · Scope (what changed?)

Understand the full surface area of this branch before verifying anything.
The diff and log above are pre-loaded — use them directly, no need to re-run.

1. **Diff inventory**
   - Use the pre-loaded diff stat above.
   - Classify changes: new files, modified files, deleted files.
   - Count lines added/removed per directory (frontend, backend, docs).

2. **Change categorization**
   Bucket every changed file into:
   - `feature` — new user-facing behavior
   - `fix` — bug fix to existing behavior
   - `refactor` — structural change, no behavior change
   - `config` — build, lint, CI, dependency changes
   - `docs` — documentation only
   - `test` — test additions/changes only

3. **Blast radius check**
   - List all components/modules that import from changed files.
   - Flag any changes to shared utilities (`lib/api.ts`, contexts, types).
   - Flag any changes to configuration (`next.config`, `pyproject.toml`, middleware).

**Output**: A structured summary table. Example:
```
| Category | Files | Key changes |
|----------|-------|-------------|
| feature  | 3     | InlineError component, EmptyState component |
| refactor | 12    | Replace ad-hoc errors with InlineError |
| docs     | 1     | ARCHITECTURE.md §16 rewrite |
```

---

## Phase 2 · Verify (does it work?)

Run every automated check. Report results as pass/fail with details on failures.

### 2a. Type checking
```bash
cd frontend && npx tsc --noEmit
```
- Zero errors required. Warnings are acceptable if pre-existing.
- If new warnings introduced, flag them.

### 2b. Linting
**Frontend:**
```bash
cd frontend && npx eslint . --max-warnings=0 2>&1 | head -50
```
**Backend (if backend files changed):**
```bash
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check app/ 2>&1 | head -50
```
- Auto-fix safe issues: `npx eslint . --fix` / `PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check --fix app/`
- Report any issues that need manual attention.

### 2c. Tests
**Frontend:**
```bash
cd frontend && npx jest --ci --passWithNoTests 2>&1 | tail -30
```
**Backend (if backend files changed):**
```bash
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/ -x -q 2>&1 | tail -30
```
- All tests must pass. If a test fails:
  - Read the failure output.
  - Determine: is this a real bug, a stale test, or a test that needs updating?
  - Fix it or flag it for the user with a clear explanation.

### 2d. Build
```bash
cd frontend && npx next build 2>&1 | tail -20
```
- Must succeed. Build warnings are acceptable if pre-existing.
- Flag any new build warnings introduced by this branch.

### 2e. Unused code scan
- Grep for unused imports in changed files only.
- Check for variables/functions declared but never referenced.
- Check for `console.log` or `console.error` that should be removed (keep only
  intentional error logging in catch blocks).

**Output**: Pass/fail table for each check. Example:
```
| Check      | Status | Notes |
|------------|--------|-------|
| Types      | ✓      |       |
| Lint (FE)  | ✓      | 2 auto-fixed |
| Lint (BE)  | ✓      |       |
| Tests (FE) | ✓      | 47 passed |
| Tests (BE) | —      | No backend changes |
| Build      | ✓      |       |
| Dead code  | ✓      |       |
```

---

## Phase 3 · Review (is it good?)

Code review your own diff. Read every changed file. This is not a skim — read
the actual code and evaluate it against these criteria.

### 3a. Correctness
- Are there any logic errors? Off-by-one, wrong condition, missing null check?
- Do error handlers actually handle errors (no empty catch blocks)?
- Are optimistic updates properly reverted on failure?
- Do loading/error/empty/data states render exclusively (never two at once)?

### 3b. Consistency
- Do all error messages follow the established tone? ("Couldn't [action]. Try again.")
- Do all empty states use the shared `EmptyState` component?
- Do all inline errors use the shared `InlineError` component?
- Are CSS patterns consistent? (`rounded-none`, `var(--color-*)`, `font-serif` for headings, `font-mono` for UI labels)
- No raw hex/Tailwind colors (`text-gray-500`, `bg-white`, etc.) — all via `var(--color-*)`.
- No `shadow-*` anywhere.
- Buttons match the `compact-touch font-mono text-xs rounded-none border` pattern.
- See `docs/instructions/design-language.md` for the full design checklist.

### 3c. Security
- No secrets, tokens, or credentials in the diff.
- No `dangerouslySetInnerHTML` without sanitization.
- No user input rendered without escaping.
- No new `any` types that bypass type safety.
- API inputs validated; error responses don't leak internal details.

### 3d. Performance
- No unnecessary re-renders (state updates in loops, missing deps in useEffect).
- No data fetching in render path (fetches should be in useEffect/useCallback).
- No large inline objects/arrays that would break memoization.
- Images use Next.js `<Image>` or have explicit width/height.

### 3e. Accessibility
- Interactive elements have accessible names (aria-label, button text, link text).
- Color is not the only indicator of state (icons, text, or borders accompany it).
- Focus management on modals (trap focus, return focus on close).
- Keyboard navigation works (Escape to close, Enter to submit).

**Output**: List of findings grouped by severity:
- **Must fix** — blocks merge
- **Should fix** — doesn't block but should be addressed in this PR
- **Note** — informational, can be a follow-up

---

## Phase 4 · Document (will the next person understand?)

### 4a. ARCHITECTURE.md sync
- Read ARCHITECTURE.md.
- Check every section that relates to changed code.
- Update sections that are now stale or incomplete.
- Add new sections if a new subsystem was introduced.
- Do NOT add sections for minor changes (bug fixes, copy changes).

### 4b. CLAUDE.md sync
- Check if any new project conventions were established.
- If a new pattern was introduced (new shared component, new API convention),
  add it to CLAUDE.md so future sessions follow it.

### 4c. Inline documentation
- Complex logic should have a brief comment explaining *why*, not *what*.
- Public component interfaces (props) should be self-documenting via types.
- Do NOT add JSDoc to every function — only where the name isn't enough.

### 4d. Feature doc check
- For every user-facing change in this branch, is there a corresponding `docs/features/<name>.md`?
- Read the existing feature doc (if any) — update any section that is now stale:
  - Prerequisites, test steps, keyboard shortcuts, tips
  - Flag behaviors that have changed (e.g. "shared tags required" → "no longer required")
- If no feature doc exists and the change is user-visible, create one now.
- Feature docs are written for users, not engineers — no internal names, no code references.

### 4e. Impact report
- Does `docs/changelog/` have an entry for the work done in this branch?
- If not, create `docs/changelog/YYYY-MM-DD-<topic>.md` with:
  - What shipped (user-visible behavior, in plain language)
  - API changes (additive, breaking, or none)
  - Component/file change table
  - Deploy order note (if relevant)
- Keep it factual — this is a record, not marketing copy.

**Output**: List of doc changes made or "docs are current, no changes needed."

---

## Phase 5 · Ship handoff (manual)

### 5a. Stage and commit
- `git status` — review what's staged and unstaged.
- Stage specific files (never `git add -A` blindly).
- Do NOT commit `.env`, credentials, or large binary files.
- Commit message: focus on *why*, match existing commit style.
- Do not execute `pre-commit` or `git commit` in this skill; provide the exact
  commands for the user to run manually.

### 5b. Create PR
- Push branch to remote (manual by user).
- Create PR with structured description (manual by user):
  ```
  ## Summary
  - [1-3 bullet points describing what and why]

  ## Changes
  - [File-level change list grouped by category]

  ## Verification
  - [What was tested and how]
  - [Type check, lint, test, build results]

  ## Screenshots
  - [If UI changed, include before/after or description of visual changes]

  🤖 Generated with Claude Code
  ```
- Assign reviewers if known.
- Do not execute `git push` or PR creation in this skill.

### 5c. Post-ship checklist
After PR is created, confirm:
- [ ] All checks passed (types, lint, tests, build)
- [ ] ARCHITECTURE.md updated (or confirmed current)
- [ ] No console.log debugging left in
- [ ] No TODO comments introduced without tracking
- [ ] PR description accurately describes all changes
- [ ] Commit history is clean (no "fix typo" chains)

---

## Failure modes

- If **types fail**: fix before proceeding. Never skip type checking.
- If **tests fail**: diagnose and fix. If the test is stale, update it and explain why.
- If **build fails**: fix before proceeding. Never ship a broken build.
- If **review finds must-fix issues**: fix them, then re-run Phase 2 verification.
- If **multiple phases need re-running**: that's fine — iterate until clean.

---

## What this skill does NOT do

- It does not write new tests for untested code (use `/pre-commit-dev` for that).
- It does not refactor code (use `/improve` for that).
- It does not optimize performance (use `/perf-audit` for that).
- It does not run `pre-commit`, `git commit`, `git push`, or create PRs automatically.
- It focuses on shipping *what's already done* cleanly.

---

## Cross-references

- After merging, run `/retro` to extract lessons and process improvements.
- If review finds structural issues (P0/P1), consider `/improve <affected-area>` after merge.
- If review finds performance issues, consider `/perf-audit` after merge.
