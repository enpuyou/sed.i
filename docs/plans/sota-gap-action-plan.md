---
type: plan
status: active
last_updated: 2026-07-26
consumer: human
---

# sed.i SOTA Gap Action Plan

Every actionable item from the two SOTA review passes
([sota-industry-comparison.md](../design/systems/sota-industry-comparison.md),
[sota-stack-and-workflow-review.md](../design/systems/sota-stack-and-workflow-review.md)),
consolidated into one prioritized list. Where an item is already tracked
in [sota-layer-plan.md](sota-layer-plan.md), this doc points there instead
of duplicating the task breakdown — this doc's job is prioritization and
completeness across *both* reviews, not a third task tracker.

Not everything below should be done. A few items are explicitly framed as
product-strategy calls, not engineering debt — flagged as such.

---

## How to read the priority tiers

- **P0** — small effort, real risk or real leverage, no reason to defer.
- **P1** — real effort, clear payoff, do next after P0 is clear.
- **P2** — legitimate but larger bets; needs a product/scope decision first.
- **Not recommended** — named in a review as a tempting-sounding option that the review itself concluded against.

---

## P0 — do these next, low effort / high leverage or risk

### 1. Build the ANN index the codebase already decided to build

**Gap**: ADR-0001 says "pgvector with HNSW indexing." In reality only
`entities.embedding` has an ANN index (HNSW). `content_items.embedding`,
`highlights.embedding`, and `content_chunks.embedding` — the three
columns hit by every user-facing semantic search — have **no index**,
just exact `<=>` full-table-scan cosine distance. There's already an eval
(`evals/hnsw-index/runner.py`) benchmarking seq-scan vs. HNSW at
N=1K/5K/10K/50K that was never applied to these tables.

**Why P0**: this isn't a new design decision — it's finishing one already
made and justified. Invisible today at low row counts, becomes a
user-visible latency cliff with no warning as libraries grow. One
migration, `CREATE INDEX CONCURRENTLY` per the pattern `post_deploy.py`
already uses for the entities table.

**Action**: add HNSW indexes on `content_chunks.embedding`,
`content_items.embedding`, `highlights.embedding`, built concurrently in
`post_deploy.py` alongside the existing entities index. Re-run
`evals/hnsw-index/` against real data post-index to confirm the latency
claim ADR-0001 makes is actually true.

**Source**: [sota-stack-and-workflow-review.md §1](../design/systems/sota-stack-and-workflow-review.md)

---

### 2. Redis-backed HTTP rate limiter, and widen its scope

**Gap**: `app/middleware/rate_limit.py` is in-memory (`defaultdict`/`deque`,
process-local) and only covers `POST /content`. Doesn't work across
multiple backend instances (a documented, self-acknowledged gap). Doesn't
cover `/auth/login` (brute-force surface) or `/search/semantic` (the most
expensive read path — fans out to 3-4 retrieval lanes plus an LLM insight
call).

**Why P0**: Redis is already a hard dependency (Celery broker). This is
an `INCR`+`EXPIRE` pattern, not new infrastructure. LLM spend already has
this exact protection (`llm-gateway-hardening` Phase 3, complete) — the
HTTP layer is the one surface still unprotected, and it's the cheaper fix
of the two.

**Action**: move `RateLimitMiddleware` to Redis-backed counters; extend
coverage to `/auth/login`, `/auth/register`, `/search/semantic` (or the
underlying `mode="full"` path), and MCP endpoints.

**Source**: [sota-industry-comparison.md Gap 4a](../design/systems/sota-industry-comparison.md)

---

### 3. Wire up the frontend refresh-token flow that already exists in the backend

**Gap**: `app/models/refresh_token.py`, `POST /auth/refresh`, hashed
token storage, and expiry logic are fully implemented server-side.
`frontend/lib/api.ts` and `AuthContext.tsx` have zero references to
refresh — the frontend still stores one long-lived JWT in `localStorage`
and never calls the refresh endpoint.

