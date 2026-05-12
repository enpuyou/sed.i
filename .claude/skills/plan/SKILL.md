---
name: plan
description: "Execution plan for a feature or initiative. Analyzes codebase, identifies dependencies, phases work, surfaces risks."
user-invokable: true
argument-hint: "<description of what you want to build>"
---

# /plan — Execution Planning

Create a detailed, phased execution plan before writing any code. This is what
separates "start coding and figure it out" from "understand the problem, design
the approach, then execute with confidence."

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

**Invoke**: `/plan <what you want to build or change>`

The argument should describe the goal, not the implementation. Good: "add inline
error feedback to every user action". Bad: "add InlineError component".

---

## Phase 0 · Prior art

Before designing anything, check what already exists:

1. **Past plans**: Search `docs/plans/` for related work. Don't re-solve solved problems.
2. **Past retros**: Search `docs/retros/` for lessons from similar features. If a retro
   called out a specific risk or process improvement relevant to this work, reference it.
3. **Existing patterns**: Grep the codebase for similar components/hooks/utilities.
4. **ARCHITECTURE.md**: Read the relevant sections to understand current structure.
5. **Design language** (frontend work): Read `docs/instructions/design-language.md`.
   The plan must explain how the new UI will follow the established aesthetic.

If prior art exists, reference it explicitly in the plan and explain how this
work builds on or differs from it.

---

## Phase 1 · Understand the problem

### 1a. Requirements gathering
- What user-facing behavior should change?
- What does "done" look like? (Acceptance criteria, not implementation details)
- What should NOT change? (Explicitly define the boundary)
- Are there reference implementations? (Other apps, design mocks, existing patterns)

### 1b. Current state analysis
- Read every file that will be affected.
- Document the current behavior for each affected surface.
- Identify the current patterns/abstractions in use.
- Note existing technical debt that will interact with this work.

### 1c. Stakeholder alignment
Ask the user to confirm before proceeding:
- Is the scope correct? Too big? Too small?
- Are there constraints not mentioned? (Timeline, backwards compatibility, mobile)
- Are there decisions the user has already made? (Technology choices, design direction)
- What's the user's preference on approach? (Conservative vs. ambitious, speed vs. quality)

---

## Phase 2 · Design the approach

### 2a. Architecture decisions
For each significant decision:
```
**Decision**: [What]
**Options considered**:
  1. [Option A]: [Pros] / [Cons]
  2. [Option B]: [Pros] / [Cons]
**Recommendation**: [Which and why]
**Reversibility**: [Easy/Hard to change later]
```

### 2b. Dependency mapping
- What must be built first? (Foundation before features)
- What can be built in parallel?
- What external dependencies exist? (APIs, libraries, design assets)
- What internal dependencies exist? (Other features, shared components)

### 2c. Risk identification
For each risk:
```
**Risk**: [What could go wrong]
**Likelihood**: Low/Medium/High
**Impact**: Low/Medium/High
**Mitigation**: [How to reduce likelihood or impact]
**Detection**: [How we'll know if it happens]
```

### 2d. Shared component / abstraction design
If the plan involves shared components:
- Define the interface (props/API) before any implementation.
- Show how 2-3 different consumers would use it.
- Identify edge cases the component must handle.
- Decide: does this get built first, or extracted after?

---

## Phase 3 · Phase the work

Break the work into phases where each phase:
- Is independently shippable (or at least independently testable).
- Has clear entry criteria ("Phase 2 starts after Phase 1 is merged").
- Has clear exit criteria ("Phase 1 is done when all error messages use InlineError").
- Can be reviewed in isolation (not a 50-file PR).

### Phase template:
```
### Phase [N] — [Name] ([Priority])

**Goal**: [One sentence]
**Entry criteria**: [What must be true before starting]

**Changes**:
1. [Specific file]: [What changes and why]
2. [Specific file]: [What changes and why]

**Exit criteria**:
- [ ] [Testable condition]
- [ ] [Testable condition]

**Risks**: [Phase-specific risks]
**Estimated scope**: [Number of files, rough line count]
```

### Phase ordering principles:
1. **Foundation first**: shared components, API changes, type definitions.
2. **Highest-risk next**: the thing most likely to invalidate the plan.
3. **Highest-value next**: the thing that delivers the most user impact.
4. **Cleanup last**: documentation, test coverage, polish.

---

## Phase 4 · Define verification strategy

For each phase, specify how to verify it works:

### 4a. Automated verification
- Which existing tests should still pass?
- What new tests are needed? (Describe behavior, not implementation)
- What type checking / linting must pass?

### 4b. Manual verification
- What should the user check visually?
- What user flows should be tested?
- What error scenarios should be triggered?

### 4c. Cross-cutting checks
- Does this change affect mobile? Check it.
- Does this change affect multiple themes? Check all of them.
- Does this change affect other features? List which ones to smoke-test.

---

## Phase 5 · Write the plan document

Create `docs/plans/<feature-name>.md` with the full plan:

```markdown
# Plan: <Feature Name>
Date: YYYY-MM-DD
Status: Draft / Approved / In Progress / Complete

## Goal
[2-3 sentences]

## Non-goals
[What this plan explicitly does NOT cover]

## Current state
[Brief description of how things work now]

## Architecture decisions
[From Phase 2a]

## Phases
[From Phase 3]

## Risks
[From Phase 2c]

## Verification
[From Phase 4]

## Open questions
[Things that need user input before proceeding]
```

---

## Phase 6 · Review with user

Present the plan and ask:
1. Does the scope match your expectations?
2. Is the phasing order correct?
3. Are there risks I missed?
4. Any open questions that need answers before starting?
5. Ready to start Phase 1?

Do NOT start implementation until the user approves. The whole point of planning
is alignment before execution.

---

## What this skill does NOT do

- It does not write code. Planning and coding are separate activities.
- It does not create tasks in external systems.
- It does not estimate time. (Time estimates are reliably wrong; scope and
  phasing are more useful.)
- It does not make decisions for the user. It presents options with tradeoffs.

---

## Cross-references

- After the plan is approved and built, use `/finalize` to ship it.
- After shipping, use `/retro` to analyze what went well and what didn't.
- Check `docs/retros/` for lessons from past similar work before planning.
