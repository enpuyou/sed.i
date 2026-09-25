---
type: decision
status: active
last_updated: 2026-07-23
consumer: both
---

# ADR-0008: Service-Layer Threshold for API Routers

**Status:** Accepted

---

## Context

`app/services/content.py` is the only file under `app/services/` in the backend.
It exists because `ingest_url()` — URL normalization, duplicate detection,
`ContentItem` creation, Celery dispatch — is called from two entry points (the
REST API and the MCP `add_content` tool) and is non-trivial enough that
duplicating it inline in both places would drift.

Every other router (`search.py` at 870 lines, `content.py` at 767, `auth.py`
at 562, down to small ones like `memory.py`/`analytics.py`/`themes.py` under
100) writes its business logic directly inside `@router` function bodies. This
is not inconsistent by accident — it's undecided. There is no stated rule for
when new logic should go in `app/services/` versus inline in the route
handler, so the next router added inherits whichever pattern its author
happens to reach for, not a rule the codebase enforces.

## Decision

**Extract to `app/services/<domain>.py` only when a piece of route logic is
called from more than one entry point, OR exceeds roughly 80 lines of
non-trivial logic (validation branches, multi-step orchestration, business
rules — not boilerplate CRUD) within a single route handler.**

Concretely:

- Called from 2+ places (REST route + MCP tool, REST route + Celery task,
  etc.) → extract, regardless of size. This is the `content.py` precedent —
  the deciding factor was reuse, not line count.
- Confined to one route handler and under ~80 lines → leave inline. Most of
  `auth.py`, `vinyl.py`, `lists.py`, `memory.py` fit here today and do not
  need retrofitting.
- Confined to one route handler but over ~80 lines of real logic (not just a
  long docstring or a wide SQL query) → extract even if there's only one
  caller today, because a handler this size is doing enough that testing it
  without spinning up the full HTTP stack becomes valuable on its own.

This is a threshold to apply going forward, not a retrofit mandate. Existing
routers are not touched unless a future change to that router crosses the
threshold anyway.

## Consequences

**Positive**: the next engineer (or agent) adding a route has a stated rule
to check against instead of pattern-matching whichever neighboring file they
happened to open first. `app/services/` stops being "the one thing that
happens to be a service" and starts being a real, if currently small,
architectural layer with a legible membership rule.

**Negative**: the ~80-line threshold is a judgment call, not a hard metric a
linter can enforce — two engineers could reasonably disagree on a borderline
case. Accepted; the goal is a shared default, not a mechanically enforced
policy.

**Neutral**: `search.py`'s connections/insight subsystem (`_search_highlights`,
`_connections_for_highlight`, `_call_insight`) is a case this rule would now
flag for extraction (single caller, but well over 80 lines of real logic) —
left as-is per the no-retrofit clause above, worth extracting the next time
that code is meaningfully touched, not as a standalone cleanup PR.

## Alternatives considered

### Option A: Extract everything to `app/services/` uniformly
Rejected. Most routers (`memory.py`, `analytics.py`, `themes.py`, much of
`auth.py`) are simple enough that a service-layer indirection would be pure
ceremony — an extra file to open with no logic worth isolating. Matches
CLAUDE.md's "minimum code that solves the problem" principle.

### Option B: Formally scope `app/services/` to "content ingestion only"
Considered and rejected as too narrow. The actual deciding signal in the
`content.py` precedent was reuse-across-entry-points, which will recur (e.g.
a future in-app chat surface calling into the same search/synthesis logic
MCP tools already use) — scoping the pattern to one domain name would just
mean re-deciding this again the next time it recurs.

### Option C: No rule — decide case-by-case as it comes up
This is the status quo the gap analysis flagged as the actual problem: no
rule means the decision silently defaults to "match whatever the nearest
file does," which compounds inconsistency rather than resolving it.

## References

- `docs/design/systems/sota-industry-comparison.md` §8.3, §8.6 — original
  gap analysis naming this inconsistency
- `docs/plans/sota-gap-action-plan.md` item 13
- `app/services/content.py` — the one existing precedent this rule
  generalizes from