**Why P0**: the backend already did the hard half of this security fix.
Shipping the frontend half doesn't require a new design, just wiring —
and until it's wired, the schema *suggesting* the security improvement is
misleading relative to what actually protects a stolen token today.

**Action**: `fetchWithAuth()` calls `/auth/refresh` on 401 before
redirecting to `/login`; store the refresh token per the existing backend
contract.

**Source**: [sota-industry-comparison.md §7.2](../design/systems/sota-industry-comparison.md)

---

### 4. Fix ARCHITECTURE.md's stale/incorrect claims

**Gap**: multiple factual errors found during this review pass:
- Says "Next.js 14" — actual `package.json` says 16.1.1.
- Says HNSW indexing is in place — only true for `entities`, not the
  three tables that matter (see item 1).
- Says the eval regression CI gate is "not yet implemented" — it's fully
  implemented and hard-fails PRs (`evals/check_regressions.py` +
  `.github/workflows/evals-ci.yml`).
- Internally inconsistent rate-limit numbers: §7 says "20 req/min," §8
  and §15 say "10 req/60s + 50 req/3600s" (the code-accurate ones).

**Why P0**: CLAUDE.md already mandates ARCHITECTURE.md be updated in the
same commit as any feature change — these are drift that accumulated
despite that rule, worth a dedicated correction pass rather than waiting
for the next feature to touch each section.

**Source**: both review docs, "notable discrepancies" sections.

---

## P1 — real effort, clear payoff

### 5. Add a reranker to hybrid search — ✅ tried, removed (2026-09-25)

**Gap**: `hybrid_search.py` fuses keyword + semantic + entity lanes via
RRF but has no second-pass reranking (no cross-encoder, no Cohere
Rerank, no LLM-as-judge over the final candidate set). 2026 production
guidance is unusually consistent that this is the single largest
precision lever available in a retrieval pipeline (cited lift: up to
48%, recall@10 78%→91% in one benchmark).

**What happened**: built and evaluated Cohere Rerank v3.5 on the
`mode="auto"` hybrid lane (`evals/reranker/`, since deleted). The eval's
own Phase 9 decision was **"investigate, not ship as-is"** — a guard-rail
case regressed (`teen_culture_identity` 1.00→0.75, a case explicitly
named in advance as must-not-regress), and wins/losses showed a
consistent tier pattern (helps concrete/entity-bridge queries, hurts
abstract concept-bridge queries) that roughly cancels in aggregate
(+3.15pp R@10 overall, but barely beats the simplest baseline variant).
It shipped anyway, with no query-shape gating and no budget/cost
enforcement despite being the only third-party paid API in the search
path.

**Decision**: removed entirely (`app/core/reranker.py`,
`evals/reranker/`, the `cohere` dependency). Two factors: the eval never
recommended shipping as-is, and the product goal shifted to supporting
more users under a hard cost constraint — a paid per-search API call
with a self-cancelling quality result doesn't clear that bar. If
reranking is revisited, the eval's own proposed fix (route reranking
only to non-abstract queries, using `search_router.py`'s classifier) is
the starting point, not re-shipping the ungated version.

**Source**: [sota-stack-and-workflow-review.md §2, §6](../design/systems/sota-stack-and-workflow-review.md); [sota-layer-plan.md Layer 5](sota-layer-plan.md)

---

### 6. Build Temporal for the research pipeline's durable execution — or consciously re-scope the ADR

**Gap**: ADR-0005 planned a three-tier orchestration stack (Celery /
Prefect / Temporal) and named Temporal as required for the research
agent's durability. It was never built. Today, a worker crash mid-run
doesn't resume from its last step — a 5-minute-poll beat task
(`recover_orphaned_runs_task`) marks the run `partial` after 10 minutes
of staleness, discarding real, already-paid-for LLM work.

