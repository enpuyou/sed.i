---
type: research
status: active
last_updated: 2026-07-21
consumer: human
---

# sed.i vs. Industry SOTA — Architecture & Product Gap Analysis

Compares sed.i's current implementation against real-world 2026 AI product
architecture: read-it-later/PKM competitors, memory systems, agentic
retrieval, LLM observability, and MCP ecosystem practice. Scope is
architecture and product depth, not business metrics.

Four passes, four different questions:

- **§1–5**: feature/product-surface comparison — what capabilities exist.
- **§6**: engineering-quality of the LLM/agentic subsystem specifically —
  how the AI-facing code is built, at what rigor.
- **§7**: whole-product system design — data layer, API layer, background
  processing, frontend, auth, deployment topology, and the extension's
  trust boundary, read for correctness and operational readiness.
- **§8**: architecture proper — the shape of the system. Not "is the code
  correct" but "are the boundaries in the right places, is coupling going
  the right direction, would this system's structure survive the product
  doubling in scope." This is the question a staff-level architecture
  review actually spends most of its time on, and it's distinct from §6/§7.

---

## 1. Where sed.i already matches or exceeds SOTA

These are not aspirational — they are in production today (see
`ARCHITECTURE.md`, `docs/design/systems/agentic-features.md`).

| Capability | sed.i implementation | Industry comparison |
|---|---|---|
| Human-readable, editable memory | `user_profiles.memory_text` — free-form prose, LLM-managed, nightly consolidation | Matches Anthropic's stated design principle for Claude Memory (markdown, not opaque vectors) over OpenAI/Google's vector-backed approach |
| Multi-lane hybrid retrieval | tsvector keyword + pgvector semantic + entity graph, fused with 3-way RRF | Matches "Adaptive RAG" 2026 guidance — route cheap queries to fast paths, escalate only when needed (`search_router.py` query classifier does exactly this) |
| Multi-agent orchestration with budget control | Lead/subagent/collector/synthesizer/verifier via Celery chord, token + iteration + timeout budget, resume support | Matches the "Plan-and-execute" + "Multi-agent retrieval" patterns identified as 2026 production standard; budget ceiling is more disciplined than most public references, which usually skip cost bounding |
| Citation grounding + hallucination mitigation | `verify_synthesis_task` strips uncited claims from `ResearchBrief` | Matches the single most-cited 2026 mitigation ("attribute every claim to a specific chunk by identifier") |
| Cross-run persistent memory for agents | `research_memory` table — pgvector similarity over past sub-questions, injected into planner context | This is ahead of most public agentic-RAG writeups, which describe single-session memory only. Concrete prior-art match: SaaS memory layers (Mem0, Zep) that decouple memory from the LLM — sed.i's version is in-DB, not a separate service, but the retrieval pattern (embed → cosine search → inject as context) is the same shape |
| LLM gateway with retry, fallback, cost attribution, budget enforcement | `LLMClient` — typed retry, provider fallback, Braintrust cost-by-user spans, Redis-backed per-user daily spend ceiling | Matches 2026 LLM gateway guidance almost exactly ("gateway sits on every request... already has model, tokens, latency, cost") — this is usually bought (Portkey, Helicone, TrueFoundry), sed.i built it in-house per ADR-0003 |
| Eval-driven development | `evals/retrieval` (R@10/MRR/NDCG, 4 variants), `evals/memory-consolidation-prompt` (A/B/C prompt comparison), `evals/research-brief` | Matches "2026 standard is eval-driven, not vibes-driven" — most consumer apps at sed.i's scale do not have this |
| Entity graph as a retrieval lane | `entities`/`entity_relations` tables, HNSW ANN dedup, IDF-dampened scoring blended into RRF | A working, evaluated subset of Microsoft GraphRAG's local-search pattern — appropriately scoped down from the full community-detection/global-search machinery, which the existing `graphrag-multiagent-research.md` correctly identifies as overkill at single-user scale |
| MCP server with OAuth + skills + text-to-SQL | Full OAuth/PKCE flow, `sqlglot`-validated read-only SQL generation with two-tier user-isolation enforcement | Ahead of the median MCP server in the ecosystem — most public servers (of ~22K) are single-tool, unauthenticated, local-only. sed.i's `_enforce_user_isolation` is more rigorous than the "start read-only" baseline most 2026 guidance treats as sufficient |

**Read this section literally**: on agentic architecture and LLMOps discipline, sed.i is not behind a typical funded AI startup at this stage — it's ahead of most self-reported implementations found in the current research pass. The gap is concentrated elsewhere.

---

## 2. Where the gap is real

### Gap 1 — Product surface: "chat with your library" is missing

Every 2026 SOTA competitor exposes a **conversational, document-aware interface** as the primary interaction:

- **Readwise Reader / Ghostreader**: unified chat interface, document-aware, cites back to exact highlighted text, reduces manual highlighting ~60% via auto-suggestion.
- **NotebookLM**: grounds every response in uploaded sources with inline citation; "Audio Overview" turns a document set into a synthesized podcast (4 formats: Deep Dive, Brief, Critique, Debate).
- **Mem.ai**: AI-first capture — retrieval and classification happen automatically, no manual filing.

sed.i has the retrieval infrastructure to support this (hybrid search, entity graph, chunk-level embeddings, research pipeline) but **no persistent chat surface**. The MCP `query_library`/synthesis tools expose this capability only to external MCP clients (Claude Desktop, etc.), not to sed.i's own web/extension UI. A user cannot ask "what are the competing views I've saved on AI alignment?" inside the app itself — this exact gap is already named in `graphrag-multiagent-research.md` §1 Break 4, but the fix (an in-app chat surface backed by the existing research/synthesis pipeline) is not yet product-scoped.

**This is the single largest gap.** sed.i has built the hard backend half of an agentic RAG product (retrieval, memory, verification, cost control) without building the interaction surface that would let a user actually feel that.

### Gap 2 — Memory is read-only from the user's perspective

Claude Memory's defining 2026 design choice — the one competitors are explicitly compared against — is that memory is **user-editable**: open it, read it, correct it, delete a line. sed.i's `user_profiles.memory_text` is LLM-written and consolidated nightly, but:

