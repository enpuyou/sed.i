---
type: instruction
status: active
last_updated: 2026-05-28
consumer: agent
---

# Workflow — TDD, subagents, context management, commit discipline

Load this when starting any non-trivial feature or when you need to decide how to
structure work. Covers: build sequence, TDD approach, subagent rules, commit discipline,
and context management.

---

## Build sequence (every non-trivial feature)

```
1. TaskCreate for the feature / phase
2. /plan <goal>          → design before touching code (see docs/plans/)
3. State acceptance criteria: "done when [user action] → [observable result]"
4. TDD loop:
   a. RED:   write one failing test for one behavior
   b. GREEN: write minimum code to pass it
   c. Repeat for each behavior
   d. Refactor only after all green
5. /pre-commit-dev       → PoC check + lint + types + targeted tests
6. Single commit         → one feature = one commit, ARCHITECTURE.md included
7. TaskUpdate → completed
8. If context long (>3 features or >5 files edited) → /handoff
```

Never accumulate uncommitted work across features. Never refactor while RED.

---

## TDD — vertical slices only

One test → one implementation → repeat. Never write all tests first then all code
(horizontal slicing produces tests that describe imagined behavior and break on refactors).

Tests verify observable behavior through public interfaces only:
- **Backend**: test HTTP responses via TestClient, not internal functions directly
- **Frontend**: test rendered output and user interactions, not implementation details

Full testing standards: `docs/instructions/testing-standards.md`.

---

## Subagents — when to spawn vs. work inline

**Spawn a subagent when:**
- Codebase exploration spans > 5 files (use `subagent_type: Explore`)
- Writing + running a test suite (`unit-test-runner` agent)
- Running a code review (`code-reviewer` agent)
- Frontend design decisions (`taste-skill` or `redesign-skill`)
- Two independent workstreams can run in parallel (e.g. backend schema + frontend component)
- `/pre-commit-dev` invoked and >1 file changed — spawn code-reviewer for PoC detection

**Work inline for:**
- Reading 1-2 known files, single-file edits, targeted grep, writing a known function

**Cost rule**: subagents start cold — full context-load cost every spawn. Only spawn when
the scoped work justifies it. Never delegate understanding — include file paths, schemas,
and context explicitly in every subagent prompt.

---

## PoC detection (automatic in /pre-commit-dev)

Before writing tests, verify the implementation is real:

```bash
# Grep changed files for PoC markers
grep -rn "TODO\|FIXME\|hardcoded\|NotImplementedError\|return \[\]$\|return {}$" <changed files>
```

Any match requires explanation or removal before proceeding.

If >1 file changed or any new public function/endpoint/service was added, spawn a
`code-reviewer` subagent with this prompt:

```
Review [list changed files]. Find only:
(a) Data returned that is hardcoded, mocked, or from a stub
(b) Unimplemented code paths (pass, ..., NotImplementedError, return [], return {})
(c) Endpoints or functions that don't do what their name says
(d) Calls to services/APIs that aren't actually wired up
Report issues only. Skip style comments. Be specific: file:line.
```

Skip when: pure refactor with no behavior change; docs-only; test-only commit.

---

## Parallel agents

When a phase has independent backend and frontend work, launch both in the same message.
Each subagent prompt must be fully self-contained — subagents share no context.

**Execution gate**: when B depends on A's API contract, include A's full output schema
explicitly in B's prompt. Never assume B can infer A's output.

```
Only parallel when: B's prompt includes A's full schema
Otherwise: run A first → extract schema → embed in B's prompt
```

---

## Feature flags

All new user-facing features ship behind an env flag (`NEXT_PUBLIC_SHOW_X=false`).
Flags live in `frontend/lib/flags.ts`. Enable to test, disable to roll back — no code
change required. Document the flag in ARCHITECTURE.md and the feature doc.

---

## Commit discipline

- **One feature = one commit.** Complete, working unit. Never bundle independent features.
- **`/pre-commit-dev` after each feature unit** — don't skip.
- **`/finalize` before every PR** — checks ARCHITECTURE.md, runs full suite, self-reviews.
- **Only the user creates PRs.** Never run `gh pr create` without explicit user request.
- **Impact reports** go in `docs/changelog/YYYY-MM-DD-<topic>.md` — only when user asks.

---

## Test discipline

- **Run tests when**: fixing a bug (confirm fixed), before `/finalize`, preparing a PR.
- **During development**: targeted file-level runs only — `pytest tests/test_foo.py`.
- **Full suite** always runs in `/finalize` — no exceptions.
- **Golden-path tests** run first: `pytest tests/test_golden_paths.py` before full suite.

---

## Context management

- **TaskCreate** at start of each phase; **TaskUpdate** as behaviors complete.
- **Handoff trigger**: >3 features committed in one session, or >5 files edited →
  invoke `/handoff` proactively. Do not wait for user to ask.
- **ARCHITECTURE.md**: reflects system state after every commit — canonical for cold-start.
- The next session reads the handoff doc and ARCHITECTURE.md; it does not read conversation history.

---

## Working style (Karpathy principles — expanded)

The condensed version is in CLAUDE.md. Full guidance here for reference.

**Think Before Coding** — Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

**Simplicity First** — Minimum code that solves the problem. Nothing speculative:
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- Ask: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

**Surgical Changes** — Touch only what you must:
- Don't improve adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove only imports/variables made unused by YOUR changes.

**Goal-Driven Execution** — Define success criteria, loop until verified:
- Transform tasks into verifiable goals before starting.
- State a brief plan with steps and verification checks.
- Don't stop until verification passes. "Make it work" is not a success criterion.