**Already tracked**: this is **Layer 7** in
[sota-layer-plan.md](sota-layer-plan.md) — explicitly called "the biggest
learning investment in the plan," budgeted 2-3x a normal layer, split
into two PRs (Temporal setup + planner, then executor + synthesis + UI).

**Why P1 not P0**: this is real effort (3 extra services: Temporal,
Temporal UI, Elasticsearch), not a quick fix. But it's not optional
forever — ADR-0005 already named the exact failure mode this leaves
open, and every research-pipeline-shaped feature added before this is
built increases exposure to it.

**Action**: execute Layer 7 as scoped, or make an explicit, written
decision that the current Celery-plus-recovery-polling behavior is an
acceptable permanent state (not an interim one) given current run
volume — that's a legitimate call, but it should be a decision, not
default drift.

**Note on LangGraph**: a separate question that came up during review —
LangGraph is not a substitute for this fix. It would replace the
Celery `group`/`chord` coordination layer, not solve durability any more
directly than Temporal does, and would introduce a second orchestration
paradigm alongside the Celery ingestion pipeline that already exists.
Temporal is the fix already reasoned through in ADR-0005; don't
re-litigate the framework choice as part of closing this gap.

**Source**: [sota-industry-comparison.md §8.4](../design/systems/sota-industry-comparison.md); [sota-stack-and-workflow-review.md §4](../design/systems/sota-stack-and-workflow-review.md); [sota-layer-plan.md Layer 7](sota-layer-plan.md)

---

### 7. XSS hardening on extracted third-party HTML

**Gap**: extracted article HTML is rendered directly in the reader.
Current mitigation is a partial extraction-time filter (strips
`iframe/frame/object/embed/script`) in the extension content script —
not DOMPurify-grade sanitization (doesn't cover event-handler
attributes, `javascript:` URLs, `srcdoc`, SVG vectors). This applies at
two separate points in the pipeline (extension ingestion, backend
trafilatura/PDF extraction) with no single central sanitization pass.

**Why P1**: higher-stakes than typical user-generated-content XSS
because the HTML source is *arbitrary third-party pages the user doesn't
control*, not the user's own input. Not urgent-today (no known
exploit), but the fix is well-understood and the current mitigation is
provably incomplete.

**Action**: DOMPurify pass centralized at storage or render time (not
per-ingestion-path) so it's fixed once for both the extension and
backend extraction paths, per the earlier review's explicit
recommendation against patching each path separately.

**Source**: [sota-industry-comparison.md Gap 4c, §7.7](../design/systems/sota-industry-comparison.md)

---

### 8. httpOnly cookie migration for the JWT

**Gap**: token lives in `localStorage`, XSS-accessible. 2026 baseline
for consumer web auth is httpOnly cookies + CSRF token — and this
matters more for sed.i than a typical CRUD app because the app also
renders scraped third-party HTML (item 7), which raises the XSS attack
surface above baseline.

**Why P1 not P0**: real effort — touches every API call site in the
frontend (`fetchWithAuth` and its ~dozens of call sites), not a
contained change like items 1-3. Do after item 3 (refresh-token wiring)
lands, since that's a prerequisite piece of the same session-security
story and is far cheaper to ship first.

**Action**: migrate token storage to httpOnly cookie + CSRF token;
update `fetchWithAuth()` and all API call sites.

**Source**: [sota-industry-comparison.md Gap 4b](../design/systems/sota-industry-comparison.md)

---

### 9. Recommendation endpoint: replace the O(N) Python similarity loop with pgvector

**Gap**: `GET /content/recommended` loads every unread item, then for
each one loops over up to 7 days of recent reads computing cosine
similarity by hand in pure Python (`sum(x*y for x,y in zip(a,b))`, no
numpy) — not the `<=>` pgvector operator already used correctly
elsewhere in the same codebase for search.

