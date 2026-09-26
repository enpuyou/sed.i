---
type: design
status: active
last_updated: 2026-07-22
consumer: human
---

# Reranking, Temporal, and Agent Frameworks — What They'd Actually Change

Written to support a real decision, not as a survey. For each concept:
what it does mechanically, what it would concretely change in sed.i's
code if adopted, and what it costs. No recommendation baked in beyond
what's already in `sota-stack-and-workflow-review.md` — this doc exists
so that recommendation can be evaluated on its merits rather than taken
on faith.

**§1 (Reranker) update, 2026-09-25**: the decision this section supported
was made — Cohere Rerank v3.5 was built and evaluated
(`evals/reranker/results/report.md`, since deleted). Result: the eval's own
verdict was "investigate, not ship as-is" (a guard-rail regression, and
gains/losses that roughly cancelled by query type); it shipped anyway and
was later removed under a cost-constrained, multi-user product direction.
See `docs/changelog/2026-09-25-reranker-removal-entity-flag-eval-sync.md`
for the full numbers. The mechanical explanation below is still accurate as
a concept reference; treat it as background, not as a live option under
evaluation.

---

## 1. Reranker on top of RRF — what does it add?

### What RRF actually does, mechanically

`rrf_fuse()` in `hybrid_search.py` takes N *already-ranked* lists (keyword
results ranked by `ts_rank_cd`, semantic results ranked by cosine
similarity, entity results ranked by IDF-dampened score) and merges them
using only **rank position**, not the underlying scores:

```
score(doc) = Σ 1/(60 + rank_in_list_i)   for each list i containing doc
```

A document ranked #1 in the keyword list and #3 in the semantic list gets
`1/61 + 1/63`. A document that appears in only one list still scores, just
lower. This is a voting mechanism across retrieval methods — it never
looks at the actual text of the query or the document. It only looks at
*where each method placed each document in its own list*.

Why rank instead of raw score: `ts_rank_cd` (a keyword relevance score)
and cosine similarity (a semantic score) live on completely different,
incomparable scales. You can't average a `ts_rank_cd` of 0.3 with a cosine
similarity of 0.85 and get anything meaningful. Rank position is
scale-free — "1st place" means the same thing regardless of which
scoring function produced it. That's RRF's entire value proposition:
sidestep the score-comparability problem.

### What a reranker does, mechanically

A reranker takes the **candidates that already survived retrieval** (say,
the top 20-50 from RRF fusion) and re-scores each one by actually reading
the query and the candidate document *together*, in the same forward
pass. This is architecturally different from everything upstream of it:

- **Bi-encoder** (what `text-embedding-3-small` is): encodes the query
  into a vector and the document into a vector *separately*, then compares
  vectors with cosine similarity. Fast (documents can be pre-embedded once
  and stored — this is why `content_chunks.embedding` exists as a stored
  column), but the model never sees the query and document interacting.
  It can't notice that "attention" in the query and "attention" in a
  document about classroom management are the same word but unrelated
  concepts, because it encoded them independently before ever comparing.
- **Cross-encoder** (what a reranker is): feeds `[query, document]` into
  the model *together* as one input, so every layer of the model can
  attend across both texts simultaneously and produce one relevance
  score. This is strictly more expressive — it can catch exactly the kind
  of contextual mismatch a bi-encoder misses. The cost is that this can't
  be precomputed: you need one forward pass per (query, candidate) pair,
  at actual query time.

### Concretely, what would change in sed.i's code

Today: `hybrid_search(mode="full")` produces `fused_results`
([hybrid_search.py:779-787](../../content-queue-backend/app/core/hybrid_search.py)),
already paginated by RRF rank, returned directly to the caller.

With a reranker: insert a step between fusion and pagination — take the
top ~20-50 fused results, send `(query, candidate_text)` pairs to a
reranker (Cohere's Rerank API, or a self-hosted cross-encoder like
`bge-reranker-base`), get back a new relevance score per candidate,
re-sort by that score, *then* paginate. The RRF step doesn't go away — it
still does the job of cheaply narrowing thousands of documents down to a
manageable candidate set. The reranker's job is precision on that already-
narrowed set, not recall across the whole corpus (a reranker over your
entire library would be prohibitively slow; it's a refinement, not a
long an initial retrieval mechanism).

### What it would concretely buy sed.i

