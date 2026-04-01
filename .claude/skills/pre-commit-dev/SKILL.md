---
name: pre-commit-dev
description: "Development checkpoint: write tests, run checks, fix issues, then hand off for manual commit."
user-invokable: true
---

# /pre-commit-dev — Development Checkpoint

The "during development" skill. Write tests for what you just built, run all
checks, fix what's broken, and hand off clean code for manual commit. Use this
after implementing a feature or fix, before you're ready for full branch
finalization.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

## Current changes

!`git diff --stat`

!`git status`

**Invoke**: `/pre-commit-dev`

---

## Phase 1 · Analyze changes

1. **Identify what changed**
   - Use the pre-loaded diff and status above.
   - Classify: new components, modified behavior, bug fix, refactor.

2. **Determine test requirements**
   For each change type:
   - **New component**: rendering tests, interaction tests, prop validation.
   - **New function/hook**: unit tests for happy path + edge cases.
   - **Modified behavior**: update existing tests or add regression tests.
   - **Bug fix**: add test that would have caught the bug.
   - **Refactor only**: verify existing tests still pass.

3. **Skip testing for**:
   - Documentation-only changes.
   - Comment/formatting changes.
   - Configuration file updates (unless they affect behavior).

---

## Phase 2 · Write tests

### Test file structure
- Frontend tests go in `frontend/__tests__/` mirroring the source structure.
- Use Jest + React Testing Library (check `package.json` for exact setup).
- Match existing test patterns in the codebase.

### Test quality
- Test behavior, not implementation.
- Cover: happy path, edge cases, error states.
- Keep tests focused — one assertion per test when possible.
- Use descriptive test names: "shows error when fetch fails", not "test error".

---

## Phase 3 · Run checks

Run all checks in order. Stop and fix before proceeding if any fail.

### 3a. Type checking
```bash
cd frontend && npx tsc --noEmit
```
Zero errors required.

### 3b. Linting
```bash
cd frontend && npx eslint . --max-warnings=0 2>&1 | head -50
```
Auto-fix safe issues first: `npx eslint . --fix`

### 3c. Tests
```bash
cd frontend && npx jest --ci --passWithNoTests 2>&1 | tail -30
```

### 3d. Backend (if backend files changed)
```bash
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check app/ 2>&1 | head -50
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/ -x -q 2>&1 | tail -30
```

---

## Phase 4 · Triage failures

For each failing check:

1. **Read the failure** — what was expected vs actual?
2. **Determine root cause**:
   - **Code is wrong**: fix the implementation.
   - **Test is wrong**: test expectations don't match intended behavior — update test.
   - **Test is outdated**: behavior intentionally changed — update test.
3. **Fix and re-run** until all checks pass.

---

## Phase 5 · Commit handoff (manual)

1. **Prepare commit checklist for the user**:
   - review staged changes with `git diff --cached` and `git status`.
   - stage specific files (never `git add -A` blindly).
2. **Check for**:
   - `console.log` that should be removed.
   - Commented-out code.
   - `.env` or credentials (never commit these).
3. **Suggest commit message** that focuses on *why*, matching existing style.
4. **Do not run** `pre-commit`, `git commit`, or `git push` in this skill.
   The user executes those manually.

---

## Phase 6 · Report

Present results:
```
Tests:      [X new, Y total passing]
Types:      Clean
Lint:       Clean
Build:      Not checked (use /finalize for full build verification)
Commit:     Manual by user (message suggested)
```

---

## What this skill does NOT do

- It does not run a full build (that's `/finalize`).
- It does not review code quality or architecture (that's `/improve`).
- It does not create PRs (that's `/finalize`).
- It does not run `pre-commit`, `git commit`, or `git push`.
- It's a checkpoint, not a finish line.

---

## Cross-references

- When the branch is ready for merge, use `/finalize` for full verification + PR.
- If tests reveal design issues, consider `/improve` to assess the broader impact.
