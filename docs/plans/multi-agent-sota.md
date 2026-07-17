# sed.i: Multi-Agent Research Orchestrator — Production Design

Feature proposal draft. Scope for deeper design work in-repo.

## Problem

sed.i currently exposes the library through single-shot retrieval: a client calls `search_content` or `get_content_item` once and gets back whatever matches. Real research questions ("what have I saved on agent eval design," "draft something using what I've read on production ML pipelines") need multiple rounds of search, synthesis across sources, and verification that the answer isn't fabricated.

This document specs an orchestration layer built to the standard of a system meant to run continuously, scale beyond one user, and survive real failure conditions — not a script that happens to work on a happy path. Every section below states the production requirement first, then the specific mechanism that satisfies it.

## Reference architecture

Orchestrator-worker pattern, following the architecture Anthropic published for Claude Research: one lead agent plans and synthesizes, subagents execute isolated search tasks in parallel and return condensed findings, and subagents never share state directly. Source: https://www.anthropic.com/engineering/multi-agent-research-system

The reference system runs at a scale and cost tolerance (roughly 15x a single chat call, per Anthropic's own accounting) that assumes broad research value per query. This design keeps the same control-flow shape but treats cost, concurrency, and failure isolation as first-class constraints from the start, not an afterthought layered on later.

## System boundaries and non-functional requirements

Before component design, the requirements that shape every decision below:

- **Concurrency**: multiple runs must be able to execute simultaneously without corrupting each other's state or exhausting shared resources (DB connections, LLM rate limits, Celery workers).
- **Multi-tenancy readiness**: even with one real user today, the schema and isolation model should not require a rewrite to add a second. Every state record is scoped by a tenant/user ID from day one.
- **Idempotency**: retrying a failed step must not duplicate side effects (duplicate subagent spawns, duplicate writes to write mode).
- **Observability**: every run must be traceable after the fact — what was searched, what was found, what it cost, where it failed — without reproducing the run.
- **Graceful degradation**: partial results are returned when a budget or timeout is hit, not a bare failure.
- **Backward-compatible schema evolution**: state record structure must be versioned so a running system doesn't break mid-deploy when the schema changes.

## Components

### 1. Lead agent (orchestrator)

- Receives the question, decides a search strategy, persists the plan before spawning any work.
- Evaluates subagent results for sufficiency; on a gap, spawns another round up to a hard cap.
- Runs as a Celery task, not an in-request synchronous call — multi-round runs have unbounded latency and cannot block an HTTP worker (see Execution model).

### 2. Subagents (workers)

- Each subagent is an independent Celery task with its own timeout, retry policy, and isolated context — not a function call inside the lead agent's process. This is what makes "parallel" real: subagents run concurrently on the worker pool, not sequentially inside one process pretending to be parallel.
- Task contract, explicit and machine-checked, not just a described convention:
  ```
  Input:  { run_id, subagent_id, task_description, output_schema, budget: {tokens, timeout_s} }
  Output: { ok: bool, data: {...} | null, error: {code, message} | null,
            meta: {tool_calls, tokens_used, duration_ms} }
  ```
- Invalid input is rejected with a machine-readable error, not silently coerced.
- No subagent-to-subagent communication. All coordination goes through the lead agent and the persisted state record.

### 3. Citation/verification step

- Runs as its own task after synthesis, using a smaller/cheaper model than synthesis (a model cascade: expensive reasoning for planning and synthesis, a fast/cheap classifier for verifying each claim against a source ID). This is a real cost control, not just an architecture preference.
- Any claim that fails to trace to a retrieved item ID is cut or flagged before the draft reaches write mode. Verification failures are logged with enough detail to debug which claim failed and why, not just a pass/fail flag.

### 4. Execution model: async, not synchronous

A multi-round agent run has latency measured in tens of seconds to minutes. Production requirement: this cannot hold an HTTP connection open or block a web worker.

- User-facing endpoint enqueues a Celery task and returns a `run_id` immediately (202 Accepted pattern).
- Client polls a status endpoint or subscribes via websocket/SSE for state updates, backed by the persisted state record (see below).
- The lead agent and each subagent are separate Celery tasks, dispatched onto the existing worker pool, not threads inside one long-lived process.

### 5. Persisted state record (the reliability backbone)

Stored in Postgres, versioned schema, one row per run:

```
{
  schema_version: int,
  run_id: uuid,
  tenant_id: uuid,              -- multi-tenancy from day one
  question: str,
  plan: str,
  searches_run: [ { subagent_id, query, tool_calls, idempotency_key } ],
  items_retrieved: [ item_id ],
  gaps_identified: [ str ],
  iteration_count: int,
  status: "queued" | "planning" | "searching" | "synthesizing" | "verifying" | "done" | "failed" | "partial",
  cost: { tokens, latency_ms, tool_call_count, usd_estimate },
  budget: { max_tokens, max_iterations, max_subagents, timeout_s },
  error: { code, message, failed_step } | null,
  created_at, updated_at
}
```

Every step transition is a write to this record, not just an in-memory variable — this is what makes a run resumable, auditable, and debuggable without re-running it. `idempotency_key` per search prevents duplicate tool calls on task retry.

## Reliability and failure handling

This is the section a production system lives or dies on, and it's specified explicitly rather than assumed:

| Failure | Handling |
|---|---|
| Subagent task times out | Celery task-level timeout kills it; lead agent treats missing result as a gap, not a crash; run continues with partial data |
| Subagent tool call fails (downstream API error) | Error surfaced back into the subagent's own context via the `{ok, data, error}` contract so it can retry with a different approach, not silently swallowed |
| Retry causes duplicate side effect | Every retryable action carries an idempotency key; write-mode writes are upserts keyed on `run_id`, not blind inserts |
| LLM provider outage or rate limit | Retry with exponential backoff + jitter; circuit breaker opens after N consecutive failures to stop hammering a down provider; run falls back to partial results rather than hanging indefinitely |
| Worker process crash mid-run | State record's last written status lets a recovery job detect orphaned runs (no update in N minutes while status is non-terminal) and either resume or mark failed |
| Runaway cost/iteration | Budget checked before every new iteration and every subagent spawn; run is force-terminated and marked `partial` with whatever synthesis is possible from data gathered so far |
| Cascading retries under load | Backoff + circuit breaker (above) specifically to prevent the retry-storm pattern where a slow downstream dependency gets hit harder by retries and gets slower |

## Guardrails

| Failure mode | Guardrail |
|---|---|
| Excessive subagent spawning | Hard cap enforced in code (`budget.max_subagents`), not just prompted against |
| Redundant/looping search | Lead agent checks new queries against `searches_run` before spawning another round; hard cap on `iteration_count` |
| Runaway cost | Budget check before each iteration; terminate to `partial` status on breach |
| Fabricated claims | Mandatory citation-check step, cannot be skipped, gates the write-mode write |
| Rigid evaluation of agent paths | Eval grades run outcome (correct, grounded answer) not exact tool-call sequence — agents legitimately take different valid paths |

## Observability

Non-negotiable for anything called "production":

- Every run emits structured OTel spans: one span per lead-agent step, one per subagent task, one for verification — parent/child relationships preserved so a single run's full trace is inspectable as a waterfall, not scattered log lines.
- Golden signals tracked per run and in aggregate: latency (per step and end-to-end), error rate, cost, saturation (queue depth on the Celery worker pool).
- This is where norma is the natural home: the same OTel-span and policy-enforcement pattern norma already implements against a POC target now traces a system doing real, repeated work.
- Structured logs carry `run_id` and `tenant_id` on every line so a single run can be traced across the lead agent, every subagent, and the verification step without guessing.

## Evaluation (CI-gated, not just manual spot-checks)

- Offline eval suite: a fixed set of real questions against a snapshot of the library, with expected grounded answers, run in CI before any change to prompts or orchestration logic ships.
- Grading is outcome-based (did the run produce a correct, source-grounded answer) with partial credit for partially correct multi-part answers, not a rigid check of the exact tool-call sequence taken.
- Online monitoring: sample a percentage of real runs for periodic re-grading (human or LLM-as-judge, calibrated against human judgment) to catch drift as the library grows and question patterns shift.

## Routing

Not every question needs the full loop:

- Lightweight classifier (cheap/fast model) in front of the orchestrator: simple lookup → direct single call to `search_content`, bypassing orchestration entirely. Multi-hop question → full loop.
- This keeps cost proportional to task complexity instead of running the expensive path for every request, and it's the first line of defense against the system being "an agent for everything."

## Deployment and rollout

- Every orchestration-logic change (prompts, budgets, routing thresholds) ships as a versioned, immutable deploy — rollback means redeploying the previous version, not patching forward.
- Canary rollout for orchestration changes: route a small percentage of real runs through the new version, compare cost/latency/eval-pass-rate against the current version before shifting more traffic. Given this is agent behavior (not a simple stateless service), the canary comparison should include eval-suite pass rate, not just error rate and latency.
- Schema migrations for the state record are additive and backward-compatible within a deploy window (add columns, don't drop/rename in the same deploy) so a rollback doesn't break against already-migrated data.

## Security and multi-tenancy

- Every state record and every tool call scoped to `tenant_id`, enforced at the query layer, not just in application logic — a bug in orchestration code should not be able to leak one user's library into another user's run.
- Rate limiting per tenant on run creation, to prevent one user's runaway usage from starving the shared worker pool.
- Secrets (LLM API keys) never embedded in state records or logs; verification-step and subagent tasks pull credentials from the existing secrets configuration, not from data passed through the run.

## Memory: consolidated, beyond single-run state

- Periodic background job (existing Celery worker, scheduled) reviews recent runs and recent saves, writing a synthesized summary of recurring topics and how new content connects to prior reading — a distinct mechanism from per-run retrieval, closer to periodic re-summarization than search.
- Staleness handling: time-bound content should decay in relevance rather than resurface uncritically.
- This layer's job is also scoped by `tenant_id` and versioned the same way per-run state is, so it scales the same direction as the rest of the system if a second user is ever added.

## Skills

Recurring behaviors packaged as discrete, on-demand-loaded instruction sets, following Anthropic's Agent Skills format (opened as a shared standard in March 2026, distinct from MCP's tool-connectivity role):

- `weekly-digest`, `connect-new-save`, `draft-from-highlights` as initial set.
- Loaded only when the routing step determines the task applies, keeping standing context small regardless of how many skills exist.
- Worth re-checking against Anthropic's current documentation before implementation, since the standard is recent.

## Explicitly out of scope for v1

- Cross-provider model failover at the scale Sierra or Anthropic run it (relevant once the user base and reliability requirements justify the added complexity, not before).
- Swarm/peer-to-peer agent topology — orchestrator-worker only, by design, for debuggability.
- A claim that this is equivalent to Claude Research at its actual scale. It's built to production engineering standards, run against a smaller corpus and lower request volume — the discipline is the same, the scale is not, and that distinction should stay explicit in any writeup or interview description of this project.

## Suggested build order

1. State record schema (versioned, tenant-scoped) + async execution model (enqueue/poll) — prove the plumbing before any agent logic.
2. Single-subagent version, no parallelism — prove the tool-call contract, idempotency, and retry/backoff work end to end.
3. Parallel subagents with isolation boundary enforced.
4. Evaluator step (gap detection, capped iteration) + budget enforcement and circuit breaker.
5. Citation/verification step as a separate, smaller-model task.
6. OTel instrumentation + CI-gated eval suite — required before this handles real, non-trivial questions, not optional polish.
7. Canary deploy process for orchestration-logic changes.
8. Consolidated memory job.
9. Skills packaging.
10. norma integration for full tracing and LLM-as-judge grading.

## Open questions for repo-level design

- Postgres vs. a dedicated state store (e.g. Redis for hot run status, Postgres for durable history) — depends on expected polling frequency and run volume.
- Exact circuit-breaker thresholds and backoff parameters — needs load-testing against real LLM provider rate limits, not guessed.
- Canary traffic percentage and promotion criteria for orchestration changes.
- Whether verification-step model choice should be configurable per deploy environment (cheaper model in dev/test, calibrated production model in prod).