The RRF fusion's blind spot is exactly the case a cross-encoder catches:
a document that scores well in one lane (say, keyword match on "attention")
but is topically wrong, or a document that's genuinely the best answer but
only mediocre in *every* individual lane (so it never ranks high enough in
any single list to score well under RRF, even though a human reading query
+ document together would immediately recognize it as the best match).
The entity-lane's non-standard score-injection into RRF ([hybrid_search.py:765,
776](../../content-queue-backend/app/core/hybrid_search.py)) is exactly
the kind of fusion-math imprecision a reranking pass downstream would
make less consequential — errors introduced by the fusion heuristics get
a second, more accurate pass to correct them.

### What it costs

- Added latency per search: cross-encoder reranking over ~20-50
  candidates is typically 50-200ms depending on provider/model — not free,
  but well within an interactive search budget.
- A new external dependency (Cohere API) or new infra (self-hosted model
  via Modal) — sed.i has neither today.
- It's a **measurable** change: sed.i already has `evals/retrieval/`
  (R@10, MRR, NDCG@10 across variants) — this is exactly the kind of
  change you'd run before/after and look at the delta, not something you
  have to take on faith. That's the strongest argument for doing it soon
  rather than deferring further: the cost of finding out if it's worth it
  is low.

---

## 2. Temporal — what does "durable execution" actually mean, and what would change?

### The problem in sed.i's own code, concretely

`app/tasks/research.py`'s pipeline is: lead plans sub-questions → dispatches
a `celery.group` of subagents → `celery.chord` callback collects results →
synthesizer → verifier. Each of those is a **separate Celery task**,
independently queued, independently executed, with no shared process or
memory between them. State is threaded through by writing to the
`ResearchRun` Postgres row at each step
([research.py](../../content-queue-backend/app/tasks/research.py)) — not
by keeping anything in memory across steps.