- No `PATCH`/edit endpoint for `memory_text` — only `GET /memory/profile` and `POST /memory/consolidate` (trigger, not edit).
- No UI surface showing the memory profile to the user at all.

A user has no way to know sed.i has profiled them as "fast reader, browsing pattern, currently on LLM alignment" or to correct it if wrong. This is a trust/transparency gap, not a technical one — the data model already supports it (`memory_text` is a plain text column); what's missing is exposing it.

### Gap 3 — No multimodal ingestion or synthesis

NotebookLM's 2026 differentiator is multimodal fusion: PDF + YouTube transcript + voice memo → one synthesized narrative, plus audio-native output (podcast-style Audio Overview). sed.i:

- Ingests article/PDF/tweet/video *links* but extracts only text (trafilatura, YOLO for PDF layout). No audio/video transcript extraction.
- Has zero audio output. The vinyl/Crates YouTube player is unrelated (external music playback, not content synthesis).

This is a legitimate scope question, not an oversight — sed.i is a reading app, not a multimodal notebook. Flagging it because "industry SOTA" explicitly includes this vector and a gap analysis should say so plainly. Whether to close it is a product-strategy call, not an architecture one.

### Gap 4 — Rate limiting and auth are below the security bar competitors clear by default

`ARCHITECTURE.md` §8, §15 self-documents these as known gaps, but worth stating in industry-comparison terms:

- **In-memory rate limiting** — resets on restart, no cross-instance enforcement. Any product running >1 backend instance (which is the default on Railway/Vercel-style deploys at any real scale) has a rate-limit bypass today.
- **localStorage JWT storage** — XSS-accessible. 2026 baseline for consumer web auth is httpOnly cookies + CSRF token, specifically because LLM-rendered/AI-touched HTML (sed.i renders extracted article HTML directly in the reader) raises XSS surface above a typical CRUD app.
- **XSS on extracted HTML** — also self-flagged, not yet mitigated with DOMPurify or sandboxed iframes. This is higher-stakes for sed.i than most apps because the HTML being rendered is *scraped from arbitrary third-party URLs the user doesn't control*, which is closer to the MCP ecosystem's stated 2026 concern ("compromised input surface, narrow the blast radius") than to typical user-generated-content XSS.

None of these are exotic fixes. They're flagged because "architecture gap vs. industry SOTA" has to include "security baseline a funded competitor would already have," and these three are the concrete instances.

### Gap 5 — No adjacency/recommendation model beyond flat similarity

`graphrag-multiagent-research.md` Break 3 already names this precisely: the recommendation engine (`GET /content/recommended`) scores embedding similarity + recency + tag overlap — it has no model of conceptual adjacency (e.g., knowing that five articles on gradient descent make backpropagation a natural next read). This is a known, documented gap already scoped in the existing research doc; restating it here only to note that **competitor products with community/graph-based retrieval (Microsoft GraphRAG-style global search) treat this as a first-class recommendation signal**, not just a retrieval-quality nicety. It's the same underlying infrastructure gap (no community/cluster layer above individual entities) showing up as a product weakness in two different features.

### Gap 6 — Pipeline observability is opt-in and effectively unused

Prefect (`PREFECT_ENABLED=false` default) requires two additional Railway services to activate. In practice this means the ingestion pipeline (extract → embed → tag → entity-analyze) runs with per-task Celery retry logic but no flow-level visibility (no DAG view, no per-run success/failure dashboard) unless a human turns on infrastructure that costs money to run. Competitors operating at any real user count have this on by default — pipeline observability is table stakes once ingestion has 5+ sequential stages, which sed.i's does.

---

## 3. Gap severity ranking

| # | Gap | User-facing? | Effort to close | Blocks what |
|---|---|---|---|---|
| 1 | No in-app chat/synthesis surface | Yes — this is the product | High (new UI + streaming + wiring to existing research/synthesis backend) | The core "AI-native" positioning; everything else in this doc is plumbing without this |
| 4a | In-memory **HTTP** rate limiter (`POST /content` only) | No (invisible until abused) | Low (Redis, already a hard dep) | Multi-instance deploy safety — distinct from LLM spend, which now has a Redis-backed per-user daily ceiling (`llm-gateway-hardening` Phase 3, complete) |
| 4b | localStorage JWT | No (invisible until exploited) | Medium (httpOnly cookie + CSRF migration touches all API call sites) | Session security baseline |
| 4c | Unsanitized extracted HTML | No (invisible until exploited) | Low–Medium (DOMPurify pass at render or extraction time) | XSS from arbitrary third-party content |
| 2 | Memory not user-visible/editable | Yes | Low (endpoint + simple settings page — data model already supports it) | Trust/transparency positioning against Claude Memory comparison |
| 6 | Pipeline observability opt-in | No | Low (flip default, size the two Railway services) | Debugging ingestion failures at scale |
| 5 | No adjacency/community model | Yes (recommendation quality) | High (community detection or lightweight cluster layer) | Recommendation and multi-hop search quality |
| 3 | No multimodal ingestion/synthesis | Yes | High (new extraction pipelines, TTS) | Feature parity with NotebookLM-class tools; may be out of scope by design |

---

## 4. What NOT to build

Consistent with `graphrag-multiagent-research.md`'s own conclusion: full Microsoft GraphRAG (Leiden clustering + pre-summarized community reports) is sized for 100k+ document corpora and doesn't amortize at single-user/hundreds-of-articles scale. The existing entity-graph implementation is the right-sized subset. Don't chase the SOTA paper; chase the SOTA product experience (Gap 1) with the infrastructure already built.

Similarly, multimodal ingestion (Gap 3) and full audio synthesis (NotebookLM parity) are expensive scope additions that should be a deliberate product bet, not a reflexive "competitors have it" reaction — sed.i's identity is a *reading* app; audio-first synthesis is a different product thesis.

---

## 5. Bottom line

sed.i's backend is closer to 2026 agentic-AI SOTA than its product surface suggests. The retrieval, memory, multi-agent, and LLMOps layers would not look out of place in a well-funded AI-native startup's architecture doc. The gap industry comparison actually reveals is:

