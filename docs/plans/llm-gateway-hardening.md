---
type: plan
status: complete
last_updated: 2026-07-21
consumer: agent
---

# Plan: LLM Gateway Hardening

Date: 2026-07-21
Status: Complete — all four phases implemented, 712 backend tests passing

## Goal

Close the gap between what SOTA LLM gateway products (LiteLLM, Portkey, Helicone) provide out
of the box and what sed.i's hand-rolled gateway (`app/core/llm_client.py`) currently does —
cost attribution, per-user spend ceilings, transient-error retry, request timeouts, and uniform
tracing across both providers — without replacing the abstraction itself.

## Non-goals

- **Not adopting an external gateway product** (LiteLLM, Portkey, Helicone, OpenRouter). See
  Architecture decision 1.
- **Not building a Postgres cost ledger.** Braintrust already computes and stores per-call cost;
  duplicating it in Postgres is drift risk for no query capability we don't already have via the
  Braintrust API/UI.
- **Not adding semantic caching for chat completions.** `embedding_cache.py` already caches
  query embeddings; most chat calls here are per-article and not repeated, so cache hit rate
  would be near zero.
- **Not adding a separate requests/minute rate limit for LLM calls.** The per-user dollar budget
  (Phase 3) already bounds worst-case spend; a second, independent request-count limiter is
  redundant machinery for the same problem.
- **Not touching AWS infra (`infra/` Pulumi project).** The existing $20/month account-level
  Bedrock budget alarm (ADR-0003) is unaffected — this plan adds an application-level per-user
  ceiling underneath it, not a replacement.
- **Not changing model selection, provider choice, or `LLM_PROVIDER` default.**

## Prior art

- [`sota-layer-plan`](sota-layer-plan) — Layer 0 built `LLMClient` as the single LLM entry
  point; Layer 1 wired Braintrust tracing on top of it. Layer 1's own scope note ("cost
  attribution") was deferred — this plan is that deferred work, tracked as new Layer 11 in that
  doc's status table.