Now: what happens if the Celery worker process crashes *while a subagent
task is running*? The subagent task itself is lost — Celery doesn't retry
a task that never returned a result (that's different from a task that
raised an exception, which Celery *can* retry). Nothing else in the
system knows the subagent died until the `chord` callback either never
fires (because it's waiting on a result that will never arrive) or fires
with an incomplete set of results. sed.i's actual answer to this is
`recover_orphaned_runs_task`, a beat task that runs every 5 minutes and
marks any `ResearchRun` stuck in a non-terminal status for more than 10
minutes as `partial`. That's not resuming the work — that's noticing the
work died and giving up on it gracefully, discarding whatever LLM calls
had already been paid for in that run.

### What Temporal actually does, mechanically

Temporal's core idea: you write what looks like ordinary sequential code
(a "workflow function") —

```python
def research_workflow(question):
    plan = yield planner_activity(question)
    results = yield [subagent_activity(sq) for sq in plan.sub_questions]
    brief = yield synthesizer_activity(results)
    return yield verifier_activity(brief)
```

— but Temporal's runtime doesn't just execute this function once. Every
time an `activity` (Temporal's term for a unit of work — like a Celery
task, but tracked differently) completes, Temporal durably persists an
**event** to its own event-sourced log: "activity X completed with result
Y." If the process running this workflow crashes at any point — mid
`synthesizer_activity`, say — Temporal doesn't restart the whole workflow
from scratch. It **replays the event log** to reconstruct exactly where
execution was (it sees "planner_activity completed," "subagent_activity
completed ×6," and knows synthesizer_activity was in flight), then
resumes from that exact point on a different worker process. The
`research_workflow` function's local variables (`plan`, `results`) are
reconstructed from the event log automatically — you don't write any
recovery code yourself.

This is fundamentally different from Celery's model. Celery tasks are
independent, fire-and-forget units of work with no shared execution
context — the "workflow" only exists implicitly, in application code that
chains tasks together and tracks state in your own database (which is
exactly what `ResearchRun`'s JSONB columns and status state machine are:
a hand-rolled version of what Temporal's event log gives you natively).

### Concretely, what would change in sed.i's code

- The 6 Celery tasks (lead, subagent ×N, collector, synthesizer, verifier)
  would become **activities** called from within one Temporal
  **workflow function** — the sequencing logic currently split across
  `.delay()` calls, `celery.chord` callbacks, and the `ResearchRun.status`
  state machine collapses into one function that reads like synchronous
  code.
- The hand-rolled state machine (`queued → planning → searching →
  synthesizing → verifying → done | partial | failed`) becomes largely
  unnecessary — Temporal's own workflow execution state *is* that state
  machine, queryable via Temporal's UI/API instead of a custom Postgres
  column.
- `recover_orphaned_runs_task` (the 10-minute staleness poll) goes away
  entirely — Temporal's replay-from-event-log *is* the recovery
  mechanism, and it's exact (resumes from the last completed activity),
  not approximate (declares the run dead after a timeout window).
  `idempotency_key`-based resume support (currently hand-rolled via
  `searches_run` JSONB) also becomes largely redundant — Temporal
  guarantees an activity only "completes" once from the workflow's
  perspective, even if it's retried internally.
- New infrastructure: a Temporal server, Temporal UI, and (per the
  existing plan in `sota-layer-plan.md` Layer 7) Elasticsearch — 3
  services that don't exist in sed.i's stack today, versus Celery/Redis
  which are already running.

### What it costs

This is real infrastructure, not a library import. Per the existing
Layer 7 plan, it's budgeted at 2-3x a normal layer and split into two PRs
specifically because of that weight. The Celery ingestion pipeline
(extraction → embedding → tagging → entity analysis) would **not** move
to Temporal under the current ADR-0005 plan — only the research agent
would, meaning sed.i would run two orchestration systems side by side
long-term, not migrate wholesale. That's a deliberate, reasoned choice in
the ADR (Celery is fine for short async dispatch; Temporal is specifically
for the research agent's multi-step, expensive-to-lose durability needs)
but it is added operational surface, not a swap.

### What it buys, precisely

Not "better agents" — Temporal has no opinion about what an agent should
do, what tools it should call, or how it should reason. It buys **exact
resume instead of approximate abandonment** when something crashes
mid-run. The current cost of not having this: a worker crash during a
3-iteration × 6-subagent research run (potentially dozens of LLM calls,
real dollars already spent) loses everything past the last DB write and
serves the user a `partial` result. With Temporal, that same crash loses
nothing — the workflow resumes from the exact last completed activity on
a different worker.

---

## 3. LangGraph, CrewAI, AutoGen — what's actually different from what sed.i built?

### The layer these operate at

All three are **agent orchestration frameworks** — they answer "how do
multiple LLM-calling steps coordinate, pass state, and make decisions
about what to do next," which is a different question from "how do I
call an LLM" (that's `LLMClient`/the OpenAI SDK's job, and it stays
exactly as-is regardless of what orchestration layer sits above it) and a
different question from "how do I guarantee a multi-step process survives
a crash" (that's Temporal's job). The three questions are stackable and
independent:

```
LangGraph / CrewAI / AutoGen   ← "what happens next, and in what order"
        ↓ (calls)
    LLMClient / OpenAI SDK      ← "make one model call"
        ↓ (could run on top of)
    Temporal (optional)         ← "survive a crash mid-sequence"
```

sed.i today has the bottom layer (LLMClient) and a hand-rolled version of
the top layer (Celery `group`/`chord` + a Postgres status column), with
no durable-execution middle layer. Adopting LangGraph would replace the
*middle-to-top* coordination logic — the `group`/`chord`/state-machine
code in `research.py` — not `LLMClient`, which LangGraph would still call
underneath (or you'd swap for LangGraph's own model-call wrappers, but
that's a separate, smaller decision).

### What LangGraph specifically is

A graph-based state machine, explicitly modeled as nodes and edges:

```python
graph = StateGraph(ResearchState)
graph.add_node("plan", planner_node)
graph.add_node("search", subagent_node)
graph.add_node("synthesize", synthesizer_node)
graph.add_edge("plan", "search")
graph.add_conditional_edges("search", route_based_on_coverage)
```

Each node is a function that receives the current state, does work
(typically an LLM call), and returns an update to the state. The graph
structure makes control flow *explicit and inspectable* — you can look at
the graph definition and see every possible path execution can take,
including loops (an agent re-planning after getting a bad search result)
and conditional branches (route to "synthesize" if coverage is sufficient,
back to "search" if not). LangGraph also ships a **checkpointer** —
optional persistence of state after each node, which is a *lighter*
version of what Temporal does (it can resume a graph from its last
checkpoint) but without Temporal's stronger guarantees (LangGraph's
checkpointing is opt-in and typically backed by a database you configure,
not an event-sourced durable log with automatic replay semantics).

Compare to sed.i's actual control flow today: the lead/subagent/collector
iterate-vs-synthesize decision
([research.py](../../content-queue-backend/app/tasks/research.py),
`collect_subagent_results_task`) is an if/else chain inside a Celery task
body, not a declared graph — the "edges" of sed.i's control flow exist
only as the sequence of `.delay()` calls scattered across multiple
functions. This is functionally similar to what LangGraph gives you, just
implicit (you have to read the code to reconstruct the flow graph) rather
than explicit (the graph *is* the code).

### What CrewAI and AutoGen are, and how they differ from LangGraph

- **CrewAI**: role-based — you define named agents with roles/goals/tools
  ("Researcher," "Writer," "Critic") and a process for how they hand off
  work (sequential or hierarchical). It's a higher-level abstraction than
  LangGraph — less control over exact control flow, more built-in
  scaffolding for "agents as collaborating personas." Closer in spirit to
  sed.i's lead/subagent/synthesizer/verifier *naming*, but CrewAI would
  impose its own opinions about how those roles communicate rather than
  sed.i's current explicit Celery dispatch.
- **AutoGen** (Microsoft): conversation-centric — agents "talk" to each
  other in a simulated chat, with a controller deciding whose turn it is
  to respond next. Best fit for open-ended multi-agent negotiation/debate
  patterns; a less natural fit for sed.i's research pipeline, which is a
  fairly linear plan→search→synthesize→verify shape, not an open-ended
  conversation between agents.

Neither of these is a closer match to sed.i's existing architecture than
LangGraph — sed.i's pipeline is already graph-shaped (a DAG with one
conditional loop), which is exactly LangGraph's native model, not
CrewAI's role/crew model or AutoGen's conversational model.

### What adopting LangGraph would concretely change

- `research.py`'s Celery `group`/`chord` orchestration code gets replaced
  by a `StateGraph` definition — the sequencing becomes declarative
  instead of imperative.
- The hand-rolled `ResearchRun.status` state machine could partially
  collapse into LangGraph's own state object, though you'd likely still
  want a Postgres row for the parts of `ResearchRun` that are product data
  (the brief itself, citations) rather than pure execution state.
- **Celery would not go away** — it's still the right tool for the
  ingestion pipeline (extraction → embedding → tagging), which has no
  agentic decision-making in it at all, just a fixed sequence of
  independent async jobs. LangGraph is specifically an *agent*
  orchestration tool; using it for the ingestion pipeline would be the
  wrong tool for a job that doesn't need conditional branching or agent
  state.
- This means the outcome is **two orchestration systems** (Celery for
  ingestion, LangGraph for the research agent) rather than one — the same
  shape ADR-0005's Celery/Prefect/Temporal three-tier plan already
  describes, just with LangGraph substituted for Temporal in the "handle
  the research agent" slot.

### The actual comparison to make: LangGraph vs. Temporal for the research agent specifically

This is the decision that matters, because they're not solving the same
problem even though both would touch the same code:

| | LangGraph | Temporal |
|---|---|---|
| Primary job | Express control flow (branches, loops) explicitly | Guarantee a multi-step process survives a crash |
| Durability | Optional checkpointing, weaker guarantee | Core guarantee, event-sourced replay |
| Changes to `research.py`'s *agent logic* | Yes — control flow gets rewritten as a graph | No — activities wrap existing task bodies mostly as-is |
| Solves the orphaned-run problem sed.i has today | Partially, if checkpointing is configured carefully | Yes, natively, as its core design goal |
| New infra required | A Python library — no new services | Temporal server + UI + Elasticsearch |
| Already reasoned about in this codebase | No | Yes — ADR-0005 |

The concrete gap sed.i has *right now* — a worker crash mid-research-run
loses progress and downgrades to `partial` — is a durability gap.
LangGraph is not primarily a durability tool; adopting it would improve
code clarity (control flow becomes explicit) without directly closing
that gap unless its checkpointer is deliberately wired up to the same
rigor Temporal provides by default. Temporal is purpose-built for exactly
this gap and is what ADR-0005 already committed to on paper. Swapping in
LangGraph instead would be answering a code-quality question ("is the
control flow readable") with a tool chosen for a different question
("does the workflow survive a crash") — not wrong, but worth being
precise that it's solving a different problem than the one that's
currently open.

---

## Summary — three independent axes, not one bundled decision

These three are separable, and you don't have to accept or reject them
as a package:

1. **Reranker**: purely a retrieval-quality lever, additive to the
   existing RRF fusion, cheap to trial-run against the existing eval
   harness, no architectural risk.
2. **Temporal**: purely a durability lever for the research agent
   specifically, real infrastructure cost, already reasoned about and
   decided in ADR-0005 — the open question is execution, not the choice.
3. **LangGraph/CrewAI/AutoGen**: a control-flow-expression choice for the
   research agent's orchestration layer, orthogonal to durability — could
   be adopted instead of, in addition to, or independent of Temporal, and
   would not replace Celery for the (non-agentic) ingestion pipeline
   regardless of which agent framework is chosen.
