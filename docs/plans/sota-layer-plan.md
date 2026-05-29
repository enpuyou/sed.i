---
type: plan
status: active
last_updated: 2026-05-28
consumer: agent
---

# sed.i SOTA Upgrade — Layer-by-Layer Build Plan

Each layer is a self-contained PR or pair of PRs. Complete one layer before starting the next.
For tool/service decisions marked **[decide when ready]**, do a short research spike at that layer rather than committing now.

Reference doc with full rationale for each choice: [sedi-sota-stack-plan](sedi-sota-stack-plan)

---

## Status

| Layer | Name | Status |
|---|---|---|
| 0 | LLMClient interface + ADRs | ✓ Done |
| 1 | Braintrust LLM observability | ✓ Done (bundled with Layer 0) |
| 2 | OTEL + Sentry + Grafana | ✓ Done |
| 3 | Eval harness | ✓ Done |
| 4 | Bedrock + provider abstraction | ✓ Done |
| 5 | Reranker | ⏭ Deferred (decision: revisit when retrieval quality is the bottleneck) |
| 6 | S3 + object storage | ✓ Done |
| 7 | Temporal + research agent | ⏭ Deferred (largest layer — tackle after Layer 9) |
| 8 | Prefect + pipeline observability | ✓ Done |
| 9 | Text-to-SQL MCP tool | ✓ Done |
| 10 | Secrets + docs polish | ☐ Not started |

---

## Layer 0 — LLMClient interface + ADRs

**Goal:** Single entry point for all LLM calls. Zero behavior change — pure refactor.
**Why first:** Every later layer (tracing, provider swap, cost attribution) is one-file work instead of four-file hunts.

### Tasks

- [ ] Create `docs/decisions/` directory
- [ ] Write `docs/decisions/0001-vector-storage.md` (defend pgvector, note migration trigger)
- [ ] Write `docs/decisions/0002-observability-stack.md` (OTEL + Braintrust + Sentry, alternatives considered)
- [ ] Create `app/core/llm_client.py` — typed wrapper around `openai.AsyncOpenAI` with methods: `embed()`, `tag()`, `summarize()`, `chat()`
- [ ] Refactor `app/tasks/embedding.py` → use `LLMClient.embed()`
- [ ] Refactor `app/tasks/tagging.py` → use `LLMClient.tag()`
- [ ] Refactor `app/tasks/summarization.py` → use `LLMClient.summarize()`
- [ ] Refactor `app/mcp/tools/summarize.py` → use `LLMClient.chat()`
- [ ] All existing tests green

**`LLMClient` design note:** takes `provider: str` config key. OpenAI now, Bedrock in Layer 4 — no call-site changes needed when swapping.

**ADR template:**
```
# ADR-XXXX: <title>
Status: Accepted
Context: <what problem, what constraints>
Decision: <what was chosen>
Alternatives considered: <what else was evaluated, why rejected>
Consequences: <tradeoffs, what would change this decision>
```

---

## Layer 1 — Braintrust LLM observability

**Goal:** Every LLM call traced with cost, latency, input/output logged to Braintrust.

### Tasks

- [ ] Add `braintrust` to `pyproject.toml`
- [ ] Wrap `LLMClient` internals with `braintrust.wrap_openai`
- [ ] Log per call: task type, model, prompt tokens, completion tokens, latency, user_id
- [ ] Seed Braintrust datasets: 5 real tagging examples, 5 real retrieval queries from your library
- [ ] Verify traces appear in Braintrust UI

**Deferred to Layer 3:** eval scoring, LLM-as-judge — just tracing here.

---

## Layer 2 — OTEL + Sentry + Grafana

**Goal:** Every HTTP request traced; errors tracked in both frontend and backend.

### Tasks

- [ ] Add `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-exporter-otlp` to `pyproject.toml`
- [ ] Instrument FastAPI in `app/main.py` — add OTEL middleware
- [ ] Start with console exporter; verify spans locally
- [ ] Wire OTEL exporter → Grafana Cloud free tier
- [ ] Add Sentry SDK to FastAPI (`sentry-sdk[fastapi]`)
- [ ] Add Sentry SDK to Next.js frontend
- [ ] Build first Grafana dashboard: request rate, p50/p95/p99 latency, error rate, Celery queue depth

**[Decide when ready]:** Grafana Cloud vs Honeycomb vs Axiom — spend 30 min comparing before wiring the exporter. Grafana is the plan default; Honeycomb has better DX for traces specifically.

