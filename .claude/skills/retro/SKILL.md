---
name: retro
description: "Post-mortem / retrospective on a completed feature. Analyzes decisions, quality, and process improvements."
user-invokable: true
argument-hint: "[branch-name]"
---

# /retro — Retrospective & Post-Mortem

Analyze the recently completed work and extract lessons that make the next round
better. This is not a celebration or a blame session — it's an honest assessment
of process, decisions, and outcomes.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

## Branch history

!`git log main..HEAD --format="%h %ad %s" --date=short`

!`git diff main...HEAD --stat`

**Invoke**: `/retro` (analyzes current branch) or `/retro <branch-name>`

---

## Phase 1 · Reconstruct what happened

1. **Timeline**
   - Use the pre-loaded commit history above.
   - Count: how many commits, over how many days, how many files touched.
   - Identify the phases of work (initial build, iteration, fixes, cleanup).

2. **Scope evolution**
   - What was the original intent? (Read PR description, commit messages, any
     planning docs like `product-quality-execution-plan.md`)
   - What actually got built? (Diff against main)
   - Did scope grow, shrink, or shift? By how much?

3. **Decision log**
   - What key decisions were made during development?
   - Were there any reversals (built X, then replaced with Y)?
   - Were there any scope cuts (planned X, skipped it)?
   - Document each decision and its reasoning.

---

## Phase 2 · Analyze quality

### 2a. What went well
Look for evidence of:
- **Patterns that worked**: shared components that got reused cleanly, conventions
  that held across the codebase, abstractions that simplified multiple files.
- **Decisions that paid off**: architecture choices, tool choices, ordering of work.
- **Efficiency**: things that were done once and applied everywhere (vs. repeated
  manual work in each file).

### 2b. What didn't go well
Look for evidence of:
- **Rework**: commits that undo or redo previous commits. Files changed multiple
  times for the same reason.
- **Scope creep**: changes that weren't in the original plan. Were they necessary
  or avoidable?
- **Inconsistencies introduced**: new patterns that don't match existing ones.
  Files that were missed in a codebase-wide change.
- **Silent failures**: catch blocks that swallow errors, error states that don't
  render, optimistic updates without rollback.
- **Copy-paste**: code duplicated across files instead of abstracted.

### 2c. Technical debt introduced
Scan the diff for debt markers. Run these against the branch diff:

```bash
git diff main...HEAD | grep -c "TODO"              # new TODOs
git diff main...HEAD | grep -c ": any"             # new any types
git diff main...HEAD | grep -c "eslint-disable"    # new lint bypasses
git diff main...HEAD | grep -c "ts-ignore"         # new type bypasses
git diff main...HEAD | grep -c "catch.*{}"         # empty catch blocks
```

Also check for:
- Functions > 100 lines.
- Components > 500 lines.
- Unused imports or dead code.

### 2d. Technical debt resolved
- What pre-existing issues were fixed?
- What patterns were improved?
- What was cleaned up?

---

## Phase 3 · Process assessment

### 3a. Planning accuracy
- Was the plan (if one existed) accurate? What did it miss?
- Were estimates reasonable? What took longer than expected?
- Were dependencies identified upfront or discovered during work?

### 3b. Workflow efficiency
- How much time was spent on rework vs. forward progress?
- Were there unnecessary round-trips (build, find error, fix, rebuild)?
- Were tools and automation used effectively?
- Was the work ordered optimally? (e.g., foundation first, then dependent work)

### 3c. Communication
- Were decisions documented as they were made?
- Would someone reading the git history understand what happened?
- Are commit messages useful or just "fix" and "update"?

---

## Phase 4 · Extract actionable improvements

For each finding, produce a concrete, actionable recommendation. Not "be more
careful" — specific process or code changes.

### Format for each improvement:

```
**Finding**: [What happened]
**Impact**: [What it cost — time, quality, maintainability]
**Action**: [Specific change to prevent this next time]
**Where**: [Which file, skill, hook, or CLAUDE.md to update]
```

### Categories of improvements:

1. **CLAUDE.md updates** — new conventions to encode so future sessions follow them.
2. **Skill updates** — changes to /finalize, /improve, /perf-audit to catch
   things that were missed.
3. **Hook additions** — automated checks that should run on every edit or commit.
4. **Architecture changes** — structural improvements to the codebase.
5. **Process changes** — ordering of work, planning approach, review cadence.

---

## Phase 5 · Write the retro document

Create a markdown file at `docs/retros/YYYY-MM-DD-<feature-name>.md` with:

```markdown
# Retro: <Feature Name>
Date: YYYY-MM-DD
Branch: <branch-name>
Commits: <count>
Files changed: <count>

## What was built
[2-3 sentence summary]

## What went well
- [Bullet points]

## What didn't go well
- [Bullet points]

## Scope changes
- [What was added/cut vs. original plan]

## Technical debt
### Introduced
- [Items with file paths]
### Resolved
- [Items with file paths]

## Process improvements
- [Actionable items with specific targets]

## Follow-up items
- [ ] [Specific tasks for the next session]
```

---

## Phase 6 · Apply improvements

For each improvement identified in Phase 4:
- If it's a CLAUDE.md update: make the edit.
- If it's a skill update: make the edit.
- If it's a follow-up task: add it to the retro doc's follow-up section.
- If it's an architecture change: document it but don't implement (too risky
  to combine with the current branch).

---

## What this skill does NOT do

- It does not fix the issues it finds (unless they're doc/config changes).
- It does not judge the developer — it judges the process and output.
- It does not create work items in external systems (Linear, GitHub Issues).
  It documents them in the retro file for the user to triage.

---

## Cross-references

- Feed process improvements into CLAUDE.md and skill updates (Phase 6).
- If retro identifies structural debt, use `/improve` to plan the cleanup.
- If retro identifies performance issues, use `/perf-audit` to measure them.
- Before the next feature, use `/plan` — check this retro's lessons first.
