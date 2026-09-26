---
type: research
status: active
last_updated: 2026-07-21
consumer: human
---

# sed.i vs. SOTA RAG Stack — Tech Choice & Workflow Review

Answers three specific questions the earlier gap analysis
(`sota-industry-comparison.md`) didn't: **is this how a SOTA RAG product is
actually built, is the tech stack the right stack, and would a real team
use something like Databricks instead of this.** That doc compared
product surface and code quality. This one compares component-level
architecture decisions against what 2026 production RAG guidance and
comparable products actually converge on.

Evidence base: full-repo inventory (all 17 tables, all 47 migrations, all
7 ADRs, `app/core/hybrid_search.py`, `app/core/llm_client.py`,
`app/tasks/research.py`, `app/tasks/chunk_embeddings.py`, `Procfile`,
`nixpacks*.toml`, `infra/__main__.py`) plus July 2026 web research on
production RAG stack decisions, vector DB scale thresholds, reranking
necessity, and agent-orchestration framework adoption.

---

## 1. Is pgvector the right call, or should this be a dedicated vector DB / data platform?

**Verdict: pgvector is correct, and ADR-0001's reasoning holds up against
current 2026 guidance almost exactly.** Independent sources converge on
the same threshold sed.i's own ADR landed on without citing them:

> "If you're starting a new production RAG project today and you don't
> have a strong reason to pick something else, start with pgvector on
> Postgres. Move to a dedicated vector DB only when you can name the
> specific bottleneck that forced the move." — [zenvanriel.com](https://zenvanriel.com/ai-engineer-blog/pgvector-vs-dedicated-vector-db/)

> "Default to pgvector on Postgres — it's enough for 80% of RAG systems
> up to ~10M vectors and reduces operational complexity." — [KnowSync](https://www.knowsync.ai/blog/choosing-vector-database-qdrant-pinecone-pgvector-2026)

> "4 clients have migrated from Pinecone back to pgvector in the past 18
> months because operational simplicity outweighed Pinecone's performance
> edge for their scale." — [devstarsj.github.io](https://devstarsj.github.io/2026/04/04/postgresql-pgvector-pgvectorscale-rag-production-guide-2026/)

sed.i is single-user-to-small-team scale, well under 100K vectors today.
The migration triggers ADR-0001 names (5M vectors, 200ms p95, >100
writes/sec) are the same shape of trigger the external sources use. This
is not a case of "the project talked itself into the easy choice" — it's
the choice the market actually converged on for this scale.