---

## Layer 3 — Eval harness

**Goal:** Measurable quality gates. The layer that separates "I built a RAG app" from "I measured it."

### Tasks

- [ ] Create `evals/` directory: `retrieval/`, `tagging/`, `mcp/`
- [ ] Hand-write 15 retrieval examples from your real library (query + expected source IDs)
- [ ] Hand-write 10 tagging examples (article → expected tags)
- [ ] Hand-write 5 MCP behavioral tests (user message → expected tool calls)
- [ ] pytest runner: `pytest tests/evals/` runs all three sets
- [ ] Upload datasets to Braintrust
- [ ] LLM-as-judge scorer for tagging (rubric: relevance, completeness, format)
- [ ] Calibrate judge with 5 manually-scored examples
- [ ] Record baseline metrics for all three sets
- [ ] GitHub Actions: eval gate on PRs touching `prompts/`, `tasks/tagging.py`, `core/hybrid_search.py`

**Note:** Hybrid search already exists in `core/hybrid_search.py`. These evals measure it for the first time — run baseline before any changes.

---

## Layer 4 — Bedrock + provider abstraction

**Goal:** `LLMClient` backed by Bedrock. AWS skill story. Cost-optimized model selection.

### Tasks

- [ ] AWS account setup, enable Bedrock model access (Claude Haiku 4.5, Sonnet 4.6)
- [ ] Set AWS Budgets alarm at **$20/month — do this before any API calls**
- [ ] Pulumi project: `infra/` directory, AWS provider, IAM role + Bedrock policy
- [ ] Implement `BedrockProvider` in `LLMClient` via `boto3` `bedrock-runtime`
- [ ] Add `provider` config key: `"openai"` (default) or `"bedrock"`
- [ ] Model selection policy in config (not hardcoded): Haiku for tagging/embed, Sonnet for synthesis/MCP
- [ ] Enable prompt caching on MCP system prompt (Bedrock supports this natively)
- [ ] Provider failover: if primary errors, retry on secondary with backoff
- [ ] Run Layer 3 evals against both providers; record delta
- [ ] Write ADR-0003: provider strategy, model selection policy, cost comparison

**[Verify cost math]:** Bedrock vs direct Anthropic API at your actual call volume. Document real numbers in the ADR. Don't migrate production traffic until evals show parity and cost math is verified.

---

## Layer 5 — Reranker

**Goal:** Cross-encoder reranker on top of hybrid search. Measurable Recall@5 lift.

### Tasks

- [ ] Evaluate: Modal (serverless GPU, teaches infra) vs Cohere Rerank API (free tier, zero infra)
- [ ] Deploy `bge-reranker-base` on Modal **or** wire Cohere Rerank (pick after evaluation)
- [ ] Add reranking stage to `core/hybrid_search.py`: top-20 hybrid → top-5 reranked
- [ ] Feature-flag via PostHog: `retrieval.reranker_enabled`
- [ ] Run Layer 3 retrieval evals: record Recall@5 before and after
- [ ] Latency budget analysis (reranker adds ~100–200ms — document acceptable or not)
- [ ] Write ADR-0005: retrieval pipeline architecture, measured lift

**[Decide when ready]:** Modal vs Cohere. Modal teaches serverless GPU patterns (higher learning value); Cohere is faster to ship. If the lift from the reranker is small, Cohere's free tier is fine and you spend the time on higher-leverage layers.

---

## Layer 6 — S3 + object storage migration

**Goal:** PDFs and images out of Railway disk, into S3 with lifecycle policies.

### Tasks

- [ ] Pulumi: add S3 bucket, SSE-S3 encryption, lifecycle policy (Standard → IA after 90 days → Glacier after 365)
- [ ] IAM: least-privilege roles — one for Railway app, one for local dev
- [ ] Presigned URL generation for protected content (no public bucket)
- [ ] Migrate PDF write path in `tasks/extraction_pdf_robust.py` to write to S3
- [ ] Migrate PDF read path to serve presigned URLs
- [ ] Evaluate: YOLO model weights on S3 vs Modal (weights ~100MB; Modal is better for bursty compute)
- [ ] Write ADR-0007: infra-as-code scope (what's Pulumi, what's manual PaaS UI)

---

## Layer 7 — Temporal + research agent