- [ADR-0003 — LLM Provider Strategy](../decisions/0003-llm-provider-strategy.md) — explicitly
  evaluated and rejected LiteLLM as the provider-abstraction layer ("worth adding if we add 3+
  providers"; sed.i has 2). Also documents the existing best-effort failover behavior this plan
  is refining (Phase 1), and the AWS-side $20/month Bedrock budget alarm that Phase 3's per-user
  ceiling complements rather than duplicates.
- Prior conversation research (this session): surveyed 2026 build-vs-buy guidance for LLM
  gateways and confirmed Braintrust — already active via `braintrust.wrap_openai()` in
  `llm_client.py` — computes per-call dollar cost automatically from its own pricing registry.
  The "no cost tracking" gap is a wiring problem, not a missing-infrastructure problem.

## Current state

`app/core/llm_client.py::LLMClient` is a module-level singleton (`llm_client`) used by every
LLM call site in the codebase (11 files: `embedding.py`, `tagging.py`, `summarization.py`,
`article_analysis.py`, `entity_dedup.py`, `chunk_embeddings.py`, `entity_embedding.py`,
`request_router.py`, `search.py::_call_insight`, `mcp/tools/summarize.py`, `research.py`).

- **Provider routing**: `LLM_PROVIDER` env var selects `"openai"` or `"bedrock"`; per-task model
  is resolved via `LLM_MODEL_{TASK}_{PROVIDER}` settings (e.g. `LLM_MODEL_TAGGING_OPENAI`).
- **Failover**: `chat()` catches *any* exception from the primary provider and immediately
  retries once on the other provider (`llm_client.py:178-198`). No distinction between
  transient errors (rate limit, timeout — worth retrying same-provider first) and permanent
  ones (auth failure, bad request — retrying same-provider is pointless).
- **Client construction**: `OpenAI(api_key=settings.OPENAI_API_KEY)` — no `timeout` or
  `max_retries` kwarg, so the SDK default (600s timeout, 2 silent internal retries) applies
  invisibly.
- **Structured output**: `instructor.from_openai(...)` for OpenAI, manual JSON-mode + retry loop
  for Bedrock (`_bedrock_structured_chat`).
- **Tracing**: `braintrust.wrap_openai()` wraps the OpenAI client only — every OpenAI call
  produces a Braintrust span automatically, including token counts and (per Braintrust's own
  pricing registry) estimated cost. Bedrock calls are untraced (file's own docstring: "covered
  only by OTEL/Sentry task-level spans — no prompt-level detail").
- **`braintrust_span()` context manager**: exists (`llm_client.py:485-512`) to group a pipeline
  step's child LLM spans and attach `metadata`/`input`. Currently used by exactly one caller
  (`app/tasks/research.py`) — the other ten call sites invoke `llm_client.chat`/
  `structured_chat`/`embed` bare, so their Braintrust spans exist but carry no `user_id` or
  grouping metadata.
- **Cost tracking**: none end-to-end. `app/models/research.py` has a `cost` JSONB column
  (`{prompt_tokens, completion_tokens, usd}`) and `app/api/research.py` reads and returns it,
  but nothing in the codebase ever writes to it — dead schema returning `null` always.
- **Budget/rate limiting**: none at the LLM-call level. The only rate limiter in the codebase
  (`app/middleware/rate_limit.py`) gates `POST /content` requests per user, unrelated to LLM
  call frequency or spend.
- **Test coverage**: no `tests/test_llm_client.py` exists. Every consumer test mocks
  `llm_client.embed`/`chat`/`structured_chat` directly (e.g. `tests/test_tagging.py:139`), so
  `LLMClient`'s internal logic — provider dispatch, failover, span wrapping — has zero direct
  test coverage today. Any new logic added inside `LLMClient` needs its own test file; there is
  no existing pattern to extend.

## Architecture decisions

### Decision 1: Enhance `LLMClient` in place; do not adopt an external gateway product

Options considered:

1. **Adopt LiteLLM SDK** (swap internal dispatch to `litellm.completion()`/`embedding()`
   underneath the existing public interface). Pros: 100+ provider support "for free," built-in
   retry/fallback config. Cons: new dependency; loses fine-grained control over the Bedrock
   Converse-specific JSON-mode emulation already written and tuned; ADR-0003 already rejected
   this explicitly with a stated re-evaluation trigger ("3+ providers") not yet met.
2. **Point OpenAI client at a hosted gateway** (Portkey/Helicone as an OpenAI-compatible
   `base_url`) for dashboards/guardrails without writing that infra. Pros: no new Python
   dependency, fast to wire. Cons: new external SaaS account and a second place API keys/traffic
   flow through; duplicates what Braintrust already does for cost/tracing; doesn't touch
   Bedrock traffic at all (these gateways are OpenAI-API-shaped).
3. **Harden `LLMClient` in place** — add timeout, typed retry, cost attribution via existing
   Braintrust integration, per-user budget, uniform Bedrock tracing, directly in
   `app/core/llm_client.py`. Pros: no new dependency, no new external account, builds on tooling
   already paid for and 80% wired (Braintrust); matches the single-service/single-maintainer
   reality (Railway, one Python service). Cons: the ~200 lines of new logic (retry policy, price
   table, budget check) are sed.i's own code to maintain, not a vendor's.

Recommendation: **Option 3.** 2026 build-vs-buy guidance for LLM gateways puts the "buy"
threshold under ~$2K/month spend, or when the gateway logic itself is still unbuilt. Neither
applies — spend is low-volume, and routing/failover/structured-output/tracing already exist and
work. The single real gap (cost attribution) is fixable by using Braintrust correctly, not by
adding a new system.

Reversibility: Easy. Everything here is additive to `LLMClient`'s public interface
(`embed`/`chat`/`structured_chat` signatures are unchanged for all 11 call sites except the
`user_id` threading in Phase 2). If provider count grows to 3+ later (ADR-0003's own
re-evaluation trigger), swapping the internal dispatch for LiteLLM remains a one-file change.

### Decision 2: Cost data lives in Braintrust, not Postgres

Options considered:

1. **New Postgres table** (`llm_calls` ledger: user_id, task, tokens, cost, timestamp). Pros:
   queryable with the app's existing SQL tooling, no external dependency for historical
   reporting. Cons: duplicates data Braintrust already stores; another migration, another table
   to keep consistent with actual call outcomes (failed calls, retries).
2. **Braintrust as source of truth**, queried via its API when the app needs cost data (e.g. a
   future per-user spend display), with only the *real-time* budget counter (Phase 3) living in
   Redis for low-latency pre-call checks. Pros: no new schema, no drift between two cost records
   of the same event. Cons: any future UI showing "your spend this month" needs a Braintrust API
   call, not a local SQL query.

Recommendation: **Option 2.** At current call volume, a second persisted copy of cost data is
pure risk (the two sources can disagree) for a query pattern (`SELECT sum(cost) WHERE user_id=`)
that doesn't exist yet as a product requirement. Delete the dead `cost` column in
`research.py` rather than start writing to it.

Reversibility: Easy — if a product need for local cost queries emerges, add the table then,
backed by real usage data on what queries are actually needed.

### Decision 3: Typed retry is same-provider-first, not immediate failover

Options considered:

1. **Keep current behavior** — any exception triggers immediate cross-provider failover. Pros:
   zero code change. Cons: a single transient `RateLimitError` burns a full provider switch
   (different model, different pricing, different eval-validated quality) for an error a 1-second
   retry would likely resolve.
2. **Add same-provider retry for transient errors only** (`RateLimitError`, `APITimeoutError`,
   `APIConnectionError`), keep immediate failover for permanent errors (auth, bad request,
   content policy). Pros: matches the actual failure mode — transient errors don't need a
   provider switch, permanent ones are never fixed by retrying the same provider. Cons: one more
   branch in `chat()`'s exception handling.

Recommendation: **Option 2.** This is the standard pattern (per this session's research into
production LLM cost/reliability practices) — retry transient errors close to the failure,
reserve failover for errors that indicate the provider itself is the problem.

Reversibility: Easy — pure logic change inside `chat()`, no interface change.

## Dependency mapping

- **Foundation (must land first)**: Phase 0 (direct unit tests for `LLMClient`) — every
  subsequent phase adds logic to a file with zero existing direct coverage. Writing tests against
  *current* behavior first means Phases 1–4 are red-green-refactor against a real baseline,
  not "trust the mocks in consumer tests still pass."
- **Phase 1** (timeout + typed retry) is independent of Phases 2–4 — can ship first or in
  parallel with Phase 2's span work; touches only `chat()` and client construction.
- **Phase 2** (cost attribution via span metadata) introduces the price table and `user_id`
  threading that **Phase 3 depends on** (budget math reuses the same per-model price table) and
  that **Phase 4 depends on** (`braintrust_span` metadata shape must exist before Bedrock spans
  can match it).
- **Phase 3** (per-user budget) depends on Phase 2's price table — do not duplicate it.
- **Phase 4** (Bedrock tracing) depends on Phase 2's `braintrust_span` extension.
- No external dependencies (no new package, no new service, no infra change).
- No internal dependencies outside `app/core/llm_client.py`, `app/core/config.py`, and the 10
  call-site files needing `user_id` threaded through (Phase 2 only — Phases 1, 3, 4 don't touch
  call sites at all).

Suggested order: **Phase 0 → 1 → 2 → 4 → 3.**

## Phases

### Phase 0 — Direct unit tests for `LLMClient` (Foundation)

**Goal**: Establish real test coverage of `LLMClient`'s internals before adding new logic to it.
**Entry criteria**: None — this is the starting phase.

**Changes**:

1. `tests/test_llm_client.py` (new file): cover current behavior first —
   provider dispatch (`_chat_with`, `_embed_with`), the existing any-exception failover in
   `chat()`, model resolution (`_resolve_model`), and `structured_chat()`'s OpenAI/Bedrock split.
   Mock the `OpenAI` client and `boto3` client at the boundary (not `llm_client.chat` itself —
   that's what every other test file does; this file tests the thing they mock).

**Exit criteria**:

- [x] `tests/test_llm_client.py` exists and passes, covering current (pre-Phase-1) behavior.
- [x] `poetry run pytest tests/test_llm_client.py -x -q` green.

**Risks**: Low. Pure test-writing, no production code change.

**Estimated scope**: 1 new file, ~150-250 lines of tests.

---

### Phase 1 — Client timeout + typed retry-then-fallback

**Goal**: Stop a hung OpenAI call from blocking a Celery worker for up to 600s, and stop
transient errors from burning an unnecessary cross-provider failover.
**Entry criteria**: Phase 0 merged (tests exist to catch regressions in this phase).

**Changes**:

1. `app/core/llm_client.py::_make_openai_client()`: pass `timeout=30.0, max_retries=0` to
   `OpenAI(...)`. `max_retries=0` disables the SDK's silent internal retries so all retry
   behavior is explicit and visible to our own logic (per this session's research: unaccounted
   SDK auto-retries are "invisible spend").
2. `app/core/llm_client.py::chat()`: before falling back to the other provider, catch
   `openai.RateLimitError | openai.APITimeoutError | openai.APIConnectionError` specifically and
   retry the *same* provider once with a fixed 1s backoff. Any other exception (including a
   second failure of the retry) falls back to the other provider immediately, as today.

**Exit criteria**:

- [x] New test: simulated `RateLimitError` on first call → same-provider retry → success, no
      fallback triggered.
- [x] New test: simulated `AuthenticationError` → immediate fallback, no same-provider retry.
- [x] New test: simulated `RateLimitError` on both the original call and the retry → falls back
      to the other provider (existing behavior preserved as the outer safety net).
- [x] `poetry run pytest tests/test_llm_client.py -x -q` green.
- [x] `poetry run ruff check app/` clean.

**Risks**:

- **Risk**: `timeout=30.0` is too aggressive for `structured_chat` calls with larger
  `max_tokens` (e.g. `article_analysis.py`'s 1000-token structured response), causing spurious
  timeouts under normal load.
  **Likelihood**: Low. **Impact**: Medium (would show up as increased fallback-to-Bedrock rate).
  **Mitigation**: 30s is generous for sub-1000-token completions; monitor Braintrust latency
  distribution post-deploy before tightening further.
  **Detection**: Braintrust span latency histogram + fallback-rate metric (Phase 2 makes this
  visible via task-tagged spans).

**Estimated scope**: 1 file (`llm_client.py`), ~20-30 line diff.

---

### Phase 2 — Cost + usage attribution via Braintrust spans

**Goal**: Make Braintrust's already-active cost computation attributable by user and task —
close the actual gap identified in the audit, using infrastructure already paid for.
**Entry criteria**: Phase 1 merged.

**Changes**:

1. `app/core/llm_client.py::braintrust_span()`: extend signature to accept `metadata: dict |
   None = None` (currently only `name` and `input`), merged into the `start_span(...)` call.
2. `app/core/llm_client.py`: define a small per-model price table (module-level dict, four
   OpenAI models + four Bedrock models currently in use — matches published per-token rates).
   This table is introduced here because Phase 3 and Phase 4 both need it; defining it once
   avoids duplication.
3. Ten call sites — thread `user_id` through and wrap each in `braintrust_span(task_name,
   input=..., metadata={"user_id": ...})`:
   - `app/tasks/embedding.py`
   - `app/tasks/tagging.py`
   - `app/tasks/summarization.py`
   - `app/tasks/article_analysis.py`
   - `app/tasks/entity_dedup.py`
   - `app/tasks/chunk_embeddings.py`
   - `app/tasks/entity_embedding.py`
   - `app/core/request_router.py`
   - `app/api/search.py::_call_insight`
   - `app/mcp/tools/summarize.py`
   (All already have `user_id` in scope — Celery tasks load the owning user via
   `content_item_id`/`user_id` args, request-path calls have `current_user`.)
4. `app/models/research.py`: delete the dead `cost` JSONB column (migration required).
5. `app/api/research.py`: remove the `"cost": run.cost` read-site that always returns null.

**Exit criteria**:

- [x] New test: `braintrust_span` called with `metadata={"user_id": ...}` produces a span whose
      logged metadata includes that key (assert against the Braintrust test/mock double, not a
      live network call).
- [ ] Manual verification: run one full ingestion pipeline locally (save URL → extract → embed →
      tag) with `BRAINTRUST_API_KEY` set, confirm in the Braintrust "sedi" project UI that spans
      are grouped by task name and each carries `user_id` in metadata with non-null
      `estimated_cost`.
- [x] Alembic migration for the column drop, reversible (`downgrade` re-adds the nullable
      column).
- [x] `poetry run pytest tests/ -x -q` green (full suite — this phase touches 10 call sites).
- [x] `poetry run ruff check app/` clean.
- [x] ARCHITECTURE.md §9 (Celery tasks) and the memory-profile/research sections updated to
      remove the now-deleted `cost` field references.

**Risks**:

- **Risk**: Threading `user_id` through 10 call sites touches enough surface area that a
  mismatched arg order or wrong variable (e.g. passing `content_item_id` where `user_id` is
  expected) silently mislabels spans without breaking functionality — bad data, not a crash.
  **Likelihood**: Medium (10 files, repetitive but not mechanical — each has different local
  variable names). **Impact**: Low (cosmetic — wrong attribution in Braintrust, no user-facing
  break).
  **Mitigation**: Phase 0's test file makes it easy to add one assertion per call site that the
  right `user_id` value reaches the span; do this file-by-file, not as one big sweep.
  **Detection**: Spot-check 2-3 spans per call site in Braintrust UI post-deploy.

**Estimated scope**: 12 files (`llm_client.py`, `config.py` not needed here, 10 call sites,
1 model file, 1 API file, 1 migration). ~150-200 line diff total, mostly repetitive
`braintrust_span(...)` wrapping.

---

### Phase 3 — Per-user daily spend ceiling

**Goal**: Application-level abuse backstop — bound worst-case per-user spend independent of the
account-level $20/month AWS Bedrock alarm (ADR-0003), which only covers Bedrock and only alerts
monthly at the account level, not per-user in real time.
**Entry criteria**: Phase 2 merged (reuses its price table).

**Changes**:

1. `app/core/config.py`: new setting `LLM_DAILY_BUDGET_USD_PER_USER: float = 5.0` (generous
   default — an abuse backstop, not a product-tier limit).
2. `app/core/llm_client.py`: new `BudgetExceededError(Exception)`. Before each provider call in
   `chat()`/`structured_chat()`/`embed()`, check Redis key `llm_spend:{user_id}:{date}` (UTC day)
   against the ceiling; raise if exceeded. After a successful call, increment the counter using
   the Phase 2 price table (`INCRBYFLOAT`, TTL 25h so it self-expires past midnight UTC).
3. Celery task call sites: catch `BudgetExceededError` and log + skip (not `self.retry` — a
   budget-exceeded condition won't resolve by retrying, so retrying wastes a retry slot and
   delays the eventual failure signal).

**Exit criteria**:

- [x] New test: pre-seed the Redis counter above the ceiling, assert the next `chat()`/`embed()`
      call raises `BudgetExceededError` without any network call being attempted (mock the
      OpenAI/Bedrock client and assert it was never invoked).
- [x] New test: a call that pushes the running total over the ceiling still succeeds (the
      ceiling gates the *next* call, not the one that crosses it) — confirms no surprise
      mid-call rejection.
- [x] `poetry run pytest tests/test_llm_client.py -x -q` green.
- [x] `poetry run ruff check app/` clean.
- [x] ARCHITECTURE.md updated: new settings row + brief note under §9 or a new subsection.

**Risks**:

- **Risk**: Redis unavailability (already a hard dependency for Celery broker, so a Redis outage
  already stops the pipeline elsewhere) — the budget check adds one more Redis round-trip per
  LLM call, increasing latency slightly.
  **Likelihood**: Low. **Impact**: Low (Redis is already load-bearing infra; this doesn't add a
  new failure mode, just a new read on the same dependency).
  **Mitigation**: None needed — if Redis is down, the pipeline already can't run.
  **Detection**: N/A — covered by existing Redis/Celery health monitoring.
- **Risk**: Default $5/day/user ceiling is wrong (too low, causing false-positive skips for a
  legitimately heavy user like the research pipeline's multi-step LLM calls).
  **Likelihood**: Medium — `research.py`'s planning/expansion/synthesis steps are multiple
  `gpt-4o` calls per run, plausible to approach $5 on a busy day.
  **Impact**: Medium (a skipped call degrades a feature silently rather than erroring loudly —
  needs a visible signal, not just a log line).
  **Mitigation**: Log at `WARNING` (not `INFO`) on skip so it's visible in existing log
  aggregation; revisit the default after a week of real Braintrust cost data from Phase 2.
  **Detection**: Braintrust cost-by-user rollup (now possible post-Phase-2) shows daily spend
  distribution — set the default from real data, not a guess, before this ships to production.

**Estimated scope**: 2 files (`config.py`, `llm_client.py`), ~40-60 line diff, plus small
edits at Celery call sites to catch the new exception type (10 files, ~2-3 lines each).

---

### Phase 4 — Uniform Bedrock tracing

**Goal**: Close the Bedrock blind spot — currently invisible in Braintrust, meaning cost
attribution (Phase 2) and budget enforcement (Phase 3) have no observability if
`LLM_PROVIDER=bedrock` is ever enabled in production (ADR-0003's stated migration trigger).
**Entry criteria**: Phase 2 merged (needs its `braintrust_span` metadata shape and price table).

**Changes**:

1. `app/core/llm_client.py::_bedrock_chat`, `_bedrock_structured_chat`, `_bedrock_embed`: since
   `wrap_openai` can't instrument boto3, manually open a `braintrust_span` around each call body
   and `span.log(metrics={"prompt_tokens": ..., "completion_tokens": ..., "estimated_cost":
   ...})` using the Phase 2 price table for the Bedrock model in use.

**Exit criteria**:

- [x] New test: a Bedrock chat call (mocked boto3 client) produces a span with the same metadata
      shape (`task`, `user_id`, token counts, `estimated_cost`) as the OpenAI path.
- [ ] Manual verification: set `LLM_PROVIDER=bedrock` locally, run one tagging call, confirm the
      Braintrust "sedi" project shows a span with non-null cost — matching today's OpenAI-path
      behavior.
- [x] `poetry run pytest tests/test_llm_client.py -x -q` green.
- [x] `poetry run ruff check app/` clean.
- [x] `llm_client.py`'s module docstring updated — the line "Bedrock calls are covered only by
      OTEL/Sentry task-level spans — no prompt-level detail" is no longer accurate after this
      phase and must be corrected.

**Risks**:

- **Risk**: Bedrock's `Converse` response shape for usage (`inputTokens`/`outputTokens`) differs
  slightly across model families (Nova vs. Claude via inference profile); the price table needs
  per-model entries, not a single Bedrock-wide rate.
  **Likelihood**: Low — already handled correctly today for the token-count return value itself
  (`ChatResult`); this phase only adds cost math on top of numbers already being extracted
  correctly.
  **Impact**: Low (wrong cost estimate, not a functional break).
  **Mitigation**: Price table keyed by exact model ID string (already the pattern from Phase 2),
  not a flat per-provider rate.
  **Detection**: Spot-check Braintrust spans against AWS Cost Explorer's actual Bedrock line item
  after a week of real traffic, if/when `LLM_PROVIDER=bedrock` is enabled in production.

**Estimated scope**: 1 file (`llm_client.py`), ~40-60 line diff (three methods touched).

---

## Risks (plan-level, not phase-specific)

**Risk**: The four-model, eight-entry price table (Phase 2) drifts from actual provider pricing
if OpenAI/AWS change rates and nobody updates the hardcoded table.
**Likelihood**: Medium over a long time horizon (pricing changes happen a few times a year).
**Impact**: Low (cost figures become slightly wrong, not functionally broken — Braintrust's own
built-in pricing registry, used for the OpenAI path's automatic cost computation, is unaffected
by this table; only the Bedrock manual logging and the Redis budget math depend on it).
**Mitigation**: Comment the price table with the pricing-page URL and a "last verified" date;
no automated staleness check is justified at this call volume.
**Detection**: None automated — accepted as a manual-maintenance item, consistent with how the
four Bedrock model IDs in `infra/__main__.py`'s IAM policy are already hand-maintained.

**Risk**: Scope creep — "harden the gateway" is an easy place to keep adding features
(semantic caching, streaming support, a UI for spend data) beyond what was actually asked for.
**Likelihood**: Medium. **Impact**: Medium (delays shipping the actual gap-closing work).
**Mitigation**: The Non-goals section above is the explicit boundary; anything not listed in
Phases 0-4 is out of scope for this plan and needs its own plan doc if it comes up later.
**Detection**: Any PR touching files outside the phase's stated "Changes" list should prompt a
scope check against this doc.

## Verification

### Automated

- `poetry run pytest tests/ -x -q` (full backend suite) must stay green after every phase —
  Phase 2 in particular touches 10 existing call sites and must not change their functional
  behavior, only their tracing/attribution.
- `poetry run ruff check app/` clean after every phase.
- New tests per phase as listed in each phase's exit criteria — all live in
  `tests/test_llm_client.py` except Phase 2's call-site changes, which should get one assertion
  added to each call site's *existing* test file (e.g. `tests/test_tagging.py` gets an
  assertion that `braintrust_span` was called with the right `user_id`), not a new file.
- No frontend changes in this plan — `npx tsc`/`eslint`/`jest`/`next build` are unaffected and
  don't need to be run for this work.

### Manual

- Phase 2: run one real ingestion pipeline locally with `BRAINTRUST_API_KEY` set, visually
  confirm span grouping and metadata in the Braintrust UI.
- Phase 4: repeat the same manual check with `LLM_PROVIDER=bedrock` set locally.
- Phase 3: after Phase 2 has been live for a representative period (a few days of real traffic),
  check Braintrust's per-user cost rollup before finalizing the $5/day default — adjust from
  real data rather than shipping the placeholder value permanently.

### Cross-cutting

- This plan touches zero frontend files, zero database tables used by user-facing features
  (only the dead `research.cost` column, which nothing displays), and zero API response shapes
  except removing the always-null `cost` field from `GET /research/{run_id}`-equivalent
  responses (confirm no frontend code reads `run.cost` before deleting — grep
  `frontend/` for `\.cost` in research-related components as part of Phase 2).
- Smoke-test after Phase 2: the research pipeline end-to-end (`app/tasks/research.py`), since
  it's the one existing `braintrust_span` consumer and its call sites are being touched
  indirectly via the shared `braintrust_span()` signature change.

## Open questions

1. **Price table accuracy**: should the four-model price table (Phase 2) be verified against
   current OpenAI/Bedrock pricing pages before merging, or is a rough estimate acceptable for
   v1 given Braintrust's own registry already handles the OpenAI-path cost computation
   authoritatively? (This table is only load-bearing for the Bedrock manual logging and the
   Redis budget math — not for the OpenAI cost figures shown in Braintrust today.)
2. **$5/day/user default**: acceptable as a placeholder pending real Braintrust cost data
   (per Phase 3's risk mitigation), or does the user want a lower/higher starting value given
   sed.i's current user count and usage pattern?
3. **Migration timing for the `research.cost` column drop** (Phase 2): bundle with the rest of
   Phase 2, or split into its own trivial PR since it's unrelated cleanup discovered during this
   plan rather than something this plan's goal requires removing?