**Why P1**: invisible today at low unread-item counts; becomes a
multi-second, single-threaded, connection-pool-holding request the
moment a power user's queue crosses a few thousand unread items. Fix is
mechanical — swap the Python loop for the same SQL pattern
`_semantic_search` already uses.

**Action**: rewrite the endpoint's scoring to a pgvector query, benefit
directly from item 1's new indexes once built.

**Source**: [sota-industry-comparison.md §7.1](../design/systems/sota-industry-comparison.md)

---

### 10. Connection pool sizing + load testing

**Gap**: `pool_size=3, max_overflow=2` (5 max connections/process) was
almost certainly tuned empirically against a one-person dev workload.
Zero load-testing artifacts exist anywhere in the repo — no answer to
"what happens at 10x concurrent users" for any subsystem (Celery queue
depth under burst, pool exhaustion, HNSW index build under write
pressure).

**Why P1**: not urgent at current traffic, but this is a prerequisite
for the deployment-topology fix in item 12 (can't safely run
`RAILWAY_REPLICAS>1` without knowing the real pool/load ceiling first).

**Action**: basic load test (k6 or locust) against the search and
content-save paths; use results to size the pool properly and get a
first real answer to "what breaks at 10x."

**Source**: [sota-industry-comparison.md §6.2, §7.1](../design/systems/sota-industry-comparison.md)

---

### 11. Beat-task overlap/backpressure story

**Gap**: `cluster_all_users_task`, `consolidate_all_users_task`,
`embed_new_entities_beat_task`, `backfill_missing_entities_task` all
fan out per-user Celery tasks from a single beat trigger with no
documented behavior if one run is still draining when the next fires.
No queue-depth or worker-saturation alerting anywhere in the
observability stack.

**Action**: add queue-depth/worker-saturation metrics to the existing
Grafana dashboard (ADR-0002's stack already covers traces/errors, not
queue health); document or enforce non-overlap for the fan-out beat
tasks.

**Source**: [sota-industry-comparison.md §7.4](../design/systems/sota-industry-comparison.md)

---

### 12. Decouple migrations from app boot; fix the runtime opencv dependency swap

**Gap**: `Procfile` runs `alembic upgrade heads` in the same process
that then serves traffic, and separately uninstalls/reinstalls
opencv-python↔opencv-python-headless on every boot (a workaround for a
transitive YOLO dependency, not pinned at the lockfile level). Both
couple deploy time to fragile, avoidable extra work, and both block
safely setting `RAILWAY_REPLICAS>1`.

**Why P1**: only actually blocking once horizontal scaling is needed —
sequence after item 10 confirms whether that's imminent.

**Action**: separate migration step from the web service's start
command; pin the transitive opencv dependency (or exclude GUI extras)
at the Poetry lockfile level instead of the runtime pip-swap.

**Source**: [sota-industry-comparison.md §7.6](../design/systems/sota-industry-comparison.md)

---

### 13. Pick one rule for where domain logic lives in the API layer

**Gap**: `app/services/content.py` is the only service-layer file in the
backend; every other router (`search.py` at 870 lines, `auth.py`,
`vinyl.py`, `lists.py`, `memory.py`) writes business logic directly in
route handlers. Two competing, undeclared answers to "where does new
logic go" — the next router added inherits whichever pattern its
neighbor happens to use, not a stated rule.

**Why P1**: cheap to decide, compounds if left undecided as the API
surface grows. Not a rewrite — either apply `services/` above a stated
complexity threshold, or formally scope it back to "content ingestion
only" (both defensible; only the lack of a decision is the problem).

**Action**: pick one, write it down (a short ADR or a CLAUDE.md rule is
enough), apply it going forward — no need to retrofit existing routers
unless touched anyway.

**Source**: [sota-industry-comparison.md §8.3, §8.6](../design/systems/sota-industry-comparison.md)

---