**Goal:** Real multi-step agent with durable execution. The biggest learning investment in the plan.

**Budget 2–3x a normal layer. Split into two PRs.**

### PR 1: Temporal setup + planner

- [ ] Add Temporal to `docker-compose.yml` (temporal, temporal-ui, elasticsearch — 3 extra services)
- [ ] Verify Temporal UI accessible at `localhost:8080`
- [ ] Write a trivial "echo" workflow to validate setup end-to-end
- [ ] Planner LLM call: question → structured JSON plan (Pydantic-validated)
- [ ] Plan schema: sub-questions, retrieval strategy per sub-question, expected output structure

### PR 2: Executor + synthesis + UI

- [ ] Typed tool activities: `retrieve()`, `read_content()`, `extract_claims()`, `check_coverage()`
- [ ] Executor workflow: iterate plan steps, invoke tools, accumulate state in workflow (not context)
- [ ] Bounded iteration: max 10 steps, max 100K tokens, max 5 min wall-clock
- [ ] Failure handling: tool error → re-plan or graceful degrade
- [ ] Synthesizer: produce cited markdown summary
- [ ] File synthesis back as new content item with back-link to sources
- [ ] Cost attribution per run (Braintrust + display in result)
- [ ] Eval set: 15 research questions, expected source IDs, expected key claims
- [ ] LLM-as-judge: citation faithfulness scorer
- [ ] Minimal UI: one input field, status stream, result displayed inline

**Architecture reference:** see [sedi-sota-stack-plan](sedi-sota-stack-plan#primary-research-agent-build-this) for planner-executor diagram.

---

## Layer 8 — Prefect + pipeline observability

**Goal:** Ingestion pipeline as an observable DAG. Clear retry boundaries per step.

### Tasks

- [ ] Wrap ingestion chain as Prefect flow: fetch → extract → enrich → embed → store → tag
- [ ] Retry policy per task (not per whole chain)
- [ ] Keep Celery for in-request work (extension callbacks, immediate async triggers)
- [ ] Prefect Cloud free tier **or** self-host — evaluate when ready
- [ ] Write ADR-0004: Celery vs Prefect vs Temporal (three tools, three different problems)

---

## Layer 9 — Text-to-SQL MCP tool

**Goal:** Natural-language query over your library, exposed as MCP tool.

### Tasks

- [ ] Schema introspection → schema-aware system prompt (run at startup, not hardcoded)
- [ ] User query → SQL via Sonnet (through `LLMClient`)
- [ ] AST validation: no DDL/DML allowed, table allow-list enforced
- [ ] Read-only execution with query timeout (500ms default)
- [ ] Result formatted back to natural language
- [ ] Exposed as MCP tool: `query_library`
- [ ] Eval set: 15 NL → expected SQL pairs
- [ ] Write ADR entry: text-to-SQL security model (why AST validation, not just prompt instructions)

---

## Layer 10 — Secrets + docs polish

**Goal:** Production-grade secrets management. All ADRs written. Demo ready.

### Tasks

- [ ] Doppler for local dev + Railway secrets (replaces raw `.env` files)
- [ ] AWS Secrets Manager for Lambda/Bedrock credentials (Pulumi-managed)
- [ ] Finalize all 10 ADRs in `docs/decisions/`
- [ ] README architecture diagram (Mermaid or hand-drawn, committed to repo)
- [ ] 3–5 min demo video: research agent in action
- [ ] Interview one-pager: system summary, 5 talking points, measured numbers

---

## Decision log index

All ADRs live in `docs/decisions/` once created.

| ADR | Topic | Layer |
|---|---|---|
| 0001 | Vector storage — pgvector vs managed | 0 |
| 0002 | Observability stack — OTEL + Braintrust + Sentry | 0 |
| 0003 | LLM provider strategy — Bedrock + OpenAI + model selection | 4 |
| 0004 | Async orchestration — Celery vs Prefect vs Temporal | 8 |
| 0005 | Retrieval architecture — hybrid search + reranker | 5 |
| 0006 | Agent architecture — planner-executor, Temporal durability | 7 |
| 0007 | Infra-as-code scope — Pulumi for AWS, manual for PaaS | 6 |
| 0008 | Eval methodology — golden sets, LLM-as-judge, CI gate | 3 |
| 0009 | Deployment strategy — Railway + Vercel + AWS | 10 |
| 0010 | Feature flag strategy — PostHog, what gets flagged | 5 |
