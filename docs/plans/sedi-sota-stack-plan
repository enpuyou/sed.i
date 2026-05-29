---
type: plan
status: active
last_updated: 2026-05-28
consumer: agent
---

# sed.i SOTA Stack & Build Plan

A reference document for extending sed.i toward the "Applied AI Engineer" senior bar (modeled on the Apple iCloud Data JD). Designed for: **infra that could support 1000 users, real cost at handful-of-users (mostly self), tools that real senior AI engineers use today.**

This doc is for Claude Code (or a human) to think with. Every layer has the chosen option, the alternatives considered, and the reasoning. Use it as the source of truth for decisions, not a prescriptive sequence.

---

## Table of Contents

1. [Guiding Principles](#guiding-principles)
2. [Current State Snapshot](#current-state-snapshot)
3. [Target State Stack — Layer by Layer](#target-state-stack--layer-by-layer)
4. [Phased Build Plan](#phased-build-plan)
5. [Agentic Features Plan](#agentic-features-plan)
6. [Separate Data-Infra Project](#separate-data-infra-project)
7. [Decision Logs to Maintain](#decision-logs-to-maintain)
8. [Cost Discipline Rules](#cost-discipline-rules)
9. [JD-to-Capability Mapping](#jd-to-capability-mapping)
10. [Interview Narrative Targets](#interview-narrative-targets)

---

## Guiding Principles

1. **Build like 1000 users, pay for ~5.** Architecture should scale; cost should not.
2. **Defensible choices over tool quantity.** One tool per category, picked deliberately. The talking point is *"I evaluated X vs Y, chose X because of [tradeoff]"*, not *"I use X, Y, and Z."*
3. **Evals gate decisions.** No prompt/model/retrieval change ships without a measured impact on the golden set.
4. **Observability is non-negotiable.** Every LLM call, every retrieval, every agent step is traced with cost, latency, and outcome.
5. **OSS and free tiers first.** Pay-per-use beats fixed minimums at this scale. Avoid tools with monthly floors unless they pay back in learning.
6. **Document tradeoffs in the repo.** Architecture decisions live in `docs/decisions/` as ADRs.

---

## Current State Snapshot

- **Frontend:** Next.js on Vercel
- **Backend:** FastAPI on Railway
- **DB:** Postgres + pgvector on Railway
- **Cache/Queue broker:** Redis on Railway
- **Async:** Celery workers
- **Browser extension:** Manifest V3
- **MCP server:** FastMCP with OAuth 2.1 + PKCE, JWT issuance, Redis for auth codes
- **ML:** OpenAI embeddings; local YOLO for PDF layout
- **Auth:** Custom JWT for sed.i, OAuth flow for MCP

**Already strong:** end-to-end deployment, OAuth, semantic search, MCP, ~80K LOC.

**Missing for the senior bar:** evals, LLM observability, retrieval quality measurement, agent architecture, infra-as-code, model selection logic, real workflow orchestration.

---

## Target State Stack — Layer by Layer

For each layer: **Pick**, **Alternatives considered**, **Why this pick**, **Cost at low usage**, **What it teaches**.

### 1. Application Runtime

**Pick:** Stay on Railway (backend) + Vercel (frontend).
**Alternatives:** Fly.io, Render, AWS App Runner, ECS Fargate, full EKS.
**Why:** No scaling problem at 1000 users; moving to AWS just for the story would be contrived. The senior answer is *"I chose managed PaaS because operational complexity wasn't justified at my scale."*
**Cost:** ~$25–50/month combined.
**Learning:** None new; you already know this. Focus learning budget elsewhere.

### 2. Primary Database

**Pick:** Postgres on Railway (or migrate to Neon for branching).
**Alternatives:** Supabase, RDS, Aurora Serverless v2, PlanetScale (MySQL).
**Why:** Postgres is genuinely the right tool. Consider Neon if you want database branching for eval experiments — each PR gets its own DB branch with seeded data.
**Cost:** $5–20/month.
**Learning:** Connection pooling (PgBouncer), read replicas, query plan reading.

### 3. Vector Storage

**Pick:** pgvector with HNSW indexing + hybrid search (vector + tsvector via RRF).
**Alternatives:** Qdrant (self-host or cloud), Pinecone, Weaviate, Chroma, Milvus, LanceDB.
**Why:** At <10M vectors, pgvector with HNSW gives sub-100ms p95. Managed vector DBs introduce dual-write problems, eventual consistency, and $70+/month floors. The senior answer: *"I chose pgvector to keep data in one place; my migration trigger is [N] vectors or [specific feature need]."*
**Cost:** $0 incremental.
**Learning:** HNSW parameters (m, ef_construction, ef_search), recall-vs-latency tradeoffs.

### 4. Object Storage

**Pick:** S3 (us-west-2 to match your location for low latency).
**Alternatives:** Cloudflare R2 (cheaper egress, no AWS skill story), GCS, Backblaze B2.
**Why:** AWS skill story matters for FDE/Applied AI roles. S3 is the canonical answer to "where do you store blobs?" Use lifecycle policies (Standard → IA → Glacier) for old PDFs.
**Cost:** $1–5/month for personal scale.
**Learning:** IAM policies, presigned URLs, lifecycle rules, encryption at rest (SSE-S3 vs SSE-KMS), event notifications (S3 → Lambda).

### 5. Async Pipeline (Batch / Ingestion)

**Pick:** Prefect 3 (self-hosted OSS or Prefect Cloud free tier).
**Alternatives:** Keep Celery, Airflow, Dagster, Temporal (covered separately for agents), Inngest.
**Why:** Celery is fine for fire-and-forget, bad for multi-step DAGs with debugging. Prefect gives DAG visibility, retries with backoff, observable failures. Airflow is heavier and Python-versioned-y; Dagster is great but learning curve is steeper.
**Cost:** $0 self-hosted; Prefect Cloud free tier covers personal scale.
**Learning:** DAG composition, task retries, conditional flows, agent-based work pools.

### 6. Agent Workflow Engine (NEW)

**Pick:** Temporal (self-hosted via `docker-compose` or Temporal Cloud dev tier).
**Alternatives:** Inngest, restate.dev, raw Celery chains, custom state machine.
**Why:** Long-running agent workflows (research agent: 30s–5min, multiple tool calls, retries) need durable execution. Temporal is what OpenAI, Stripe, and other serious AI infrastructure teams use. Prefect can technically do this; Temporal is purpose-built for it.
**Cost:** $0 self-hosted (lightweight Docker stack); ~$0 on dev tier.
**Learning:** Workflows vs activities, durable execution, signal handling, child workflows. **High-leverage learning** — this is core senior agent-system knowledge.

### 7. LLM Observability + Evals

**Pick:** Braintrust (free tier).
**Alternatives:** Langfuse (OSS, more general observability), Arize Phoenix (OSS, eval-focused), Helicone (proxy-based, lighter), LangSmith (LangChain-coupled).
**Why:** Braintrust is what AI-forward companies (Notion, Vercel, Stripe, Coinbase) use. Eval-centric workflow matches the Apple JD's emphasis on "evaluation harnesses that change decisions." Free tier covers personal use.
**Cost:** $0 at handful-of-users scale.
**Learning:** Eval datasets, experiments, scorers (heuristic + LLM-as-judge), prompt playgrounds, side-by-side experiment comparison.

**Optional alternative if self-host is important to you:** Use Arize Phoenix for traces + Braintrust for evals. Slightly more setup, fully OSS option.

### 8. LLM Provider Routing

**Pick:** Provider abstraction with **Bedrock primary, OpenAI as fallback/comparison**.
**Alternatives:** OpenAI-only, Anthropic API direct, LiteLLM as routing layer, Portkey.
**Why:** Bedrock is the FDE-critical surface. Provider abstraction enables failover and cost-arbitrage (Haiku via Bedrock for tagging, Sonnet for synthesis). Consider LiteLLM as a thin routing library if you want OpenAI-compatible interface across providers.
**Cost:** $5–20/month depending on usage. Set AWS Budget alarm at $20.
**Learning:** Bedrock IAM, cross-region inference, model IDs, prompt caching, Batch API for evals, the Converse API.

**Model selection policy** (encode in code):

| Task | Default Model | Provider | Reasoning |
|---|---|---|---|
| Content tagging / classification | Haiku 4.5 | Bedrock | Cheap, fast, sufficient quality |
| Embeddings | Titan v2 or OpenAI text-embedding-3-small | Configurable | A/B-testable |
| MCP synthesis | Sonnet 4.6 | Bedrock or Anthropic | Quality-critical |
| Research agent planning | Sonnet 4.6 | Bedrock | Reasoning quality matters |
| Research agent reading/extraction | Haiku 4.5 | Bedrock | Volume, simple extraction |
| LLM-as-judge for evals | Sonnet 4.6 via Batch API | Bedrock | 50% discount, latency OK |
| "Smartest" escape hatch | Opus 4.7 | Anthropic | Rarely used, gated |

### 9. Distributed Tracing / Infra Observability

**Pick:** OpenTelemetry SDK → Grafana Cloud (free tier: 10K series, 50GB logs, 50GB traces).
**Alternatives:** Datadog, Honeycomb, New Relic, self-hosted Grafana stack, AWS CloudWatch alone.
**Why:** OTEL is the actual industry standard. Instrument once, swap backends without code changes. Grafana Cloud free tier is genuinely generous.
**Cost:** $0.
**Learning:** OTEL spans, attributes, context propagation, semantic conventions, PromQL/LogQL.

### 10. Error Tracking

**Pick:** Sentry (free tier: 5K errors, 10K performance events).
**Alternatives:** Rollbar, Bugsnag, Highlight.io (OSS).
**Why:** Sentry is the default at most companies. Free tier covers personal usage.
**Cost:** $0.
**Learning:** Error grouping, performance monitoring, release tracking, source maps.

### 11. Feature Flags + Analytics

**Pick:** PostHog (free tier: 1M events/month, includes flags + session replay + analytics).
**Alternatives:** LaunchDarkly, Unleash (OSS), Flagsmith, Statsig.
**Why:** Real production AI systems gate prompt versions and model changes behind flags. PostHog combines flags, product analytics, and session replay in a generous free tier.
**Cost:** $0.
**Learning:** Flag rollout strategies, A/B test design, event taxonomy.

**Concrete use cases for sed.i:**
- Flag `retrieval.hybrid_search_enabled` → progressive rollout of hybrid search
- Flag `agent.research_agent_v2` → A/B test research agent variants
- Flag `model.tagging_provider` → swap between Haiku/Nova for tagging

### 12. CI/CD

**Pick:** GitHub Actions.
**Alternatives:** CircleCI, Buildkite, GitLab CI.
**Why:** Free for personal repos, integrates with everything.
**Cost:** $0.
**Required workflows:**
- Unit tests on every PR
- Eval suite on PRs touching `prompts/`, `retrieval/`, `agents/`
- Lint + type check
- Deploy preview to Railway/Vercel
- Pulumi preview on infra changes

### 13. Infrastructure as Code

**Pick:** Pulumi (Python, individual tier free) OR OpenTofu (Terraform fork, fully OSS).
**Alternatives:** Terraform proper, AWS CDK, Crossplane, manual console clicks (don't).
**Why:** Pulumi-in-Python keeps everything in one language. OpenTofu if you want the more universally recognized HCL syntax.
**Cost:** $0.
**Learning:** State management, drift detection, stack outputs.

**Scope for sed.i:** Codify the AWS resources you create — S3 buckets, IAM roles, Lambda functions, Bedrock policies. Don't try to codify Railway/Vercel (use their own UI/CLI).

### 14. Secrets Management

**Pick:** Doppler (free for individuals, 5 projects) OR AWS Secrets Manager for AWS resources.
**Alternatives:** Infisical (OSS), 1Password CLI, HashiCorp Vault, raw .env files (don't).
**Why:** Doppler has the cleanest dev UX and free tier. Use AWS Secrets Manager for things that AWS services need to access (Lambda, ECS).
**Cost:** $0.

### 15. Compute for Bursty ML Workloads

**Pick:** Modal (serverless GPU + CPU).
**Alternatives:** Replicate, RunPod, AWS Lambda (CPU only), Banana, Beam.
**Why:** YOLO PDF processing and (future) cross-encoder reranking are bursty workloads — long idle, occasional load. Serverless GPU is the right pattern. Modal has the best DX.
**Cost:** $0–10/month at handful-of-users; pennies per invocation.
**Learning:** Serverless GPU patterns, cold start mitigation, function-as-deployable-unit.

### 16. Reranker

**Pick:** `bge-reranker-base` deployed on Modal OR Cohere Rerank API.
**Alternatives:** Self-host on Railway (slow), Voyage rerank, Jina reranker.
**Why:** Cross-encoder reranking is the standard "beyond vanilla RAG" upgrade. `bge-reranker-base` is small (~280MB), free, and runs fast on CPU.
**Cost:** Pennies per use on Modal; or Cohere's free tier (1000 calls/month).

### 17. Cache Layer

**Pick:** Redis (already have it) + extend usage.
**Use cases:**
- Embedding cache: hash(text) → embedding (avoid re-embedding identical text)
- LLM response cache for deterministic prompts (e.g., tagging)
- Rate limiting (per-user quotas)
- Session state for agent workflows (or use Temporal's state)
**Cost:** Already paying.
**Learning:** Cache invalidation strategies, TTL design, Redis data structures beyond strings.

### 18. CDN / Edge

**Pick:** Cloudflare (free tier) in front of sed.i.
**Alternatives:** Just use Vercel's CDN (already there).
**Why:** Cloudflare adds DDoS protection, WAF rules, edge caching, and you get to learn it.
**Cost:** $0.
**Learning:** Edge functions, page rules, security rules.

### 19. Auth (existing, expand)

**Pick:** Keep custom JWT + OAuth 2.1 + PKCE for MCP. Add rate limiting and quotas.
**Don't:** Rip out for Clerk/Auth0/Supabase Auth — you already built it and it's a strength.
**Add:**
- Per-user quotas (requests/min, tokens/day)
- API keys for programmatic access (separate from user JWT)
- Audit log of sensitive actions

### 20. Documentation

**Pick:** ADRs (Architecture Decision Records) in `docs/decisions/` as numbered markdown files.
**Format:** Title, status, context, decision, consequences. One per significant choice.
**Why:** This *is* the senior signal. Interviewers love seeing real ADRs with documented tradeoffs.

---

## Phased Build Plan

Sequenced for highest JD-leverage per hour of work. Estimate ~10–15 hours/week; 12–14 weeks total to complete everything below. **Start interviewing after Phase 3.**

### Phase 1: Observability Foundation (Weeks 1–3)

**Goal:** Every LLM call traced, cost-attributed, latency-measured. Eval harness running.

**Week 1: OpenTelemetry + Sentry + Braintrust setup**
- [ ] Install OTEL SDK in FastAPI; instrument all routes
- [ ] OTEL exporter → Grafana Cloud free tier
- [ ] Sentry SDK integrated, source maps wired up
- [ ] Braintrust account; install SDK
- [ ] Wrap every OpenAI call with Braintrust `wrap_openai`
- [ ] Build first Grafana dashboard: request rate, latency p50/p95/p99, error rate

**Week 2: Eval harness**
- [ ] Create `/evals` directory structure: `retrieval/`, `mcp/`, `tagging/`
- [ ] Write 30 retrieval examples by hand (use your real usage)
- [ ] Write 15 MCP examples (user message → expected tool calls)
- [ ] Write 20 tagging examples (article → expected tags)
- [ ] Braintrust dataset uploads for each
- [ ] pytest-based runner: `pytest tests/evals/` runs all evals
- [ ] GitHub Actions: on PR touching prompts/retrieval/agents, run evals, post comment

**Week 3: LLM-as-judge + first ADRs**
- [ ] LLM-as-judge scorer for tagging (rubric: relevance, completeness, format)
- [ ] LLM-as-judge for MCP synthesis (rubric: faithfulness, citation accuracy)
- [ ] Calibrate judges with 10 manual-scored examples each
- [ ] Write ADRs: choice of Braintrust, choice of OTEL, choice of eval methodology
- [ ] Set baseline metrics for everything

**JD bullets addressed:** evaluation harnesses, golden sets, LLM-as-judge, telemetry that changes decisions, observability.

### Phase 2: Retrieval Quality (Weeks 4–5)

**Goal:** "Moved beyond vanilla RAG" with measurable lifts.

**Week 4: Hybrid search**
- [ ] Add `tsvector` column with weighted fields (title=A, body=B)
- [ ] GIN index on tsvector
- [ ] Implement parallel vector + keyword query
- [ ] RRF fusion (k=60 default, make configurable)
- [ ] Run retrieval evals: baseline vs hybrid
- [ ] Tune field weights, RRF k against eval set
- [ ] Document lift in ADR

**Week 5: Reranker**
- [ ] Deploy `bge-reranker-base` on Modal
- [ ] Add reranking stage: top 20 hybrid → top 5 reranked
- [ ] Run retrieval evals: hybrid vs hybrid+reranked
- [ ] Latency budget analysis (reranker adds ~200ms; acceptable?)
- [ ] Feature-flag the reranker via PostHog
- [ ] Document final 3-stage pipeline architecture

**JD bullets:** moved beyond vanilla RAG, reranking, custom retrieval models.

### Phase 3: AWS + Bedrock + Model Selection (Weeks 6–7)

**Goal:** Production-grade LLM routing, cost control, AWS skill story.

**Week 6: S3 + Bedrock + Pulumi**
- [ ] Pulumi project for AWS resources
- [ ] S3 bucket(s) for PDFs/images with lifecycle policies
- [ ] Migrate PDF/image storage from current location to S3
- [ ] Presigned URL serving for protected content
- [ ] Bedrock IAM roles configured via Pulumi
- [ ] Provider abstraction: `LLMProvider` interface with OpenAI + Bedrock impls
- [ ] AWS Budgets alarm at $20

**Week 7: Model selection + cost dashboard**
- [ ] Implement `select_model(task_type, priority)` per the table above
- [ ] Prompt caching for MCP system prompt (Bedrock)
- [ ] Batch API for nightly eval runs (50% off)
- [ ] Cost dashboard: weekly token spend by task type + model, surfaced in admin view
- [ ] Provider failover: if primary errors, retry on secondary with backoff
- [ ] ADR: provider strategy, model selection policy

**JD bullets:** model selection, token economics, caching, batching, graceful degradation, cost-performance-quality optimization.

### Phase 4: Research Agent (Weeks 8–10)

**Goal:** Real multi-step agent with planner-executor, durable execution, evals.

See [Agentic Features Plan](#agentic-features-plan) below for full architecture.

**Week 8: Temporal setup + planner**
- [ ] Self-host Temporal via docker-compose
- [ ] First workflow: trivial "echo" to validate setup
- [ ] Planner LLM call: question → structured plan (Pydantic-validated JSON)
- [ ] Plan schema: sub-questions, retrieval strategies, expected output structure

**Week 9: Executor + tools**
- [ ] Tool layer (typed Pydantic interfaces): `retrieve`, `read_content`, `web_search` (optional), `summarize_section`
- [ ] Temporal activities for each tool
- [ ] Executor workflow: iterate over plan steps, invoke tools, accumulate state
- [ ] Bounded iteration: max steps, max tokens, max wall-clock
- [ ] Failure handling: tool error → re-plan or graceful degrade

**Week 10: Synthesis + evals + UI**
- [ ] Synthesizer: produce cited markdown summary
- [ ] File synthesis back as new content item (writing back-link)
- [ ] Cost attribution per agent run (Braintrust + custom display)
- [ ] Eval set: 15 research questions with expected source IDs and key claims
- [ ] LLM-as-judge for citation faithfulness
- [ ] Minimal UI in sed.i for invoking research agent

**JD bullets:** agents, agentic architectures, tool invocation, stateful reasoning, multi-step reasoning orchestration, graceful degradation.

### Phase 5: Workflow Orchestration + Text-to-SQL (Weeks 11–12)

**Goal:** Pipeline orchestration discipline + natural-language data interface.

**Week 11: Prefect migration**
- [ ] Wrap ingestion as Prefect flow: fetch → extract → enrich → embed → store → tag
- [ ] Retry policies per task
- [ ] Prefect Cloud (free) or self-host
- [ ] Keep Celery for in-request work (extension callbacks, etc.)
- [ ] ADR: Prefect vs Celery vs Temporal — different problems, different tools

**Week 12: Text-to-SQL**
- [ ] Schema introspection → schema-aware system prompt
- [ ] User query → SQL via Sonnet
- [ ] Read-only execution with timeout
- [ ] Query validation: AST check, no DDL/DML, allow-list of tables
- [ ] Result formatting back to natural language
- [ ] Add as MCP tool: `query_library`
- [ ] Eval set: NL question → expected SQL (15 examples)

**JD bullets:** workflow orchestration, natural-language interfaces over data, text-to-SQL.

### Phase 6: Polish + Documentation (Weeks 13–14)

- [ ] All ADRs finalized
- [ ] README with architecture diagram
- [ ] Public write-up of the journey (LinkedIn post or blog)
- [ ] Demo video (3–5 min) of the research agent in action
- [ ] Interview-ready one-pager summarizing the system

---

## Agentic Features Plan

Three candidate agentic features for sed.i, in priority order.

### Primary: Research Agent (BUILD THIS)

**User-facing pitch:** Ask a research question. The agent explores your library, identifies what you've read on the topic, finds gaps, optionally searches the web, and produces a cited synthesis filed back as a content item.

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│  Temporal Workflow: research_agent                          │
│                                                             │
│  Input: question, depth_budget, source_filter               │
│                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐  │
│  │  Planner    │───▶│  Executor    │───▶│  Synthesizer   │  │
│  │  (Sonnet)   │    │  (loop)      │    │  (Sonnet)      │  │
│  └─────────────┘    └──────────────┘    └────────────────┘  │
│         │                  │                     │          │
│         ▼                  ▼                     ▼          │
│    structured plan    tool invocations     cited markdown   │
│    (sub-questions)    (activities)         → new content    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                  ┌──────────────────────┐
                  │  Temporal Activities │
                  │  ┌────────────────┐  │
                  │  │ retrieve()     │  │  hybrid + rerank
                  │  │ read_content() │  │  full text fetch
                  │  │ extract_claims │  │  Haiku
                  │  │ web_search()   │  │  optional, gated
                  │  │ check_coverage │  │  did we answer?
                  │  └────────────────┘  │
                  └──────────────────────┘
```

**Key design decisions:**
- **Explicit planner-executor split.** Planner produces JSON plan; executor follows it. Allows replanning on failure.
- **Typed tool interfaces.** Pydantic schemas; outputs validated.
- **Durable execution via Temporal.** Survives restarts, retries idempotently.
- **State persisted in workflow.** Not just in LLM context — Temporal stores intermediate results.
- **Bounded iteration.** Hard limits on steps (default 10), total tokens (default 100K), wall-clock (default 5min).
- **Cost attribution per run.** Display total cost when synthesis is filed.
- **Eval-driven.** Golden set of (question, expected_sources, expected_claims). LLM-as-judge for citation faithfulness.

**Evaluation rubric (for LLM-as-judge):**
- Citation faithfulness: do cited claims actually appear in source?
- Source coverage: did the agent find the expected sources?
- Synthesis quality: is the output coherent and useful?
- Cost efficiency: was the agent efficient with tool calls and tokens?

### Secondary: Reading Companion (CONSIDER LATER)

While reading an article, ambient agent surfaces: related items in your library, comprehension questions, potential contradictions with prior highlights.

Genuinely multi-agent (orchestrator + connection + question + contradiction agents). Harder to evaluate and product-design. Build only after research agent is solid.

### Tertiary: Writing Agent (PARTIAL FIT)

Slash commands invoke specialized sub-agents during writing: `/source`, `/outline`, `/cite`, `/critique`.

Aligns with your writing-system roadmap. More "structured tool use" than "true agent." Build as part of the writing system phase, not as a standalone agent project.

---

## Separate Data-Infra Project

**Not sed.i.** A focused weekend project to close the Spark/lakehouse gap.

**Goal:** Hands-on Databricks + PySpark + Bedrock RAG over a public dataset.

**Scope:**
- Databricks Community Edition (free)
- Load a chunk of Common Crawl, HN comments dump, or Reddit Pushshift archive
- PySpark pipeline: clean → chunk → embed → store
- Vector store options: Databricks Vector Search (free in CE) or load embeddings back to local pgvector
- Bedrock-powered Q&A over the dataset
- Public write-up on LinkedIn or your blog

**Time budget:** 1–2 focused weekends.

**Interview talking point:** *"For lakehouse-native AI experience separate from sed.i, I built a PySpark pipeline over [dataset] that chunks, embeds, and indexes for RAG using Bedrock. The reason I didn't try to bolt this onto sed.i is that sed.i is OLTP — small per-user reads/writes — and lakehouse architectures are designed for OLAP at scale. They're solving different problems."*

That last sentence is what shows the JD reviewer you understand the architectural distinction.

---

## Decision Logs to Maintain

Create `docs/decisions/` with these ADRs:

1. **0001-vector-storage.md** — pgvector vs managed vector DB
2. **0002-observability-stack.md** — Braintrust + OTEL + Grafana + Sentry
3. **0003-llm-provider-strategy.md** — Bedrock primary, OpenAI fallback, model selection policy
4. **0004-async-orchestration.md** — Celery vs Prefect vs Temporal (different tools, different problems)
5. **0005-retrieval-architecture.md** — Hybrid search + reranker rationale
6. **0006-agent-architecture.md** — Planner-executor split, Temporal for durability
7. **0007-infra-as-code.md** — Pulumi for AWS, manual for PaaS
8. **0008-eval-methodology.md** — Golden sets, LLM-as-judge, behavioral regression
9. **0009-deployment-strategy.md** — Railway + Vercel + AWS (S3, Bedrock, Lambda)
10. **0010-feature-flag-strategy.md** — PostHog, what gets flagged, rollout patterns

Each ADR should answer: what problem, what alternatives, what was chosen, what tradeoffs, what would change the decision.

---

## Cost Discipline Rules

1. **AWS Budgets alarm at $20/month.** Set this *before* creating any AWS resources.
2. **No tools with monthly minimums** unless they pay back massively in learning (Temporal self-hosted = $0, Temporal Cloud minimum = avoid for now).
3. **Tear down spike work the day you finish it.** OpenSearch Serverless, SageMaker endpoints, anything that bills 24/7.
4. **Default to Haiku.** Only use Sonnet/Opus when evals show it's needed.
5. **Use Batch API for evals.** 50% off, latency doesn't matter.
6. **Prompt caching for repeated context.** MCP system prompt, agent planner prompts.
7. **Cache embeddings.** Hash input text, store embedding by hash.
8. **Rate limit yourself.** Even in dev — a runaway loop on Opus can cost $50 in minutes.

**Target steady-state cost at handful-of-users: $30–60/month.**

| Category | Tool | Cost |
|---|---|---|
| Hosting | Railway + Vercel | $25–50 |
| Storage | S3 | $1–5 |
| LLM tokens | Bedrock (mostly Haiku) | $5–20 |
| GPU compute | Modal | $0–5 |
| Observability | Braintrust + Grafana free + Sentry free | $0 |
| Analytics/flags | PostHog free | $0 |
| Orchestration | Prefect OSS + Temporal self-host | $0 |
| Secrets | Doppler free | $0 |
| Infra-as-code | Pulumi free | $0 |
| Domain, misc | | $5 |

---

## JD-to-Capability Mapping

Cross-reference of Apple Applied AI Engineer JD bullets to sed.i capabilities after this plan.

| JD Requirement | sed.i Capability After Plan |
|---|---|
| Architect, build, operate production-grade AI products | sed.i end-to-end, ~80K LOC, real deployment |
| LLMs, foundation models, agents, deterministic components | MCP (LLM tools) + research agent (agent) + extraction pipeline (deterministic) |
| Both human and machine consumption | Next.js UI (human) + MCP server (machine) |
| Inference-vs-compute boundaries, task decomposition | YOLO (compute) → extraction (deterministic) → embeddings (LLM) → tagging (Haiku) → synthesis (Sonnet) |
| Orchestration of multi-step reasoning and tool use | Temporal-backed research agent with planner-executor |
| Graceful degradation under failure | Provider failover, bounded iteration, fallback paths |
| Modern AI stack fluency | Bedrock, OpenAI, Braintrust, OTEL, Modal, Temporal, Prefect |
| Vector databases | pgvector with HNSW, hybrid search, reranking |
| Moved beyond vanilla RAG | Hybrid search + cross-encoder reranker + measured lifts |
| Evaluation harnesses, golden sets, LLM-as-judge | Phase 1 deliverables |
| Behavioral regression, drift monitoring | Eval-on-PR gate, dashboarded eval metrics over time |
| Cost, latency, throughput optimization | Cost dashboard, model selection policy, prompt caching, Batch API |
| Caching, batching, streaming | Embedding cache, Batch evals, streaming MCP responses |
| Workflow orchestration | Prefect for batch, Temporal for agents (two tools, defended choice) |
| Natural-language interfaces over data, text-to-SQL | Phase 5 deliverable |
| Cloud platforms (AWS) | S3, Bedrock, Lambda, IAM, Budgets, all in Pulumi |
| Streaming systems (Flink, Spark Streaming) | **GAP** — conversant only |
| SQL engines (Trino, Presto, Spark) | **PARTIAL** — separate data-infra project covers Spark basics |
| Lakehouse architectures | **PARTIAL** — Databricks weekend project |
| MLOps / LLMOps | Full coverage — observability, evals, versioning via PostHog flags |
| Fine-tuning, custom embeddings/rerankers | **GAP** — could add small experiment if time permits |

**Coverage: ~80% strong, ~15% partial, ~5% gap.** That's senior-shaped for any role in this class, including most Apple ICT3 versions of this posting.

---

## Interview Narrative Targets

After completing Phases 1–5, you should be able to deliver these soundbites naturally:

**On observability:**
> "Every LLM call in sed.i is traced through Braintrust with cost attribution and latency. I use OpenTelemetry for infra-level tracing into Grafana Cloud. Sentry handles error tracking. I can pull up p99 latency on the MCP server or token spend by feature for any week."

**On evals:**
> "I don't ship a prompt or model change without running the eval suite. Golden set of 30 retrieval queries, 15 MCP behavioral tests, 20 tagging examples — all gated in CI. For open-ended outputs like research-agent syntheses, I use LLM-as-judge calibrated against a manual-scored sample."

**On retrieval:**
> "sed.i runs hybrid retrieval — vector via pgvector HNSW, keyword via Postgres tsvector, fused with RRF. Top 20 results go through a bge-reranker-base cross-encoder on Modal. Recall@5 went from [baseline] to [improved] on my eval set."

**On agents:**
> "The research agent is built on Temporal for durable execution. Planner-executor split: Sonnet produces a structured JSON plan, the executor runs it through typed tool interfaces with bounded iteration. State persists in the workflow, not just in context. Every run has cost attribution and is evaluated for citation faithfulness."

**On model selection:**
> "Provider abstraction across Bedrock and OpenAI. Tagging and classification go to Haiku because evals showed sufficient quality at 1/15th the cost. Synthesis goes to Sonnet. Opus is gated. Prompt caching on the MCP system prompt cut my MCP token costs by [X]%. Batch API for nightly evals at 50% off."

**On architecture choices:**
> "I evaluated managed vector DBs but stayed on pgvector — at <10M vectors the operational complexity wasn't justified, and dual-write to a separate store is a real failure mode. My migration trigger is documented in the ADR."

**On the data-infra gap:**
> "sed.i is OLTP, so I didn't try to bolt Spark onto it. Separately I built a PySpark + Bedrock pipeline over [public dataset] on Databricks to get hands-on with lakehouse-native AI patterns."

---

## What to Hand to Claude Code

Suggested first prompts after sharing this doc with Claude Code:

1. *"Read this plan. Inventory what already exists in sed.i and identify which Phase 1 items can start immediately. Propose a concrete Week 1 PR plan."*
2. *"Draft ADR-0002 (observability stack) based on this plan and current sed.i code."*
3. *"Generate the directory structure for `/evals` and the pytest harness skeleton."*
4. *"Implement the OpenTelemetry instrumentation in the FastAPI app per Phase 1 Week 1. Don't change app logic; just add tracing."*

Treat each phase as a sequence of small PRs, not one giant change. Every PR should leave sed.i shippable.