### 14. Automated guard against the "half our LLM calls have no cost attribution" class of gap — ✅ done (2026-07-26)

**Gap**: the `llm-gateway-hardening` work (complete) was believed to have
fixed the actual problem — Braintrust `user_id`/metadata covering the
known call sites plus Bedrock. A fresh audit before building the guard
found the real state had drifted further than expected: **13 of 33 real
call sites** (not 10 — the earlier count undercounted `research.py`,
`research_memory.py`, `memory.py`, `embedding_cache.py`, and two MCP
tool files) were either completely unwrapped or wrapped without `user_id`
in metadata. Since `user_id` also gates the per-user daily LLM budget
check (`check_budget` in `llm_client.py`), this meant the entire research
pipeline — the most expensive, most iteration-heavy LLM workflow in the
codebase — had **zero per-user budget enforcement**, not just missing
cost-attribution tracing.

**Action taken**: fixed all 13 gaps first (research.py ×7 call sites
including one `call_embed` site the initial grep missed, research_memory.py
×2, memory.py ×1, embedding_cache.py + hybrid_search.py's 4 downstream
call sites via a threaded `user_id` parameter, mcp/tools/query.py ×2,
mcp/tools/synthesis.py ×2), re-audited to confirm 33/33 call sites clean,
then added `tests/test_llm_call_site_guard.py` — an AST-based static
analysis test that walks every file under `app/`, finds every
`llm_client.chat`/`structured_chat`/`embed` call, and fails if it isn't
wrapped in `braintrust_span(..., metadata={"user_id": ...})` with
`user_id=` also passed to the call itself. Verified the guard actually
catches the regression shape it's meant to (tested against a synthetic
unwrapped call site) plus 8 unit tests of the guard logic itself.

**Source**: [sota-industry-comparison.md §6.2 (updated)](../design/systems/sota-industry-comparison.md)

---

## P2 — larger bets, needs a product decision first

### 15. In-app chat/synthesis surface

**Gap**: every 2026 SOTA competitor (Readwise Ghostreader, NotebookLM,
Mem.ai) exposes a conversational, document-aware interface as the
*primary* interaction. sed.i has the retrieval infrastructure (hybrid
search, entity graph, chunk embeddings, the full research pipeline) but
exposes it only to external MCP clients (Claude Desktop etc.), not to
sed.i's own web/extension UI. A user cannot ask "what are the competing
views I've saved on X" inside the app itself.

**Why this is the single largest gap named across both reviews**: sed.i
built the hard backend half of an agentic RAG product without building
the interaction surface that would let a user feel it. Everything else
in this doc is plumbing without this.

