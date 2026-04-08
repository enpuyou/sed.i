# Skills Workflow Guide

How to use the Claude Code skills system to build, ship, and improve features
with the rigor of a staff engineer — without doing everything manually.

## The Big Picture

Seven skills form a complete development lifecycle. Each one handles a specific
phase of work so you don't skip steps or forget checks.

```
    /plan-product-strategy  -->  /plan  -->  build  -->  /pre-commit-dev  -->  /finalize  -->  /retro
    |                                                             |
    +-------- /improve, /perf-audit (between features) <----------+
```

## The Skills

### 1. `/plan <what you want to build>`

**When**: Before writing any code for a new feature or significant change.

**What it does**:
- Checks past plans and retros for related work (so you don't re-solve solved problems)
- Analyzes the current codebase to understand what exists
- Designs the approach with explicit architecture decisions and tradeoffs
- Breaks the work into shippable phases with entry/exit criteria
- Identifies risks and mitigation strategies
- Produces a plan document in `docs/plans/` for review

**What you do**: Describe the goal ("add inline error feedback to every user action"),
not the implementation ("add InlineError component"). Review the plan, answer open
questions, approve before coding starts.

**Output**: `docs/plans/<feature-name>.md`

---

### 1b. `/plan-product-strategy <topic>`

**When**: Before implementation planning when you need a deeper strategy artifact
that includes market/context comparison, gap analysis, and phased product direction.

**What it does**:
- Audits current sed.i behavior from code/docs
- Analyzes external patterns and what they imply for sed.i
- Builds a capability/gap model (including uncertainty where metrics are missing)
- Produces a realistic phased roadmap with metrics, risks, and decision gates
- Writes a reusable strategy plan artifact to `docs/plans/`

**What you do**: Use this when the question is product-directional (e.g. ingestion
quality, mixed-media rendering, knowledge artifact strategy), then use `/plan` to
convert approved phases into implementation plans.

**Output**: `docs/plans/<topic>-plan.md`

---

### 2. Build (you write the code)

This is the part where you actually implement the feature. The plan tells you
what to build and in what order. You write code, Claude helps.

No special skill needed here — just normal development.

---

### 3. `/pre-commit-dev`

**When**: After implementing a chunk of work, before you're done with the branch.

**What it does**:
- Analyzes what you changed (auto-loads git diff)
- Writes tests for new behavior
- Runs type checking, linting, and tests
- Fixes issues it finds
- Creates a clean commit

**What you do**: Say `/pre-commit-dev` when you want to checkpoint your work.
It handles the boring parts — tests, lint, types — so you can focus on the
next piece of implementation.

**Think of it as**: A development checkpoint. Use it multiple times per branch,
after each meaningful piece of work.

---

### 4. `/finalize`

**When**: Your branch is done and ready for merge.

**What it does**:
- Inventories every change on the branch (auto-loads full diff and commit log)
- Runs ALL checks: types, lint, tests, build
- Reviews its own code for: correctness, consistency, security, performance, accessibility
- Updates ARCHITECTURE.md and CLAUDE.md if conventions changed
- Creates the commit and PR with structured description

**What you do**: Say `/finalize` and let it run. Review the findings — it will
flag "must fix" vs "should fix" vs "note" issues. Approve the PR when you're
satisfied.

**Think of it as**: Your automated code reviewer + PR bot. It does everything
a thorough reviewer would check.

---

### 5. `/retro`

**When**: After merging a feature branch.

**What it does**:
- Reconstructs what happened (timeline, scope evolution, decision log)
- Analyzes quality: what went well, what didn't, rework, inconsistencies
- Scans for technical debt introduced (TODOs, `any` types, lint bypasses)
- Assesses the development process itself
- Extracts actionable improvements (not vague "be more careful")
- Updates CLAUDE.md and skills with lessons learned

**What you do**: Say `/retro` after merging. Read the retro document. The
improvements it finds feed back into future `/plan` runs — creating a
learning loop.

**Output**: `docs/retros/YYYY-MM-DD-<feature-name>.md`

**Think of it as**: A team retrospective, but focused on the code and process
rather than feelings.

---

### 6. `/improve [path]`

**When**: Between features. The codebase feels messy or you want to pay down debt.

**What it does**:
- Audits component sizes (flags god components >800 lines)
- Finds duplicated logic across 3+ files
- Identifies missing abstractions (similar-but-not-identical patterns)
- Maps dependencies (high fan-in, high fan-out, circular deps)
- Detects dead code (unused exports, unreachable branches)
- Checks pattern consistency (state management, error handling, naming)
- Produces a prioritized improvement plan (P0-P3)

**What you do**: Say `/improve` for the full codebase or `/improve frontend/components`
to scope it. Review the plan. Pick items to implement based on priority and
available time.

**Output**: `docs/improvement-plan-YYYY-MM-DD.md`

---

### 7. `/perf-audit [area]`

**When**: The app feels slow, or periodically as a health check.

**What it does**:
- Captures baseline build/bundle metrics before analyzing
- Analyzes dependency weight and tree-shaking
- Finds code splitting opportunities
- Checks for re-render issues (missing memoization, inline objects)
- Audits data fetching (waterfalls, missing caching, duplicate fetches)
- Checks images, fonts, CSS optimization
- Produces prioritized findings with estimated impact
- Identifies quick wins (<10 lines, zero risk)

**What you do**: Say `/perf-audit` for everything or `/perf-audit bundle` for
a specific area. Apply quick wins immediately. Plan larger optimizations with
`/plan`.

---

## How They Connect

The skills reference each other. Here's when one leads to another:

| After running... | Consider running... | When |
|------------------|---------------------|------|
| `/plan-product-strategy` | `/plan` | Strategy direction is approved and ready for implementation breakdown |
| `/plan` | Build + `/pre-commit-dev` | Plan is approved |
| `/pre-commit-dev` | `/finalize` | Branch is complete |
| `/finalize` | `/retro` | PR is merged |
| `/retro` | `/improve` or `/plan` | Retro identified debt or next feature |
| `/improve` | `/plan` | P0/P1 improvements need implementation |
| `/perf-audit` | `/plan` or direct fix | Large optimizations vs quick wins |

## Typical Feature Flow

Here's what a complete feature lifecycle looks like:

```
1.  /plan-product-strategy notifications experience and reliability
    --> Review strategic direction, constraints, and roadmap

2.  /plan add user notifications
    --> Review plan, approve scope and phases

3.  Build Phase 1 (foundation: notification model, API)
    /pre-commit-dev
    --> Tests written, checks pass, committed

4.  Build Phase 2 (UI: notification list, badge)
    /pre-commit-dev
    --> Tests written, checks pass, committed

5.  Build Phase 3 (real-time: WebSocket integration)
    /pre-commit-dev
    --> Tests written, checks pass, committed

6.  /finalize
    --> Full audit, all checks pass, ARCHITECTURE.md updated, PR created

7.  Merge PR

8.  /retro
    --> Lessons extracted, CLAUDE.md updated with new conventions
```

## What Makes This Different from "Just Coding"

1. **Plans before code.** You don't start coding and figure it out. You understand
   the problem, design the approach, then execute with confidence.

2. **Checkpoints during development.** `/pre-commit-dev` catches issues early —
   before they compound into a mess at the end.

3. **Automated rigor at ship time.** `/finalize` does everything a thorough
   reviewer would: types, lint, tests, build, security scan, accessibility check,
   doc updates. Every time. No skipping.

4. **Learning loops.** `/retro` extracts lessons that feed back into the skills
   and conventions. Each feature makes the next one better.

5. **Proactive maintenance.** `/improve` and `/perf-audit` prevent debt from
   accumulating silently.

## Shared Conventions

All skills load the same conventions file (`.claude/skills/_shared/conventions.md`)
so they enforce consistent patterns:

- Error feedback: `InlineError` component, "Couldn't [action]. Try again." tone
- Empty states: `EmptyState` component, sentence case, no emoji
- State rendering: Loading > Error > Empty > Data (exclusive)
- API: `fetchWithAuth` everywhere, `{detail: string}` error shape
- Documentation: ARCHITECTURE.md stays current with every feature

These conventions are the project's institutional knowledge — they encode
decisions that were already made so they don't get relitigated.

## Documents Produced

| Skill | Output location | Purpose |
|-------|----------------|---------|
| `/plan` | `docs/plans/<feature>.md` | Design decisions, phases, risks |
| `/retro` | `docs/retros/YYYY-MM-DD-<feature>.md` | Lessons learned, improvements |
| `/improve` | `docs/improvement-plan-YYYY-MM-DD.md` | Prioritized debt items |
| `/finalize` | GitHub PR | Structured change description |

These documents form the project's engineering history. Future `/plan` runs
check them so lessons aren't lost between sessions.