1. **A missing chat/synthesis UI** sitting on top of infrastructure that's already built for it (Gap 1) — the highest-leverage fix, because it's not a new backend, it's exposing what exists.
2. **A missing transparency layer** on memory that Anthropic has made a competitive differentiator (Gap 2) — also cheap, because the data already exists.
3. **Three security baseline items** (Gap 4a-c) that are self-documented already and just need scheduling.
4. Two harder, legitimately-scoped-out items (Gaps 3, 5) that are product bets, not oversights.

Sources consulted (WebSearch, July 2026):
- Readwise Reader / Ghostreader — [aiforbusinessautomation.com](https://aiforbusinessautomation.com/tools/readwise-reader-review/), [skywork.ai](https://skywork.ai/skypage/en/unlocking-second-brain-readwise/1979074653299449856)
- Claude Memory vs. ChatGPT/Gemini memory — [Hacker News discussion](https://news.ycombinator.com/item?id=45214908), [glasp.ai](https://glasp.ai/articles/ai-memory-wars), [lumichats.com](https://lumichats.com/blog/chatgpt-memory-vs-claude-memory-vs-gemini-personal-intelligence-2026-which-ai-actually-knows-you)
- Agentic RAG production patterns — [digitalapplied.com](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide), [brightter.com](https://www.brightter.com/articles/agentic-rag-five-retrieval-patterns-that-survive-production)
- Mem.ai / Rewind / Limitless / memory-layer SaaS (Mem0, Zep) — [vellum.ai](https://www.vellum.ai/blog/best-personal-ai-assistants-with-memory), [memx.app](https://memx.app/blog/tried-every-ai-memory-app-2026/)
- NotebookLM multimodal synthesis — [medium.com/@jimmisound](https://medium.com/@jimmisound/the-cognitive-engine-a-comprehensive-analysis-of-notebooklms-evolution-2023-2026-90b7a7c2df36), [notebooklm-guide.com](https://notebooklm-guide.com/notebooklm-audio-complete-guide)
- LLM observability / gateway cost control — [braintrust.dev](https://www.braintrust.dev/articles/best-llm-gateways-observability-2026), [portkey.ai](https://portkey.ai/blog/the-complete-guide-to-llm-observability/)
- MCP ecosystem scale and security practice — [chatforest.com](https://chatforest.com/guides/mcp-ecosystem-2026-state-of-the-standard/), [aws.amazon.com MCP tool design](https://aws.amazon.com/blogs/machine-learning/mcp-tool-design-practical-approaches-and-tradeoffs/)

---

## 6. Engineering-quality comparison (staff-eng lens)

This section evaluates *how* the system is built — code paths, failure
handling, testing discipline, operational maturity — against what a staff
engineer would expect walking into a design review at a real AI product
company (Series B+ startup or a product team inside a larger org). Not
"does it have chat," but "if I had to be on-call for this, or review a PR
against this codebase, would I trust it."

Evidence base: `app/core/llm_client.py` (775 lines), `app/tasks/research.py`
(1286 lines), `app/core/hybrid_search.py` (857 lines), 47 Alembic migrations,
35 backend test files (~16.4K test LOC against ~19.9K app LOC — a ~0.82:1
test-to-code ratio), and git history back through the entity-graph and
LLM-gateway-hardening work.

### 6.1 Signals that read as senior, not junior

**Failover and retry are typed, not reflexive.** `LLMClient.chat()` retries
once on the *same* provider only for transient error classes
(`RateLimitError`, `APITimeoutError`, `APIConnectionError`); anything else
falls back to the other provider immediately. Most side projects either
retry everything blindly (masking real bugs as transient) or retry nothing
(brittle under normal API flakiness). This is the distinction pattern a
staff reviewer looks for — retry policy driven by error taxonomy, not by
`try/except: retry`.

**Budget enforcement degrades open, deliberately.** The per-user daily
spend ceiling (`check_budget`/`record_spend` in Redis) fails open if Redis
is unreachable, with the reasoning stated inline: Redis is already a hard
dependency for the Celery broker, so a Redis outage already stops
everything else — no point adding a second failure mode on top. That's the
kind of tradeoff a staff engineer states explicitly in a PR description
rather than leaving implicit. It's documented in ARCHITECTURE.md §9, not
just in a comment.

**Subprocess isolation for a specific, named memory problem.** PDF
extraction (`extract_with_yolo`) always runs in an isolated subprocess
because `torch`+`ultralytics` load ~1–1.5GB RSS that would otherwise
persist in the Celery worker's address space for the worker's lifetime.
The subprocess invocation even works around a specific stdlib shadowing
bug (`app/tasks/email.py` colliding with the `email` module `torch` needs
internally, fixed via `sys.executable -P`). This is the kind of fix that
only shows up after someone actually watched worker RSS climb in
production and root-caused it — not defensive boilerplate.

**Scale work is measured, not assumed.** The entity-dedup and entity-search
commits (2026-07-07) replaced an O(N²) self-join with HNSW ANN and report
concrete before/after numbers: "5.6× at N=10K, 19× at N=50K," and "~15s →
~2s at 2K entities/user." A fallback to sequential scan is documented for
when the HNSW index is absent. This is what a performance PR is supposed
to look like — a claim, a benchmark, a fallback path — and most solo/small
projects skip the benchmark and the fallback both.

**Multi-agent budget control is a real state machine, not a loop with a
counter.** The research pipeline has an explicit status machine (`queued →
planning → searching → synthesizing → verifying → done | partial | failed`),
a recovery task that reclaims orphaned runs after a timeout, resume support
that skips already-covered sub-questions via an idempotency key, and a
distinct `partial` terminal state for budget exhaustion (vs. silently
returning a worse answer or hanging). Budget is tracked across three
dimensions simultaneously (tokens, iterations, wall-clock timeout) — most
public agentic-RAG reference implementations track one.

**Security fixes show up as their own commits, driven by review, not
after an incident.** `fix(research): address PR review — SQL injection,
orphan FK, memory extraction reliability` and the text-to-SQL tool's
two-tier enforcement (`sqlglot` AST validation + a text-scan-and-AST-walk
belt-and-suspenders check for `user_id` isolation, documented in
ADR-0006) reflect a review process that's actually catching things, not
a checklist being filled in after the fact.

**Test-to-code ratio and structure.** ~0.82:1 test:app LOC is a healthy
ratio for a backend this shape (most solo AI-product codebases run
0.2–0.4:1). Golden-path tests are explicitly ordered to run first in CI.
Cross-user isolation is tested as its own concern across multiple test
files, not assumed. Migration hygiene has its own pre-commit check
(single-head enforcement) and a dedicated CI gate for ARCHITECTURE.md
staleness — most teams don't get automated doc-drift enforcement until
after they've been burned by stale docs at least once.

### 6.2 Where it reads as a strong individual project, not a funded product team

**Single points of manual intervention that a real product team would
automate.** The Redis-based rate limiter is explicitly in-memory
per-process (self-documented as broken across multiple instances). A
funded AI product serving real users would not ship this — not because the
fix is hard (it's a Redis `INCR`+`EXPIRE`, an hour of work), but because a
team with an on-call rotation and a security review gate would have caught
"this doesn't work past one instance" before merge, not after
self-documenting it as a known gap post-hoc.

**Provider abstraction exists partly for learning, not purely for
resilience** — and this is stated explicitly in ADR-0003 ("the project is
explicitly a learning vehicle... operating Bedrock is a distinct and
valuable skill," and LiteLLM was rejected specifically because it "obscures
the Bedrock API surface we want to learn"). That's a legitimate and
self-aware tradeoff for a learning-driven project. It's also a tell: a
staff engineer evaluating this purely as "would this decision survive a
design review at a company" would flag that rejecting a well-tested
abstraction layer to preserve a learning opportunity is backwards for a
product that has to stay maintainable by more than one person. Both things
are true — it's a reasonable choice for what this project is, and it's not
the choice a product-first team would make.

**No load testing or chaos testing anywhere in the repo.** The eval harness
(`evals/retrieval`, `evals/memory-consolidation-prompt`, `evals/research-brief`)
is genuinely good — it's the single strongest piece of engineering-process
evidence in the repo, and better than what most funded startups have at
Series A. But it evaluates *quality* (relevance, faithfulness, prompt
variant comparison), not *load* (concurrent users, Celery queue depth under
burst, Postgres connection pool exhaustion, pgvector HNSW index build time
under write pressure). A staff engineer's first question in a scale review
is "what happens at 10x," and there's no artifact in this repo that answers
that question for any subsystem.

**Observability was comprehensive-but-manually-wired; now closed by the
`llm-gateway-hardening` plan (Phases 2 and 4, complete as of this branch).**
Braintrust `user_id`/task metadata is now threaded through all 10
non-research call sites, and Bedrock calls get manual `braintrust_span` +
`span.log(metrics=...)` wrapping since `wrap_openai` can't instrument
`boto3` — closing the blind spot that would otherwise have left
`LLM_PROVIDER=bedrock` traffic invisible to cost/budget tooling. The gap
that remains is process, not code: this was caught and fixed by a
deliberate hardening pass, not by a dashboard alerting on missing
attribution — so the same class of gap (a new call site added later
without `braintrust_span` wrapping) has no automated guard against
recurring, only convention. Prefect pipeline-level observability is
still `PREFECT_ENABLED=false` by default and requires two more Railway
services to turn on — meaning the ingestion pipeline's multi-stage DAG has
no flow-level visibility in the deployment that's actually running, only
in the deployment nobody has turned on. This part of the finding is
unchanged.

**No incident/postmortem trail.** `docs/retros/` exists as a skill
(`/retro`) but there's no evidence in the repo of a *production incident*
retro — only feature retros. A product serving real users for any length
of time accumulates incidents (a bad migration, a runaway Celery queue, an
API key leak). Their absence here most likely just means the traffic
volume hasn't produced one yet, not that the process is missing — but it
also means the operational muscle of "something broke in prod, here's the
timeline and the fix" is untested.

### 6.3 The honest staff-engineer verdict

If this were a PR stack presented in a design review, the verdict is:
**the individual engineering decisions are frequently at or above the bar
of a well-run startup's backend team — the operational maturity around
those decisions is not.**

Concretely:

- **Code-level rigor**: at or above bar. Typed retries, explicit budget
  degradation semantics, measured performance work with fallbacks, a real
  state machine for the multi-agent pipeline, security fixes that come out
  of actual review cycles.
- **Testing discipline**: at or above bar for the backend (0.82:1 ratio,
  golden-path-first CI ordering, cross-user isolation tested explicitly).
  Frontend testing is thin by comparison (one Jest file covering reader
  utilities) — a real product team would not ship a reader/highlighting/
  writing-workspace UI this complex with only unit tests on bionic-reading
  string transforms.
- **Eval discipline**: genuinely ahead of typical practice — this is the
  strongest section of the whole codebase from a "does the team know what
  good looks like" perspective.
- **Operational maturity**: below bar. No load testing, partially-wired
  observability that required a manual audit to complete, an
  admittedly-broken rate limiter shipped as a documented gap rather than
  blocked at review, opt-in pipeline observability defaulted off in the
  environment that's actually serving traffic.
- **Process maturity**: mixed. Migration hygiene and doc-freshness are
  enforced by CI (strong signal). Incident response as a practiced
  discipline is untested (no signal either way, but no evidence of it
  either).

The honest one-line summary: **this reads like an unusually strong solo/
small-team engineer building at senior-to-staff code quality, without yet
having had the operational pressure (on-call, real incidents, load beyond
one process) that turns individually-good decisions into a system that's
been proven to survive contact with production at scale.** That pressure
is precisely what's absent — not because of a skill gap, but because it
hasn't been applied yet. The in-memory rate limiter and the months-long
partial-tracing gap are the two concrete artifacts of that absence; the
retry/budget/dedup/state-machine work is the concrete evidence the skill
is already there for when it is.

---

## 7. Whole-product system design review

§6 covered one subsystem (LLM gateway + agentic pipeline). This section
covers the rest: data layer, API layer, background processing, frontend,
auth end-to-end, deployment topology, and the browser extension's trust
boundary — the areas a staff engineer would actually spend most of a
system-design review on, because most production incidents come from the
plumbing, not the AI code.

Evidence base: `app/core/database.py`, `app/api/content.py`,
`app/core/deps.py`, `app/models/*.py` (10 tables with `ondelete="CASCADE"`
FKs), `frontend/lib/api.ts`, `frontend/contexts/AuthContext.tsx`,
`Procfile`, `Makefile`, `extension/manifest.json`.

### 7.1 Data layer

**Schema discipline is solid.** Every user-owned child table (`highlights`,
`content_chunks`, `entities`, `entity_mentions`, `list` membership, drafts,
reading clusters, refresh tokens) has `ondelete="CASCADE"` FKs back to
`users`/`content_items`. Combined with soft-delete (`deleted_at`) on the
primary entities, this is a deliberate two-layer deletion model — CASCADE
handles hard structural cleanup (account deletion), soft-delete handles
reversible user actions (archiving, removing a saved item). That's the
correct pattern and most projects get only one of the two layers right.

**Connection pool is sized for one process, not a fleet.**
`create_engine(..., pool_size=3, max_overflow=2, pool_recycle=1800,
pool_pre_ping=True)` — 5 connections max per FastAPI process. `pool_recycle`
and `pool_pre_ping` are the right calls for a managed Postgres that can
silently drop idle connections (Railway/RDS-style). But at `pool_size=3`,
any handler that holds a connection across a slow synchronous call
(the recommendation endpoint's full in-Python scoring loop, described
below, is exactly this shape) will exhaust the pool under concurrent
requests from a single busy user, let alone multiple users. This number
was almost certainly picked empirically against a dev workload of one
person, not load-tested — there's no artifact anywhere in the repo
suggesting otherwise (consistent with the load-testing gap already noted
in §6.2).

**No ORM eager-loading anywhere in the codebase** (`grep -rn
"selectinload\|joinedload"` returns zero hits across the entire `app/`
tree). Every relationship access that touches related rows either goes
through hand-written SQL (the pgvector paths, correctly, since raw SQL is
required there anyway) or risks N+1 if a route ever iterates a collection
and touches a lazy relationship. Today's list-serving routes appear to
avoid the trap by using flat queries and array columns (`tags` is a
Postgres array, not a join table) rather than by using eager-loading
correctly — which means the *absence* of N+1 bugs right now is closer to
"the current query shapes happen not to trigger it" than "the team has a
policy against it." That's a fragile invariant to depend on as more
relationship-heavy features (drafts, research runs, entity relations) grow
query complexity.

**The recommendation endpoint (`GET /content/recommended`) is an O(N)
full-table scan with cosine similarity computed in pure Python, per
request.** It loads every unread item for the user, then for each one
loops over up to 7 days of recent reads computing dot products by hand
(`sum(x*y for x, y in zip(a,b))` over 1536-dim vectors, in Python, not
even numpy). This is fine at tens of unread items. It stops being fine
in a way that's completely invisible until a power user's queue crosses
a few thousand unread items — at which point every dashboard "For You"
load becomes a multi-second, single-threaded, connection-pool-holding
Python loop. The exact same pattern (pgvector `<=>` operator, used
correctly and documented at ARCHITECTURE.md §12) already exists
elsewhere in the codebase for search — this endpoint just doesn't use it.
This is the single clearest "would get flagged in a design review before
merge" finding in the whole system: a hand-rolled O(N) algorithm sitting
next to a working O(log N) index-backed version of the same computation
in the same codebase.

### 7.2 Auth and session management

**Refresh-token infrastructure exists in the backend and is unused by the
frontend.** `app/models/refresh_token.py`, `POST /auth/refresh`, hashed
token storage, and expiry logic are all implemented (`app/api/auth.py`).
But `frontend/lib/api.ts` and `frontend/contexts/AuthContext.tsx` contain
zero references to refresh — the frontend still stores a single long-lived
JWT in `localStorage` and never calls the refresh endpoint. This means:

- The backend did the harder half of a real session-security fix (rotating
  refresh tokens, hashed-at-rest, revocable) and the frontend integration
  that would actually deliver the security benefit was never finished.
- ARCHITECTURE.md §6 documents "localStorage token, XSS-accessible,
  production hardening: httpOnly cookies + CSRF" as a known gap — but
  doesn't mention that half the fix (refresh tokens) is already sitting
  in the backend unused. That's a doc gap worth closing on its own, but
  more importantly it's a product gap: shipping the backend half of a
  security feature without the consuming half means the actual attack
  surface (a stolen long-lived JWT) is unchanged even though the schema
  suggests it's been addressed.

A staff engineer reviewing this would treat it as higher priority than
either "add refresh tokens" (already done) or "migrate to httpOnly
cookies" (still the eventual right answer) alone — it's specifically the
half-finished state, discoverable only by reading both sides of the stack,
that's the risk.

### 7.3 API layer

**Route-ordering footguns are self-documented, repeatedly, across three
different routers** (`/content`, `/search`, `/search/connections`) —
literal paths must be registered before parameterized `{id}` paths or
FastAPI misroutes them as UUID lookups. This is called out three separate
times in ARCHITECTURE.md as a "note." A pattern that needs the same
warning three times in the same codebase is a signal that the underlying
cause (flat route registration order as the only guard against this
class of bug) hasn't been addressed at the framework level — e.g., with a
dedicated sub-router for literal-path endpoints, or an explicit ordering
lint. Each individual instance is a one-line fix; the repeated need to
remember it is the actual issue.

**Error contract is genuinely consistent.** `{detail: string}` uniformly,
global exception handlers for `RequestValidationError`/`SQLAlchemyError`/
generic `Exception`, no internal details leaked on 500. This is a real,
enforced convention (CLAUDE.md rule 9), not aspirational — worth noting
because it's an easy thing for teams to claim and not actually hold to
under time pressure, and this codebase holds to it.

**Rate limiting covers exactly one route.** `POST /content` is limited;
nothing else is — not `/auth/login` (brute-force surface), not
`/search/semantic` (the most expensive read path per-request, given it
can fan out to three retrieval lanes plus an LLM-generated insight call),
not the MCP endpoints. Combined with the in-memory implementation already
flagged in §6.2, this is under-scoped even relative to its own
architecture, not just under-scoped relative to industry practice.

### 7.4 Background processing

**Task chaining shape is correct — async where it should be, chained where
order matters** (`extract_metadata` → `generate_embedding` → `generate_tags`),
matching the ARCHITECTURE.md-documented flow. `max_retries=3` on
extraction, distinct `processing_status` states surfaced to the frontend
so the UI can show real state rather than a generic spinner. This is
solid, unremarkable-in-a-good-way task design.

**Beat-scheduled tasks lack a documented overlap/backpressure story.**
`cluster_all_users_task` (weekly), `consolidate_all_users_task` (nightly),
`embed_new_entities_beat_task` (hourly), `backfill_missing_entities_task`
(daily) all fan out per-user Celery tasks from a single beat trigger. At
current scale this is invisible. There's no evidence in the repo of what
happens if a fan-out from the previous run is still draining when the
next one fires (a slow nightly consolidation overlapping the next night's
run), and no queue-depth or worker-saturation alerting mentioned anywhere
in the observability stack (`docs/decisions/0002-observability-stack.md`
covers traces and errors, not queue health). This is the kind of gap that
stays invisible for a long time and then produces a very confusing
incident the first time it's hit.

**Worker concurrency is `--pool=solo --concurrency=2` in dev** (per
`Makefile`), which is fine locally but is a dev-only config choice, not
evidence either way about production worker sizing — worth flagging only
because there's no equivalent production worker-sizing documentation
anywhere in ARCHITECTURE.md to check it against.

### 7.5 Frontend architecture

**Component-to-test ratio is thin.** 54 components in `frontend/components/`
against 8 test files, and of those 8, most cover isolated utility/rendering
logic (bionic reading transforms, filter dropdown, content item cards) —
not the interaction-heavy surfaces: `Reader.tsx`'s `c`-key state machine
for the two-mode connections panel, the highlight creation/selection
toolbar flow, the writing workspace's autosave-then-relevant-reads
sequencing, or the extension↔ephemeral-reader↔save-to-library handoff
described in ARCHITECTURE.md §14. These are exactly the areas most likely
to regress silently, because they're stateful and cross-component, and
none of them are covered by anything beyond manual testing per the
CLAUDE.md UI-testing rule ("start the dev server and use the feature in a
browser"). Manual verification is a reasonable stopgap for a small team,
but it's a standing regression risk on the most complex parts of the UI,
not the simplest ones.

**Context usage is appropriately scoped, with one piece of acknowledged
dead weight.** `AuthContext`, `ListsContext`, `PlayerContext`,
`ReadingSettingsContext` all have clear, non-overlapping responsibilities.
`ToastContext` is explicitly documented as "Legacy — replaced by inline
`InlineError` feedback... still exists but is unused." Leaving it in place
rather than silently deleting it (with the dead-code fact stated in
ARCHITECTURE.md rather than hidden) matches this project's own stated
convention (CLAUDE.md: "mention pre-existing dead code but don't delete
it unless asked") — a small thing, but consistent self-discipline is
itself a signal.

**SSR/hydration mismatches are handled deliberately, not accidentally.**
`ReadingSettingsContext` exposes a `hydrated: boolean` specifically so
`PreviewBox` avoids flashing server defaults before `localStorage` values
load; `Navbar`'s mini-player uses a `mounted` guard for the same reason.
This is the correct, known pattern for Next.js App Router + localStorage-
backed state, applied consistently across at least two components rather
than fixed once and left inconsistent elsewhere — a good sign of the
pattern being understood, not just patched around a specific bug report.

### 7.6 Deployment topology

**Migrations run inline on every boot, in the same process that then
serves traffic.** `Procfile`: `poetry install → alembic upgrade heads →
uvicorn`. On a platform like Railway, if two instances redeploy
concurrently (a real possibility if `RAILWAY_REPLICAS` is ever set above
1, or during a rolling restart), both will attempt `alembic upgrade heads`
against the same database at roughly the same time — Alembic's revision
locking makes this safe from *corruption*, but it means deploy time is
coupled to migration time, migrations are not separated from app
availability, and there's no visible rollback story if a migration fails
mid-deploy (does the old code keep serving on the new schema, or does the
deploy just fail loudly?). This is an extremely common pattern for
single-instance side projects and inappropriate the moment horizontal
scaling is needed — worth flagging now specifically because §7.1 already
identified connection-pool sizing as tuned for one instance too. Both
findings point at the same underlying fact: **this system is architected,
tuned, and deployed for exactly one backend process**, which is a
coherent and reasonable choice at current scale, but is not a "just add
replicas" system today — someone would need to separate migration-from-
deploy and move rate limiting to Redis (already flagged) before
`RAILWAY_REPLICAS=2` is safe to flip.

**Runtime dependency shuffling at deploy time is a smell, even though it's
explainable.** The `Procfile` uninstalls `opencv-python` and reinstalls
`opencv-python-headless` after `poetry install`, on every single boot —
almost certainly because `ultralytics` (the YOLO PDF-layout dependency)
pulls in the GUI variant of opencv transitively and there's no
headless-only constraint expressible cleanly in the Poetry lockfile for
that transitive dependency. It works, but it adds real time and fragility
to every deploy (a network blip during the extra `pip install` step fails
the boot), and the actual fix (pin the transitive dependency, or exclude
GUI extras at the lockfile level) is a solved problem in Python packaging
that this works around at runtime instead of at build time.

### 7.7 Browser extension trust boundary

**Extension permissions are minimal and correct** —
`["activeTab", "scripting", "storage"]`, no `<all_urls>` host permission,
no persistent background access to every tab. This is the right default
and better than a lot of production extensions that request broad host
permissions out of convenience. Content script injection is on-demand
(`chrome.scripting.executeScript`) rather than declared to run on every
page load, which is both a performance and a trust-surface win.

**HTML sanitization at the boundary is partial.** The content script
strips `iframe/frame/object/embed/script` before the extracted HTML is
handed to the reader overlay (per ARCHITECTURE.md §23) — a reasonable
extraction-time filter. But the same unsanitized-HTML-in-reader gap
flagged in §2/Gap 4c applies identically here: stripping four tag types
at extraction time is not equivalent to DOMPurify-grade sanitization
against the full XSS payload space (event handler attributes on any
remaining tag, `javascript:` URLs, `srcdoc`, SVG-based vectors, etc.),
and this extension is specifically the surface that turns arbitrary
third-party page content into HTML the app trusts enough to render
directly. This is the same root gap as §2 Gap 4c, showing up a second
time at a second point in the pipeline — worth fixing once, centrally
(sanitize at storage/render time, not per-ingestion-path), rather than
patching each ingestion path (extension, backend trafilatura, PDF
pipeline) separately as this pattern currently suggests is happening.

### 7.8 Whole-product verdict

Extending the §6.3 conclusion across the full system: the pattern holds.
**Where a decision required understanding a specific failure mode, the
decision is usually right** — CASCADE + soft-delete together, `pool_pre_ping`
against a managed Postgres that drops connections, on-demand content-script
injection, deliberate SSR-hydration guards, a real error contract enforced
consistently. **Where a decision is implicitly "whatever works for one
developer on one machine," it hasn't yet been revisited for a multi-
instance, multi-user-at-scale reality** — the connection pool, the
recommendation endpoint's algorithm, rate limiting's single-route scope,
migrations coupled to boot, the half-finished refresh-token rollout.

None of these are hard fixes individually. What they have in common, and
why a staff engineer would cluster them into one finding rather than five
separate tickets, is that **they all share the same root cause: this system
has never been forced to run as more than one instance, under more than
one concurrent real user, against more than a personal-scale dataset.**
Every gap in this section is a symptom of that, not an independent skill
gap — which is actually good news, because it means the fixes are known
and bounded (Redis rate limiter, numpy-or-pgvector for recommendations,
separate migration step from boot, wire up the frontend refresh-token
call, raise pool size and load-test it) rather than requiring a redesign.
The engineering judgment on display when a failure mode *was* encountered
(§6.1, and the CASCADE/soft-delete/hydration patterns above) is consistently
good. The gaps are entirely in the set of failure modes that single-user,
single-instance development never surfaces — which is exactly what a
staff-level system-design review exists to catch before it becomes an
incident instead of a code review comment.

---

## 8. Architecture proper — system shape, boundaries, and coupling

§6 and §7 asked "is this built well." This section asks a different
question: **is this system shaped correctly** — where are the seams, which
way does coupling point, is there one architecture or several competing
ones, and would the current shape survive the product growing in scope
without a rewrite. This is what a staff-level *architecture* review
(as opposed to a code review) actually spends its time on.

Evidence base: `app/services/content.py` (199 lines, the only service-layer
file in the backend), import graph across `app/api/`, `app/tasks/`,
`app/core/`, `app/mcp/`; `app/api/search.py` (870 lines); the three ADRs
(`0001-vector-storage.md`, `0003-llm-provider-strategy.md`,
`0005-pipeline-orchestration.md`); and the Prefect/Celery dual-orchestration
setup described in ARCHITECTURE.md §24.

### 8.1 The macro shape: correctly a modular monolith, not over- or under-built

At the top level, this is a modular monolith — one FastAPI app, one Celery
worker pool, one Postgres — with clean module boundaries (`api/`, `core/`,
`tasks/`, `models/`, `mcp/`, `services/`) rather than a premature
microservice split. For a single-user-to-early-multi-tenant product, this
is the correct call, and it's a call a lot of AI-native products *get
wrong in the other direction* — reaching for separate services (a vector
DB service, a memory service, an agent-orchestration service) before
there's traffic that justifies the operational cost of running them
separately. sed.i's decision to keep pgvector *in* Postgres rather than
spinning up a dedicated vector DB (ADR-0001) is the same instinct applied
correctly at the data-layer level, and it's stated as a deliberate,
revisitable decision rather than defaulted into.

**The one place this instinct was NOT applied consistently is
orchestration**, and that's worth its own finding (§8.4).

### 8.2 Dependency direction is clean at the macro level

The import graph is exactly what you want: `app/api/*` imports from
`app/tasks/*` (routes dispatch background work) and `app/core/*` (routes
use shared infra), but nothing in `app/tasks/`, `app/core/`, or `app/mcp/`
imports anything from `app/api/`. That's a real, unbroken one-directional
dependency — API is a thin dispatch layer over a domain/task layer that
doesn't know the API exists. This matters architecturally because it means
the domain logic (extraction, embedding, search, research) is reusable
from *any* entry point — which is exactly what's happening: the MCP server
(`app/mcp/`) and the REST API both call into the same `app/tasks/` and
`app/core/hybrid_search.py` without either one depending on the other.
**This is the single strongest piece of pure-architecture evidence in the
codebase** — it's the reason MCP could be bolted on as a second front door
without duplicating the retrieval/synthesis logic, and it's the kind of
boundary discipline that's usually the first thing to erode under deadline
pressure in a real product. It hasn't eroded here.

### 8.3 Where the architecture is inconsistent: the service-layer seam was invented once and never generalized

`app/services/content.py` exists and does real work — `ingest_url()` and
`DuplicateContentError` encapsulate the URL-save business logic (dedup
detection, extraction-path branching) outside the route handler. This is
correct layering: transport (FastAPI route) → domain logic (service) →
persistence/background dispatch (tasks/core). It's used by exactly one
router (`content.py`).

Every other router — `search.py` (870 lines), `auth.py`, `vinyl.py`,
`lists.py`, `memory.py` — has its business logic written directly inside
the `@router.get/post` function bodies. `search.py` is the clearest case:
the core retrieval algorithm is correctly factored out to
`app/core/hybrid_search.py` (good), but the connections/insight subsystem
— `_search_highlights`, `_connections_for_highlight`, `_call_insight`,
inline Redis client construction — lives as private, underscore-prefixed
functions directly in the route file, not in `app/core/`. There's no
`app/services/search.py` or `app/services/connections.py` alongside the
one that exists for content.

This is not "the code is disorganized" — each individual router is
internally coherent and readable. It's that **the codebase has two
competing answers to "where does domain logic live,"** and which one a
given router uses appears to depend on when that router was last touched
significantly, not on a stated rule. A staff-level architecture review
would flag this specifically because it's the kind of inconsistency that
compounds: the next engineer (or agent) adding a feature to `auth.py` has
no signal from the codebase about whether new logic should go in a service
file or inline, and will most likely match whatever's already in that
specific file — which perpetuates the split rather than resolving it. The
fix isn't "add more service files" reflexively; it's deciding, once,
whether `services/` is the pattern going forward and either applying it
where routers exceed some real complexity threshold or formally scoping
it back to "content ingestion only, because that's genuinely special" —
either is defensible, but right now neither has been decided.

### 8.4 The planned three-tier orchestration architecture, with the middle tier unbuilt

ADR-0005 is worth reading in full because it changes this finding from
"unresolved inconsistency" to something more precise. sed.i has a
**deliberately planned three-tier orchestration architecture**, reasoned
about explicitly per-tool:

| Tool | Role (per ADR-0005) | Status |
|---|---|---|
| Celery | Immediate async dispatch, in-request trigger layer | Live, load-bearing |
| Prefect | Pipeline DAG visibility + per-step retry for the ingestion pipeline | Built, `PREFECT_ENABLED=false` |
| Temporal | Durable multi-step execution for the research agent ("required for 10+ step agents," survives worker restarts, built-in saga/compensation) | **Planned, not built** |

The ADR's own reasoning is specifically why Prefect was rejected for the
research agent ("Prefect tasks are not durable — a worker restart loses
in-flight flow state... Temporal persists workflow state and can resume
from exactly where it left off"). That's the correct call, and it means
the research pipeline's current implementation — Celery chains with a
bespoke status machine (`queued → planning → ... → done | partial | failed`),
a hand-rolled orphan-recovery beat task (`recover_orphaned_runs`, which is
functionally Temporal's "worker restart resume" durability guarantee,
reimplemented as a 10-minute staleness poll instead of true durable
state), and manual token/iteration/timeout budget tracking — is **the
identified-in-advance stopgap for a documented, unbuilt Tier 3**, not an
accidental architectural fork.

This reframes the finding: it's not "the codebase has two competing
answers and hasn't picked one" (§8.3's actual shape) — it's "the codebase
correctly diagnosed it needs a durable execution engine for the research
agent, wrote that decision down, and hasn't built it yet." The gap between
those two framings matters, because the fix is different: §8.3 needs a
decision; this needs execution against a decision that's already made.

**What's actually at risk today, concretely**: `recover_orphaned_runs`
polling for staleness every 10 minutes is a materially weaker guarantee
than Temporal's actual workflow-state persistence — a worker crash
mid-research-run doesn't resume from its exact last step, it gets marked
`partial` after a 10-minute window and the user gets a degraded result,
not a completed one. For a multi-agent pipeline that can run 3 iterations
× 6 subagents with real LLM cost already spent, "lose the last few minutes
of work and downgrade the run to partial" is the exact failure mode
ADR-0005 named Temporal as the fix for. The ADR is right that Prefect
alone doesn't solve it. The gap is that the interim state (Celery-only,
no Temporal) has been running in production since the research pipeline
shipped, and there's no evidence of it being revisited since.

### 8.5 Would this survive the product doubling in scope?

The honest architecture answer: **yes, for the retrieval/agentic core;
uncertain, for the API surface.**

- **Retrieval and agentic core**: the hybrid-search/entity-graph/research
  pipeline layer is well-bounded, reusable from multiple front doors (REST,
  MCP), and the one-directional dependency graph means new consumers
  (a future chat UI — see §2 Gap 1) can be added without touching the
  domain layer. This part of the architecture would absorb 2x scope
  without restructuring.
- **API surface**: the service-layer inconsistency (§8.3) means growth
  in the API layer doesn't have a settled pattern to follow, which means
  each new router either repeats the "logic inline in the route" shape
  (accruing more of the same inconsistency) or the team has to stop and
  retroactively decide the pattern under the pressure of a specific PR,
  which is a worse time to decide it than now.
- **Orchestration**: the plan (ADR-0005) is sound and the interim state
  (Celery-only) is an explicitly acknowledged stopgap, not an accident —
  but "acknowledged" isn't the same as "safe to leave indefinitely." The
  research pipeline is the most complex, most expensive-per-run, most
  failure-prone workflow in the product, and it's the one running on the
  weakest durability guarantee of the three tiers, by design, until Tier 3
  is built. Scope doubling that adds more research-pipeline-shaped features
  (more agent workflows, more multi-step LLM pipelines) increases exposure
  to exactly the failure mode ADR-0005 named Temporal as the fix for,
  faster than it increases pressure to actually build Temporal.

### 8.6 Architecture-level verdict

Structurally, this is a well-drawn modular monolith with one genuinely
excellent property (clean one-directional API→domain dependency, proven
by MCP reusing the same core without duplicating it), one real unresolved
inconsistency, and one correctly-diagnosed-but-unbuilt piece of planned
architecture:

- **Unresolved inconsistency** (§8.3): where domain logic lives in the API
  layer — service files vs. inline in the route — has two live answers
  with no stated rule distinguishing when to use which. This is a decision
  that hasn't been made yet, not one that was made and deferred.
- **Diagnosed but unbuilt** (§8.4): the three-tier orchestration plan
  (Celery / Prefect / Temporal) correctly identifies that the research
  agent needs durable execution, and has been running on the weaker
  Celery-only fallback since it shipped, with the documented reason
  (Tier 3 isn't built yet) rather than an undocumented one.

These are different kinds of gaps and deserve different responses: §8.3
needs someone to pick a rule and apply it; §8.4 needs someone to either
build Temporal or consciously re-evaluate whether the interim Celery
state machine's actual failure mode (partial results after a crash,
not resumed results) is acceptable at current run volume — which is a
product/risk call, not just an engineering one. Both gaps become
materially more expensive to close the longer the product's scope grows
around the current interim state, which is the same underlying dynamic
§7's verdict identified from the infrastructure side: **decisions made
under single-developer, low-volume conditions are individually reasonable
and become the wrong shape specifically at the point the product outgrows
those conditions** — and for §8.4 specifically, that point is not
theoretical; ADR-0005 already named the exact scenario ("10+ step agents,"
survives restarts) where the current fallback stops being adequate.