**Databricks specifically**: no 2026 source treats Databricks Mosaic AI
as a peer alternative to pgvector/Pinecone/Qdrant for a project at sed.i's
scale. The consistent framing is organizational, not technical —
Databricks Vector Search is a feature *of* a lakehouse platform, adopted
because a company already has petabytes in Delta tables, multiple data
teams, and a governance/lineage requirement, not because it retrieves
vectors better than a purpose-built vector store. [Startupik's](https://startupik.com/when-should-you-use-databricks/)
own framing: it depends on "data complexity, team maturity, and how fast
you need to turn raw data into production AI" — none of which describes
a single-Postgres-instance app with 17 tables and one backend developer.
**Reaching for Databricks here would be the over-engineering mistake ADR-0001
explicitly reasoned its way past when it rejected Qdrant for the same
reason** ("adds an extra service... without a measurable quality or
latency benefit at current scale") — Databricks is a strictly heavier
version of the same wrong call.

**Where the ADR's reasoning and the actual codebase diverge — this is the
real finding, not the vector-DB choice itself**: ADR-0001's stated
decision is "pgvector **with HNSW indexing**," and the aspirational
latency claim ("sub-50ms p95... with a proper index") depends on that
index existing. It doesn't, for the tables that matter. `content_items.embedding`,
`highlights.embedding`, and `content_chunks.embedding` — the three
columns actually queried on every semantic search request — have **no
ANN index at all**. Only `entities.embedding` (a secondary retrieval
lane) got HNSW; `tag_embeddings` and `research_memory` got IVFFlat, not
HNSW. The primary semantic-search path is doing exact `<=>` cosine
distance over a full sequential scan, every query. There's even a
dedicated eval (`evals/hnsw-index/runner.py`) benchmarking seq-scan vs.
HNSW at N=1K/5K/10K/50K — the team already measured the case for doing
this and the finding was never applied to the tables it matters for.

This is invisible today (low row counts hide it), which is exactly the
kind of gap that's cheap to fix now and expensive to discover in
production later. **This is the single highest-priority concrete finding
in this review** — not a stack choice question, a "the stack decision
that was made was never actually implemented" question.

---

## 2. Is the chunking/embedding strategy SOTA?

**Mostly yes, with one real gap.** `chunk_embeddings.py`'s approach —
structure-aware splitting at HTML headers, sentence-boundary splitting
within over-long sections, ~350-token targets, 40-word overlap, and a
contextual prefix (`From the article "{title}" (section {i+1} of
{total}): {chunk}`) before embedding — **is Anthropic's own published
"contextual retrieval" pattern**, applied correctly. This is a genuinely
current technique (2024–2026 consensus), not something dated.

Embedding model: `text-embedding-3-small` (1536-dim) is a reasonable,
cost-appropriate choice — not the newest OpenAI embedding model, but
adequate for the corpus size and consistent with the Bedrock
Titan-embed-v1 fallback being dimension-compatible (a real constraint
correctly respected — embeddings never fall back cross-provider, by
design, to avoid mixing vector spaces).

**The gap: no reranker.** 2026 production guidance is unusually
consistent and blunt on this point:

> "Reranking is the single largest precision gain in the entire
> pipeline... can improve retrieval quality by up to 48%." — [DigitalApplied](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)

> "Combine BM25 sparse retrieval, dense embeddings, RRF fusion, and a
> cross-encoder reranker... pushes recall@10 from 78% to 91%." — [AppScale](https://appscale.blog/en/blog/hybrid-search-and-reranking-production-rag-bm25-dense-cross-encoder-2026)

sed.i's hybrid search does exactly the first three of those four steps
(keyword + dense + RRF) and stops. There is no cross-encoder, no Cohere
Rerank, no LLM-as-reranker anywhere in the retrieval path — confirmed by
direct code read of `hybrid_search.py`. This is a known, already-scoped
gap (Layer 5 in `sota-layer-plan.md`, explicitly deferred "revisit when
retrieval quality is the bottleneck") — so it's not an oversight, it's a
deliberate deferral. But it's worth naming plainly against the external
numbers: **a reranker is not a nice-to-have refinement in 2026 guidance,
it's described as the largest single lever available**, larger than most
other tuning available in this stack. Given sed.i already has an eval
harness (`evals/retrieval/`) measuring R@10/MRR/NDCG, this is the kind of
change that could be measured cheaply (Cohere Rerank free tier, or a
small cross-encoder via Modal) rather than argued about.

**The RRF implementation itself has a subtler issue worth flagging**:
`mode="full"` fuses three lanes via RRF (`k=60`, standard `1/(60+rank)`)
but the entity-graph lane doesn't participate in RRF — it's a separate
IDF-dampened cosine score scaled by a hardcoded constant
(`_ENTITY_SCORE_SCALE = 0.025`) added directly into the same score dict.
This is a **weighted-sum-bolted-onto-RRF hybrid**, not pure RRF. RRF's
whole point is sidestepping the "different lanes produce
non-comparable scores" problem by using rank position instead of raw
score — mixing in one lane's raw score reintroduces exactly the problem
RRF exists to avoid. It probably works in practice because the entity
lane is a minority contributor by design, but it's worth being honest
that this isn't textbook RRF, and if entity-lane influence needs tuning
later, the fix isn't an RRF weight (there isn't one) — it's a magic
number.

---

## 3. Is the query classification / routing approach SOTA?

**Yes — this is the right architectural instinct, correctly implemented
cheaply.** `search_router.py`'s pure-regex classifier (operator syntax →
filter, quoted phrase → keyword, domain-shaped string → filter,
question-shaped → semantic, ≤3 words → keyword, else → hybrid, <1ms, no
LLM call) matches what's called "Adaptive RAG" in 2026 guidance — route
cheap, deterministic queries to fast paths and reserve expensive
multi-lane fan-out for genuinely ambiguous queries. Most public writeups
of adaptive routing use an LLM classifier for this step (cheap model, but
still a network call + latency + cost per search). sed.i's regex-first
approach is strictly cheaper and faster for the same architectural
pattern, at the cost of being less flexible to genuinely novel query
phrasings — a reasonable tradeoff at this scale, and one that's easy to
upgrade later (swap the classifier function, not the routing
architecture) if regex proves too brittle.

---

## 4. Is the multi-agent research pipeline SOTA, and should it be on a framework?

**This is the most consequential judgment call in the whole system, and
it's a closer call than the pgvector question.** The research pipeline
(`app/tasks/research.py`) is a hand-rolled 6-role agent topology (lead →
subagents → collector → synthesizer → verifier → recovery) built entirely
on raw Celery `group`/`chord` primitives — no LangGraph, CrewAI, AutoGen,
or any agent framework anywhere in the codebase.

2026 guidance leans the other way from what sed.i built:

> "LangGraph has emerged as the leading standard for production-grade
> agent systems... provides a graph-based orchestration layer where every
> agent's decisions, retrievals, and intermediate outputs are represented
> as nodes in a persistent graph... state, transitions, and logic are all
> explicit." — [LangChain](https://www.langchain.com/resources/ai-agent-frameworks)

> "Building these primitives from scratch means reinventing message
> passing, state checkpointing, handoff protocols, and failure recovery."

That's a real critique, and sed.i's pipeline is a live example of exactly
that reinvention: `research.py` hand-rolls a status state machine
(`queued → planning → searching → synthesizing → verifying → done |
partial | failed`), hand-rolls idempotency keys for resume support, and
hand-rolls orphan recovery via a 10-minute-staleness beat-task poll
instead of true checkpointed state. That's real work that a framework
like LangGraph would give for near-free via its built-in persistence
layer and checkpointer.

**But the counter-case is stronger than the guidance above implies, for
sed.i specifically**, for three concrete reasons:

1. **This system already has real production infrastructure the
   framework would have to sit on top of, not replace.** Celery is
   already the load-bearing task queue for the entire ingestion pipeline
   (extraction → embedding → tagging → entity analysis), with beat
   scheduling, retry policy, and worker deployment already solved.
   Adopting LangGraph doesn't remove Celery — it adds a second
   orchestration paradigm alongside it, which is a bigger architectural
   split than the one already flagged in §8.3 of the industry-comparison
   doc (service-layer inconsistency). ADR-0005 already reasoned through
   exactly this three-tier tradeoff (Celery / Prefect / Temporal) and
   picked Temporal, not LangGraph, as the durability answer — because
   Temporal solves the actual gap (durable execution surviving worker
   restarts) without also replacing the agent-logic layer, which
   LangGraph would.

2. **The verification/grounding logic (`verify_synthesis_task` stripping
   uncited claims, injecting missing gap entries) is domain-specific
   business logic, not orchestration** — a framework wouldn't remove this
   code, it would just host it in a different execution shell. The
   actual hard-won engineering here (citation grounding, honest coverage
   assessment, token-budgeted context building that drops whole articles
   rather than truncating mid-chunk) is framework-agnostic and already
   good, per the earlier engineering-quality review.

3. **The gap LangGraph would close is specifically durability, and
   ADR-0005 already named the correct fix (Temporal) and just hasn't
   built it.** Swapping to LangGraph now would mean re-deciding an
   already-reasoned-through architecture choice under the pressure of
   "frameworks are popular in 2026," not because the ADR's original
   reasoning (Temporal for actual durable multi-step execution,
   Prefect/Celery for the rest) was wrong. The gap is execution against
   an existing decision, not the decision itself.

**The honest verdict**: if this were a greenfield agent build starting
today, LangGraph would be the reasonable default per current guidance —
it's not clearly wrong to have picked it. But sed.i isn't greenfield; it
has an existing Celery-based task infrastructure that the research
pipeline correctly reuses (the alternative — a parallel LangGraph runtime
alongside the Celery ingestion pipeline — is a worse shape, two
orchestration systems instead of one, for a codebase that already has one
documented cross-router inconsistency it hasn't resolved). **The actual
fix that matters is building Tier 3 (Temporal) per the already-written
ADR-0005**, not switching orchestration paradigms. The framework question
and the durability question look like the same question from outside but
aren't — sed.i has correctly answered "how do we get durability" (Temporal,
on paper) and just hasn't shipped the answer.

---

## 5. Is the LLM gateway a reasonable build-vs-buy call?

**Yes, and 2026 guidance explicitly supports the threshold sed.i used.**
LLM gateway products (Portkey, Helicone, TrueFoundry, LiteLLM-as-a-service)
exist precisely for what `LLMClient` does by hand: typed retry, provider
fallback, cost attribution, per-user budgets, uniform tracing. ADR-0003's
explicit re-evaluation trigger ("worth adding if we add 3+ providers") is
consistent with general build-vs-buy guidance that gateway products earn
their keep either at higher spend volume or higher provider-count
complexity than a 2-provider, low-volume, single-maintainer project has.
The `llm-gateway-hardening` work (now complete — typed retry-then-fallback,
Braintrust cost attribution across all 10 call sites including Bedrock,
Redis-backed per-user daily budget) closes the concrete gap that would
otherwise be the strongest argument *for* buying instead of building.
This is a case where "build it yourself" was the right call and was
executed to the point where the buy-vs-build gap has actually closed, not
just been argued away.

---

## 6. Stack summary table — component-by-component verdict

| Layer | Choice | SOTA verdict | Confidence |
|---|---|---|---|
| Vector storage | pgvector in Postgres | Correct at this scale — matches 2026 convergent guidance almost exactly | High |
| ANN indexing | HNSW planned, **not implemented** on the 3 tables that matter | **Not SOTA — it's a bug relative to the system's own documented decision**, not a stack choice problem | High |
| Chunking | Structure-aware + contextual prefix (Anthropic pattern) | SOTA, correctly implemented | High |
| Hybrid fusion | RRF (3 lanes) + weighted-sum entity lane bolted on | Directionally correct, technically impure — works, not textbook | Medium |
| Reranking | None | **Below SOTA bar** — 2026 guidance treats this as the single largest lever, already scoped as deferred (Layer 5) | High |
| Query routing | Regex classifier, no LLM | Matches "Adaptive RAG" pattern, cheaper than typical LLM-classifier implementations of the same idea | High |
| Agent orchestration | Hand-rolled Celery group/chord | Diverges from LangGraph-as-default 2026 guidance, but defensible given existing Celery infra + an already-correct durability plan (Temporal) that's unbuilt, not undecided | Medium — this is the most legitimately debatable call in the system |
| Durable execution | Planned (Temporal, ADR-0005), not built | Gap is real and the ADR already named the fix | High |
| LLM gateway | Hand-built (`LLMClient`) | Correct build-vs-buy call at current volume/provider-count, and now hardened | High |
| Data platform (Databricks-class) | Not used | Correctly not used — no source treats this as a peer option at sed.i's scale | High |

---

## 7. Bottom line

Answering the three framing questions directly:

**"Is this how a SOTA RAG product is built?"** Mostly yes, on the parts
that are hardest to get right (chunking, cost/retry infrastructure,
query routing, citation grounding) — and no, on one specific, fixable
part (reranking) that 2026 guidance is unusually unanimous about being
worth the cost.

**"Is it using the right tech stack?"** Yes, and this is worth stating
plainly: pgvector-over-dedicated-vector-DB-over-data-platform is not a
compromise sed.i settled for, it's the choice current production guidance
converges on for this scale, arrived at independently and for the same
reasons. The stack question has a clean answer. The **implementation**
of that stack has a bug — HNSW indexing was decided and not built on the
tables that need it.

**"Do people use Databricks instead of this?"** No — not at this scale,
and not for this reason. Databricks Vector Search is adopted for
lakehouse-governance and multi-team-data-platform reasons that don't
apply to a single-Postgres-instance product with one developer. Reaching
for it would repeat, at a much heavier weight, the exact mistake ADR-0001
already reasoned past when it rejected a dedicated vector DB.

**"Is the workflow solid?"** The ingestion workflow (Celery chains,
correct task ordering, real status surfacing) is solid. The research
workflow is solid in its domain logic (citation grounding, budget
tracking, resume support) and running on a documented, self-aware
durability shortcut (Celery instead of the planned Temporal tier) that's
fine at current volume and will not be fine indefinitely — this is a
"when," not an "if," and the ADR already says so.

**Priority order if closing gaps**: (1) build the HNSW/IVFFlat index on
`content_chunks.embedding`/`content_items.embedding`/`highlights.embedding`
— this is a one-migration fix for a decision that's already made and
already justified, not a new design decision; (2) add a reranker
(Cohere Rerank free tier is the lowest-effort path, per the existing
Layer 5 deferral) and measure the lift against the existing retrieval
eval harness before deciding whether it's worth keeping; (3) build
Temporal per ADR-0005, or explicitly re-scope the ADR if the team decides
Celery-plus-recovery-polling is an acceptable permanent state rather than
an interim one.

---

Sources consulted (WebSearch, July 2026):
- pgvector/vector-DB scale guidance — [zenvanriel.com](https://zenvanriel.com/ai-engineer-blog/pgvector-vs-dedicated-vector-db/), [devstarsj.github.io](https://devstarsj.github.io/2026/04/04/postgresql-pgvector-pgvectorscale-rag-production-guide-2026/), [KnowSync](https://www.knowsync.ai/blog/choosing-vector-database-qdrant-pinecone-pgvector-2026), [Encore](https://encore.dev/blog/you-probably-dont-need-a-vector-database)
- Databricks Mosaic AI positioning — [Startupik](https://startupik.com/when-should-you-use-databricks/), [Kanerika](https://kanerika.com/blogs/databricks-mosaic-ai/)
- Reranking/hybrid search production guidance — [DigitalApplied](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026), [AppScale](https://appscale.blog/en/blog/hybrid-search-and-reranking-production-rag-bm25-dense-cross-encoder-2026)
- Agent orchestration framework guidance — [LangChain](https://www.langchain.com/resources/ai-agent-frameworks), [gurusup.com](https://gurusup.com/blog/best-multi-agent-frameworks-2026)
