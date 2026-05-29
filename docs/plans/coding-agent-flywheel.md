---
type: plan
status: in_progress
last_updated: 2026-05-28
consumer: both
---

# Plan: Coding Agent Flywheel

Date: 2026-05-28
Status: Draft — awaiting user approval before implementation

## Goal

Transform the coding agent experience from instruction-compliance (the agent hopes to
remember the rules) to enforcement-by-construction (the repo makes mistakes hard and
good practices automatic). The result: agents produce verified, working code; docs are
trustworthy; the repo gets easier to work in as it grows.

## Non-goals

- Rewriting working product features
- Changing user-facing behavior
- Migrating to a new tech stack

## Supersedes

`docs/plans/agent-harness-improvements.md` (Draft, 2026-05-07) — this plan absorbs all
six initiatives from that plan and extends them with the verification and governance
layers that were missing. Do not execute agent-harness-improvements.md separately.

---

## SOTA research: what elite AI-assisted repos do differently

### Information retrieval hierarchy

Agents retrieve information through four channels. Each is best for a different question:

| Channel | Best for | Reliability |
|---------|----------|-------------|
| Code itself (grep, read file) | "where is X implemented?" "what does Y return?" | Highest — always current |
| CLAUDE.md (always loaded) | "what rules apply right now?" | High — only if short enough to read fully |
| docs/instructions/* (on-demand) | "how do we do X in this repo?" | Medium — can go stale |
| Memory (MEMORY.md + files) | "what did we learn across sessions?" | Medium — snapshot |

**Key insight**: code is the most reliable source of truth. Docs supplement code only
for things that can't be expressed in code: rationale, UX, workflow, design decisions.
Module docstrings, type signatures, and consistent naming mean an agent extracts truth
from code directly. Docs that duplicate or describe code diverge silently.

**Navigation principle**: CLAUDE.md is the menu, not the meal. It points to the right
channel. An agent should reach the right information in ≤3 steps:
1. CLAUDE.md → "for error handling, read frontend-patterns.md"
2. frontend-patterns.md → the specific pattern
3. Or: grep for existing usage of the pattern in the codebase

### Constitutional enforcement over instructional compliance

Rules in a markdown file are forgotten. Rules baked into git hooks, CI checks, and
linters cannot be forgotten. Every CLAUDE.md rule should ask: can this be mechanically
enforced? If yes: enforce it, remove it from CLAUDE.md. If no: keep it, but make the
trigger condition explicit.

### Specification-first development

The agent's first output for any feature is acceptance criteria: "this is done when
[observable user action] → [observable result]". The failing test is written before
implementation. This forces verifiability before code that looks correct but isn't.

### Fresh-agent verification (bias-breaking)

An agent that implements a feature is biased toward believing it works. A fresh
subagent — given only the spec and the code, not the implementation history — sees it
without that bias. Used not for style but to answer: "Is any part of this mocked,
hardcoded, or unimplemented?"

### Eval-driven CI gates

Evals that only report are advisory. Evals that block on regression create accountability.
Eval datasets accumulate: each session can add a case, raising the floor over time.

### Living ADRs

Decisions recorded with context and rationale let the next agent skip re-deriving the
same analysis. docs/decisions/ exists but is not referenced in CLAUDE.md or plans.
It should be the first place to check before any architectural decision.

---

## The three systemic problems

### Problem 1: Instruction-based workflow (no enforcement)

Rules exist in CLAUDE.md (269 lines — too long to read fully every session) but nothing
enforces them. Retro found ARCHITECTURE.md skipped 3 times; /finalize not run before PRs.
Same findings appear two sessions later. The cost of skipping a rule is zero until the
user notices.

### Problem 2: Proof-of-concept code ships as production code

Agent writes code that compiles and passes lint, but returns hardcoded data, calls a
mock, or has a critical path never exercised. Unit tests pass; the feature doesn't work.
No mandatory step asks: "Is any part of this faked?"

### Problem 3: Documentation without governance or retrieval strategy

21 plans, 14 with status tracking. Docs reference deleted files. No consistent format,
no lifecycle, no freshness signals. An agent loads a doc, trusts it, acts on stale
information — or can't find the right doc at all.

---

## The flywheel model

```
Feature request
    ↓
Acceptance criteria — observable, testable, stated before any code
    ↓
Failing test → implementation → test passes (TDD vertical slice)
    ↓
[AUTOMATIC] Spawn code-reviewer subagent: "Is any part mocked or hardcoded?"
    ↓
[AUTOMATIC via hook] Pre-push: lint + types pass locally
    ↓
Single commit — ARCHITECTURE.md included
    ↓
[AUTOMATIC via CI] Golden-path tests + eval regression check
    ↓
Memory + handoff carry forward what was learned
    ↓
Next session: richer context, lower re-orientation cost
    ↓
Evals accumulate → baseline rises → "working" means more over time
```

---

## Automation classification

Every rule in this plan is classified as one of three types:

**(A) Truly automatic** — git hook or CI enforces it regardless of agent
**(B) Trigger-based agent behavior** — agent does it when a specific condition fires,
no user prompt needed, trigger is explicit in CLAUDE.md
**(C) Instruction-only** — agent tries but may forget; minimize these

The goal: push everything from (C) toward (A) or (B).

| Rule | Type | Mechanism |
|------|------|-----------|
| Lint before commit | A | pre-push hook |
| Type check before commit | A | pre-push hook |
| Golden-path tests on every PR | A | CI required check |
| Eval regression gate | A | CI required check |
| ARCHITECTURE.md updated with feature | A | CI check: code changed but ARCHITECTURE.md didn't |
| Code-reviewer runs in /pre-commit-dev | B | Trigger: /pre-commit-dev invoked (embeds PoC check) |
| Write acceptance criteria before coding | B | Trigger: task created for non-trivial feature |
| /handoff when context long or session ending | B | Trigger: >3 features committed or >5 files edited |
| /finalize before creating PR | B | Trigger: user asks to open PR |
| Update ARCHITECTURE.md in same commit | B | Trigger: any code file changed |
| /plan before any feature | B | Trigger: user describes non-trivial feature work |

---

## Phase 1 — CLAUDE.md restructure + doc governance

**Goal**: CLAUDE.md becomes a ≤60-line router. Docs have types, templates, and
lifecycle rules. Every agent session starts with a complete, trustworthy orientation.

**Entry criteria**: None. Zero code risk.

### 1a. CLAUDE.md hard constraints — what stays vs. what moves

**Keep in CLAUDE.md** (truly hard, session-universal, binary pass/fail):

```
1. ARCHITECTURE.md updated in the same commit as any feature change  [CI-enforced]
2. Feature doc in docs/features/ for every customer-facing change    [/finalize checks]
3. make lint passes before committing (ruff + tsc + eslint)          [hook-enforced]
4. /finalize before every PR — no exceptions                         [B: trigger = PR]
5. One feature = one commit                                          [B: trigger = commit]
6. Acceptance criteria stated before any implementation              [B: trigger = task]
7. Run /pre-commit-dev after each feature unit (PoC check embedded)  [B: trigger = feature done]
```

**Move to docs/instructions/frontend-patterns.md**:
- Loading > Error > Empty > Data (never two states at once)
- Optimistic updates — update UI, revert on failure, show InlineError
- EmptyState for all empty data — sentence case, no emoji
- Error tone: "Couldn't [action]. Try again."
- fetchWithAuth only (no direct fetch())
- No toasts — InlineError component only

**Move to docs/instructions/backend-patterns.md**:
- Backend error shape: { detail: string }

**Move to docs/instructions/workflow.md** (working style, not binary rules):
- Think before coding — state assumptions, ask if unclear, push back if simpler
- Simplicity first — minimum code, no speculative abstractions
- Surgical changes — touch only what the task requires
- Goal-driven execution — transform tasks into verifiable criteria
- Subagent decision rules (when to spawn vs. work inline)
- TDD loop, context management, parallel agent patterns

**New CLAUDE.md** (target: 55-60 lines):

```markdown
# sed.i — Claude Code Instructions

## What this is
A read-it-later app. Users paste URLs; backend extracts content; frontend provides
reader, highlights, search, and writing workspace. See CONTEXT.md for vocabulary.

## Cold start
1. Check docs/handoffs/ — read the most recent one if it exists.
2. Use the key file paths table below. Do not grep to orient.
3. Check docs/decisions/ before any architectural decision.

## Key file paths
[table — unchanged]

## Run commands
make dev / make backend / make worker / make frontend / make test / make lint / make install-hooks

## Hard constraints — these 7 rules apply every session, every file
1. ARCHITECTURE.md updated in the same commit as any feature change
2. Feature doc in docs/features/ for every customer-facing change (same commit)
3. make lint passes before committing
4. /finalize before every PR — no exceptions
5. One feature = one commit
6. Acceptance criteria stated before any implementation
7. After any implementation >1 file changed: spawn code-reviewer subagent automatically

## Skill workflow
[table — unchanged]

## Automation classification
Rules 1 and 3 are CI/hook-enforced. Rules 4-7 are trigger-based: agent does them
when the trigger fires, without user instruction. See docs/instructions/workflow.md.

## When to read more
- Frontend UI work → docs/instructions/frontend-patterns.md
- Backend API / DB / Celery work → docs/instructions/backend-patterns.md
- Testing → docs/instructions/testing-standards.md
- Workflow, subagents, TDD → docs/instructions/workflow.md
- MCP tools → docs/mcp-wiki.md
- Architecture decisions → ARCHITECTURE.md + docs/decisions/
- Domain vocabulary → CONTEXT.md
```

### 1b. Doc taxonomy — eight types

| Type | Location | Consumer | Trigger | Lifecycle |
|------|----------|----------|---------|-----------|
| Architecture | ARCHITECTURE.md | Both | Every feature commit | Updated in same commit |
| Domain vocabulary | CONTEXT.md | Both | Term changes | Living; never archived |
| Instructions | docs/instructions/*.md | Agent-primary | New pattern established | Updated when pattern changes |
| Plans | docs/plans/*.md | Agent-primary | Before any feature | Draft→InProgress→Complete→Archived |
| Design docs | docs/design/*.md | Human-primary | After any significant feature ships | Updated when design changes |
| Handoffs | docs/handoffs/*.md | Agent-only | End of every session | Read at cold start; delete after 3 sessions |
| Retros | docs/retros/*.md | Process | After every merge | Immutable after written |
| Decisions | docs/decisions/*.md | Both | Before any architectural decision | Immutable after decided |

### 1c. Design docs — what "human understanding" means

Design docs are the human-primary doc type. They are NOT API references or user guides.
They are the document a tech lead would write to give someone complete mental ownership
of a feature: how it works, why this approach, what the limits are, what could change.

A good design doc lets you: (1) explain the feature confidently to anyone, (2) make
sound architectural decisions about adjacent features, (3) identify risks and limits
without re-reading the code.

**Template** (`docs/design/TEMPLATE.md`):

```markdown
---
type: design
status: active
last_updated: YYYY-MM-DD
consumer: human
---

# Design: <Feature Name>

## Problem being solved
What user pain or capability gap does this address? What was the user experience before?

## User experience (how it feels)
Walk through the feature from the user's perspective. What do they do, see, and feel?
No code. Written in plain language.

## Architecture overview
High-level diagram or prose: what components exist, how they talk to each other,
where data flows. Enough to understand the system without reading the code.

## Key design decisions
For each significant decision: what were the options, why was this chosen, what
did we trade away? Reference the relevant ADR in docs/decisions/ if one exists.

## Technical deep dive (for system design understanding)
The concepts worth internalizing: data models, algorithms, protocols, patterns.
Explain the "why" of the technical approach at a level that builds intuition.
This is the section that makes you a better architect.

## What this explicitly does NOT do
Scope boundaries. Deliberate non-goals and why.

## Limits and known failure modes
What breaks under what conditions? What are the performance cliffs? What are the
known edge cases that are accepted but not handled?

## Extension points
If someone wanted to extend this in the future, where would they plug in?
What would change, what would stay the same?

## Glossary of concepts introduced
Any new terms or concepts this feature introduces to the system.
```

**First design docs to write** (highest value for human understanding):
1. Hybrid Search system (search_router + hybrid_search + pgvector + tsvector)
2. MCP server (tools, OAuth, transport modes)
3. Ingestion pipeline (URL → pending → processing → complete)
4. Reader architecture (Reader.tsx, ReaderArticle.tsx, extension overlay)

### 1d. Doc frontmatter standard

All docs in docs/ get frontmatter:
```yaml
---
type: plan | instruction | design | feature | retro | decision | handoff
status: active | stale | archived
last_updated: YYYY-MM-DD
consumer: agent | human | both
---
```

### 1e. Doc audit

One-time pass over all docs in docs/:

**Plans (21 files)**: Add status frontmatter. Archive any plan >60 days old with
Status=Complete or Archived unless actively in-progress. Delete zombie plans (reference
deleted features with no remaining value).

**Handoffs**: Keep last 3, delete older.

**Instructions (7 files)**: Add frontmatter. ✓ Done. engineering-workflow.md and
skills-workflow-guide.md renamed to OBSOLETE-*; content consolidated into
docs/instructions/workflow.md and docs/instructions/deploy-to-prod.md.

**Decisions (7 ADRs)**: Add frontmatter. Link from CLAUDE.md "When to read more" table.

**Exit criteria for Phase 1:**

- [ ] `wc -l CLAUDE.md` ≤ 60
- [ ] All 7 hard constraints listed; 2 are CI/hook-enforced, 5 are trigger-based (B)
- [ ] All docs in docs/ have frontmatter (type, status, last_updated, consumer)
- [ ] `docs/design/TEMPLATE.md` and `docs/plans/TEMPLATE.md` exist
- [ ] `docs/decisions/TEMPLATE.md` exists
- [ ] Zombie/stale plans archived or deleted
- [ ] `docs/instructions/workflow.md` contains moved working-style content
- [ ] No regressions: `make test` and `make lint` pass

---

## Phase 2 — Verification system

**Goal**: Make "done" mean done. Every non-trivial feature gets acceptance criteria
before coding, integration smoke tests, and a fresh-agent review. Automatically.

**Entry criteria**: Phase 1 complete.

### 2a. Acceptance criteria (trigger-based, type B)

Add to CLAUDE.md (hard constraint #6) and expand in workflow.md:

```
Trigger: any task is created for a non-trivial feature (>1 file will change,
         or a new endpoint/service/component will be added)
Action:  Before writing any code, state:
         "This is done when: [user action] → [observable result]"

Examples:
  "Submit a URL → item appears in queue within 30s with title filled in"
  "Search 'react hooks' → article about React hooks in top 3 results"
  "Add article to list → article shows in list view, count increments"

If you cannot state an observable outcome, the feature is not scoped yet. Ask.
```

### 2b. Code-reviewer subagent — embedded in skills, not a standalone trigger

The PoC check belongs in the existing skill workflow, not as a loose CLAUDE.md constraint.
Two placements, two scopes:

**In `/pre-commit-dev` Phase 1b (new step — per-feature, before writing tests):**

```
After identifying what changed and before writing any tests:
1. Grep changed files for PoC markers:
   grep -rn "TODO\|FIXME\|hardcoded\|NotImplementedError\|return \[\]$\|return {}$" <files>
   Any match requires explanation or removal before proceeding.

2. If >1 file changed OR any new public function/endpoint/service was added:
   Spawn code-reviewer subagent:
   "Review [changed files]. Find only:
    (a) Data returned that is hardcoded, mocked, or from a stub
    (b) Unimplemented code paths (pass, ..., NotImplementedError, return [], return {})
    (c) Endpoints/functions that don't do what their name says
    (d) Calls to services/APIs that aren't actually wired up
   Report issues only. Skip style. Be specific: file:line."

Skip when: pure refactor, docs-only, test-only commit.
```

**In `/finalize` Phase 3f (new step — full-branch, before PR):**

```
Spawn fresh code-reviewer subagent on the complete PR diff:
"Review all files changed in this branch vs. main. Check the entire change set for:
 (a) Any feature that looks implemented but relies on mocked/stubbed/hardcoded data
 (b) Any integration point (API call, DB query, external service) not actually wired up
 (c) Any code path added in one file that requires a matching change in another that's missing
Report only genuine functional gaps. Skip style comments."
```

This removes the standalone CLAUDE.md constraint "spawn code-reviewer after >1 file" —
that behavior is now embedded in `/pre-commit-dev` (which already appears in the skill
workflow table). CLAUDE.md gets one fewer constraint.

### 2c. Golden-path integration tests

Five flows that cover the critical happy paths. These use the existing infrastructure
(real PostgreSQL, TestClient) — no mocks. They run first in CI.

**File**: `content-queue-backend/tests/test_golden_paths.py`

```python
"""
Golden-path integration tests — 5 critical user flows, real DB, real routes.
A failing test here means the app is broken for real users.
Run first: pytest tests/test_golden_paths.py before the full suite.
"""

class TestSubmitAndRetrieve:
    def test_submit_url_creates_pending_item(client, auth_headers)
    def test_submitted_item_is_retrievable(client, auth_headers)
    def test_duplicate_url_returns_409_with_existing_id(client, auth_headers)
    def test_other_user_cannot_see_item(client, auth_headers, other_auth_headers)

class TestSearch:
    def test_keyword_search_returns_matching_article(client, auth_headers, seeded_article)
    def test_search_scoped_to_current_user(client, auth_headers, other_user_article)
    def test_filter_by_author_finds_article(client, auth_headers, seeded_article)

class TestHighlights:
    def test_create_highlight(client, auth_headers, seeded_article)
    def test_get_highlights_for_article(client, auth_headers, seeded_highlight)
    def test_highlight_scoped_to_owner(client, auth_headers, other_user_highlight)

class TestLists:
    def test_create_list(client, auth_headers)
    def test_add_article_to_list(client, auth_headers, seeded_article)
    def test_list_content_scoped_to_owner(client, other_auth_headers, seeded_list)

class TestAuth:
    def test_unauthenticated_request_returns_401(client)
    def test_invalid_token_returns_401(client)
```

### 2d. Module-level docstrings

Every Python module in `app/` gets a 3-5 line docstring:
"What does this module do? What is its seam? What does it NOT do?"

Priority: `app/api/content.py`, `app/core/hybrid_search.py`, `app/core/search_router.py`,
`app/mcp/tools/content.py`, `app/mcp/tools/write.py`, `app/tasks/extraction.py`.

**Exit criteria for Phase 2:**

- [ ] `tests/test_golden_paths.py` exists with all 15 tests passing
- [ ] All app/ Python modules have module-level docstrings
- [ ] Acceptance criteria workflow documented in docs/instructions/workflow.md
- [ ] Code-reviewer subagent trigger and prompt documented in workflow.md + CLAUDE.md
- [ ] `make test` passes (golden paths + full suite)

---

## Phase 3 — Enforcement automation

**Goal**: Convert trigger-based rules (B) and instructions (C) into mechanical enforcement
(A) wherever possible. Remaining (B) rules have explicit trigger conditions.

**Entry criteria**: Phase 2 complete (golden-path tests pass in CI).

### 3a. Pre-push git hook

Committed to `.githooks/pre-push`. Installed via `make install-hooks`.

```bash
#!/bin/bash
set -e
echo "→ pre-push: lint + type check..."
cd content-queue-backend
PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check app/ --quiet
cd ../frontend
npx tsc --noEmit --pretty false 2>&1 | grep "error TS" | head -10
[ ${PIPESTATUS[0]} -eq 0 ] || exit 1
echo "✓ pre-push passed"
```

Add `make install-hooks` to cold-start steps in CLAUDE.md.

### 3b. CI: golden-path gate (type A)

Required check in `.github/workflows/backend-ci.yml`. Golden paths run before full suite.
If they fail, CI stops immediately. No merging without golden paths green.

### 3c. CI: eval regression gate (type A)

`content-queue-backend/tests/evals/baselines.json`:
```json
{
  "classification_accuracy": 1.00,
  "search_hit_rate_at_10": 0.80,
  "search_mrr": 0.70
}
```

A `check_baselines.py` script reads eval output and fails CI if any metric drops below
baseline. Baseline is updated manually with an explicit PR comment explaining why.

### 3d. CI: ARCHITECTURE.md freshness check (type A)

If any file in `app/` or `frontend/components/` changes in a commit but ARCHITECTURE.md
did not, CI fails with: "Code changed but ARCHITECTURE.md was not updated."

Exception: commits with message containing `[skip-arch]` (for minor refactors).

**Exit criteria for Phase 3:**

- [ ] `.githooks/pre-push` committed; `make install-hooks` in Makefile
- [ ] Golden-path test is a required CI check (not just present — required to pass)
- [ ] `baselines.json` exists; eval CI fails below baseline
- [ ] ARCHITECTURE.md freshness check in CI
- [ ] All checks pass on current branch

---

## Phase 4 — Code architecture

**Goal**: Deepen shallow modules, eliminate duplication, lower the complexity each agent
session has to navigate.

**Entry criteria**: Phase 3 complete.

### 4a. CONTEXT.md (domain vocabulary)

30-40 entries. Term + 3-5 sentences: what is it, what does it contain, what is it NOT,
where in the code. Key terms: ContentItem, Queue, Library, Ingestion, Processing, Reader,
Highlight, List, Draft, Hybrid Search, MCP, Normalization, Reading Status, Chunk, Embedding.

This was the highest-priority item in agent-harness-improvements.md and is still unbuilt.

### 4b. ContentIngestionService

Extract `ingest_content()` from the 120-line `create_content_item` handler into
`app/services/content.py`. Handler becomes ≤25 lines. Raises `DuplicateContentError`
instead of JSON-inside-detail. List attachment becomes a single batch query.

Full design: agent-harness-improvements.md Initiative 3.

### 4c. hydrate_items

`hydrate_items(rows, db)` in `app/core/hybrid_search.py`. Three callers (keyword_search,
_semantic_search, find_similar) replace their N+1 loops with one call.

Full design: agent-harness-improvements.md Initiative 4.

### 4d. Typed APIError + generated TS types

`fetchWithAuth` throws `APIError(status, detail, data)`. `frontend/src/generated/api.ts`
generated from FastAPI's OpenAPI spec. `types/index.ts` becomes a thin re-export wrapper.

Full design: agent-harness-improvements.md Initiative 5.

### 4e. Makefile + .env.example

`make install-hooks`, `make generate-types`. `.env.example` covers all vars in config.py.

Full design: agent-harness-improvements.md Initiative 6.

**Exit criteria for Phase 4:**

- [ ] CONTEXT.md at repo root, ≥25 entries, linked from CLAUDE.md
- [ ] `app/services/content.py` exists; handler ≤25 lines
- [ ] `hydrate_items` exists; no N+1 loops in search/MCP
- [ ] `APIError` in `frontend/lib/api.ts`; no `JSON.parse(err.message)`
- [ ] `frontend/src/generated/api.ts` committed and current
- [ ] Makefile has all targets including install-hooks and generate-types
- [ ] `.env.example` complete
- [ ] All tests pass; all lint passes

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CLAUDE.md restructure causes orientation loss | Medium | High | Cold-start test: can agent answer 5 questions from CLAUDE.md alone? |
| Golden-path tests flaky (DB state, ordering) | Medium | Medium | Transactions roll back after each test; no inter-test dependencies |
| ARCHITECTURE.md CI check false positives | Low | Low | Exempt commits with `[skip-arch]`; check paths not just file presence |
| Phase 4 refactors break existing behavior | Medium | High | Write service-layer tests before refactoring; keep HTTP tests as regression |
| Eval baseline too aggressive | Low | Medium | Start baselines at current measured values; raise only on verified improvement |

---

## Implementation sequencing

```
Phase 1 — CLAUDE.md + doc governance  [1 session, zero code risk]
  Highest leverage: every future session is cheaper

Phase 2 — Verification system         [1-2 sessions, low risk]
  Catches PoC code; trigger-based agent behavior documented

Phase 3 — Enforcement automation      [1 session, medium risk]
  Mechanical gates replace instructions; golden paths must exist first

Phase 4 — Code architecture           [2-3 sessions, medium risk]
  Lower complexity permanently; enabled by stable CI from Phase 3
```

---

## Open questions

1. **Start with Phase 2?** The verification gap (PoC code) feels most urgent. Phase 1
   (docs) has higher long-term leverage but lower urgency. Recommend: Phase 1 first —
   the CLAUDE.md restructure makes Phase 2 instructions land correctly.

2. **Design docs: which feature first?** Hybrid Search is the most technically complex.
   MCP is the most novel. Ingestion pipeline is the most central. Recommend: Hybrid Search,
   since it has the most conceptual depth worth internalizing.

3. **Eval baselines**: Classification accuracy is now 17/17 (100%) after the classifier
   refactor. Set baseline at 100%? Or 95% with one miss allowed? Recommend: 100% for
   classification (it's deterministic); 80% for hit-rate (depends on embeddings/content).

4. **Phase 4 scope**: ContentIngestionService is the most complex refactor. It can be
   a separate plan (this plan enables it). Leave it in Phase 4 or break it out?