**Why P2 not P1**: genuinely high effort (new UI + streaming + wiring to
the existing research/synthesis backend) and is a product-scope decision
(what's the chat surface *for* — full research runs, quick Q&A, both?),
not a pure engineering task.

**Source**: [sota-industry-comparison.md Gap 1, §5](../design/systems/sota-industry-comparison.md)

---

### 16. User-visible, editable memory

**Gap**: `user_profiles.memory_text` is LLM-written, nightly-consolidated,
free-form prose — architecturally matching Anthropic's own Claude Memory
design (a real strength, not a gap). But there's no `PATCH` endpoint and
no UI surface — a user has no way to see how sed.i has profiled them or
correct it if wrong. Claude Memory's defining competitive differentiator
specifically *is* user-editability.

**Why P2 not P1**: the data model already supports this
(`memory_text` is a plain text column) — this is genuinely low technical
effort. Classified P2 here only because it's most valuable paired with
item 15 (a settings/chat surface) rather than shipped as an isolated
screen; worth reconsidering as P1 if item 15 isn't happening soon.

**Action**: `PATCH /memory/profile` endpoint + a simple settings page
showing current `memory_text` with edit/save.

**Source**: [sota-industry-comparison.md Gap 2](../design/systems/sota-industry-comparison.md)

---

### 17. Flip Prefect on by default in production

**Gap**: `PREFECT_ENABLED=false` by default — the ingestion pipeline's
5+ stage DAG has no flow-level visibility (no per-run success/failure
dashboard) in the deployment actually serving traffic, only in the one
nobody has turned on. Requires two additional Railway services.

**Why P2**: real recurring infra cost (2 more services running
continuously), not just an engineering decision — worth sizing the
actual monthly cost before flipping the default.

**Action**: size the two-Railway-service cost, decide if it's worth
running continuously vs. only during active debugging.

**Source**: [sota-industry-comparison.md Gap 6](../design/systems/sota-industry-comparison.md)

---

## Not recommended — considered and rejected by the reviews themselves

**Databricks / data-platform-style vector search.** No 2026 source treats
this as a peer option to pgvector at sed.i's scale — it's an
organizational tool (lakehouse governance, multi-team data platforms),
not a better retrieval engine. Would repeat, at a much heavier weight,
the exact over-engineering ADR-0001 already reasoned past when rejecting
a dedicated vector DB.

**Migrating off pgvector to a dedicated vector DB (Qdrant/Pinecone/etc.)
today.** ADR-0001's migration triggers (5M+ vectors, 200ms+ p95, >100
writes/sec) aren't close to being met. Revisit only when one of those
triggers actually fires — not preemptively.

**Adopting LangGraph (or CrewAI/AutoGen) to replace the hand-rolled
research pipeline.** See item 6's note — this doesn't solve the
durability gap that's actually open (Temporal already does, on paper),
and would add a second orchestration paradigm alongside the
Celery-based ingestion pipeline that already exists and works. The
framework question and the durability question look similar from
outside the codebase but aren't the same question.

**Full multimodal ingestion + audio synthesis (NotebookLM parity).**
Named explicitly in the industry-comparison doc as a legitimate
product-strategy call, not an oversight — sed.i's identity is a reading
app; audio-first synthesis is a different product thesis. Not scored
into the tiers above because it isn't a gap to close, it's a strategic
fork to decide on deliberately if ever revisited.

**Full Microsoft GraphRAG (Leiden clustering + community reports).**
Already correctly rejected in `graphrag-multiagent-research.md` as sized
for 100k+ document corpora that doesn't amortize at sed.i's scale. The
existing entity-graph implementation is the right-sized subset — don't
chase the SOTA paper.

---

## Summary table

| # | Item | Tier | Effort | Already tracked elsewhere? |
|---|---|---|---|---|
| 1 | HNSW index on the 3 unindexed embedding columns | P0 | Low | No — net-new finding |
| 2 | Redis rate limiter, widen scope | P0 | Low | No |
| 3 | Wire up frontend refresh-token flow | P0 | Low-Med | No |
| 4 | Fix ARCHITECTURE.md drift | P0 | Low | No |
| 5 | Reranker | P1 | Medium | Yes — Layer 5 |
| 6 | Temporal durable execution | P1 | High | Yes — Layer 7 |
| 7 | Centralized XSS sanitization | P1 | Low-Med | No |
| 8 | httpOnly cookie + CSRF migration | P1 | Medium | No |
| 9 | Recommendation endpoint → pgvector | P1 | Low | No |
| 10 | Load testing + pool sizing | P1 | Medium | No |
| 11 | Beat-task overlap/backpressure guards | P1 | Low-Med | No |
| 12 | Decouple migrations from boot; fix opencv swap | P1 | Medium | No |
| 13 | Service-layer rule decision | P1 | Low (decision) | No |
| 14 | Automated Braintrust-wrapping guard | P1 | Low | ✅ Done |
| 15 | In-app chat/synthesis surface | P2 | High | No — product decision needed |
| 16 | User-editable memory UI | P2 | Low | No — sequencing decision |
| 17 | Prefect on by default | P2 | Low (decision) + recurring cost | No |
