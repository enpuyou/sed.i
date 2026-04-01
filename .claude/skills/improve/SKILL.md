---
name: improve
description: "Codebase improvement audit: duplicated logic, god components, missing abstractions, dead code."
user-invokable: true
argument-hint: "<path to scope, e.g. frontend/components>"
---

# /improve — Codebase Improvement Audit

Systematic analysis of maintainability, reusability, and code quality. Finds
concrete problems and proposes concrete fixes — no vague "consider refactoring"
suggestions.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

**Invoke**: `/improve` (full codebase) or `/improve <path>` (scoped to directory)

---

## Phase 1 · Structural analysis

### 1a. Component size audit (two-tier)

**Tier 1 — Quick scan**: Get line counts for all component files:
```bash
find frontend -name "*.tsx" -exec wc -l {} + | sort -rn | head -20
```

**Tier 2 — Deep analysis**: Only for files that cross thresholds:
- **Critical** (>800 lines): must be split. These are god components.
- **Warning** (>400 lines): review whether responsibilities can be extracted.
- **OK** (<400 lines): no action needed — skip detailed analysis.

For each critical/warning file, identify:
- How many distinct responsibilities it handles (data fetching, rendering,
  event handling, state management).
- Which responsibilities could be extracted into custom hooks or sub-components.
- Dependencies between responsibilities (what must stay together).

### 1b. Duplication analysis
Search for code patterns that appear in 3+ files:
- Identical or near-identical fetch-then-setState patterns.
- Identical error handling patterns (try/catch with same structure).
- Identical UI patterns (modal shells, confirmation dialogs, action buttons).
- Identical state management patterns (loading/error/data triplets).

For each duplication, propose:
- A shared abstraction (hook, component, or utility).
- Which files would use it.
- Estimated reduction in total lines.

### 1c. Abstraction opportunities
Look for patterns that are similar but not identical:
- Components that render lists with different item types but same structure.
- API calls that follow the same pattern but hit different endpoints.
- State machines that have the same states but different transitions.

For each, assess whether a generalized version would be simpler or more complex
than the current specialized versions. Only propose abstractions that reduce
total complexity — three similar 20-line blocks are better than one 40-line
generic abstraction.

---

## Phase 2 · Dependency analysis

### 2a. Import graph
For the target directory:
- Which files are imported by the most other files? (high fan-in = shared utility)
- Which files import the most other files? (high fan-out = likely doing too much)
- Are there circular dependencies?

### 2b. Coupling assessment
- Which components are tightly coupled (always changed together)?
- Which components are loosely coupled (can be changed independently)?
- Are there hidden dependencies (component A works only if component B is
  rendered nearby, but this isn't expressed in types)?

### 2c. Dead code detection
- Exported functions/components that are never imported.
- Props defined in interfaces but never passed.
- State variables declared but never read.
- CSS classes defined but never applied.
- Feature-flagged code where the flag is always on/off.

---

## Phase 3 · Pattern consistency

### 3a. State management patterns
Catalog how state is managed across the codebase:
- Which components use `useState` vs `useReducer`?
- Which use context vs prop drilling?
- Which fetch data themselves vs receive it as props?
- Is there a consistent pattern, or does it vary?

### 3b. Error handling patterns
- Do all components use `InlineError`?
- Do all API calls have error handling?
- Do all optimistic updates have rollback?
- Are error messages consistent in tone?

### 3c. Loading state patterns
- Do all async operations show loading indicators?
- Are loading/error/empty/data states mutually exclusive?
- Is there a consistent loading component or does each file roll its own?

### 3d. Naming conventions
- File naming: PascalCase for components, camelCase for utilities?
- Function naming: handle* for event handlers, fetch* for API calls?
- State naming: is*/has* for booleans, *Loading/*Error for async state?
- Are these consistent or mixed?

---

## Phase 4 · Prioritized improvement plan

Produce a ranked list of improvements. Each item must include:

```
### [Priority] [Title]

**Problem**: What's wrong and where (with file paths and line numbers).
**Impact**: What it costs (maintenance burden, bug risk, performance, DX).
**Effort**: S/M/L — how much work to fix.
**Proposal**: Specific change (not "refactor this" but "extract X hook from Y,
used by Z and W").
**Dependencies**: What must happen first, if anything.
```

### Priority levels:
- **P0 — Fix now**: Active bugs, security issues, things that will get worse fast.
- **P1 — Fix soon**: Maintenance burden, frequent source of confusion, blocks
  future work.
- **P2 — Fix eventually**: Nice to have, would improve DX, not urgent.
- **P3 — Track**: Not worth fixing now but worth knowing about.

### Ordering within priority:
Sort by effort (smallest first) within each priority level. Quick wins first.

---

## Phase 5 · Output

Write the improvement plan to `docs/improvement-plan-YYYY-MM-DD.md`.

The plan should be:
- Actionable by someone who didn't write it.
- Scoped to specific files and line ranges.
- Honest about effort (don't downplay complexity).
- Explicit about what NOT to do (tempting refactors that aren't worth it).

---

## What this skill does NOT do

- It does not implement the improvements (that's feature work).
- It does not audit performance (use `/perf-audit` for that).
- It does not review a specific PR (use `/finalize` for that).
- It does not write tests (use `/pre-commit-dev` for that).

---

## Cross-references

- If improvements are urgent (P0/P1), plan the work with `/plan <improvement>`.
- After implementing improvements, ship with `/finalize`.
- For performance-specific issues, use `/perf-audit` instead.
- Check `docs/retros/` — past retros may have already identified these issues.
