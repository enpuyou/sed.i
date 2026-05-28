# ADR-0003: LLM Provider Strategy

**Status:** Accepted
**Date:** 2026-05-22

---

## Context

sed.i makes four categories of LLM calls:

| Category | Frequency | Latency sensitivity | Quality bar |
|---|---|---|---|
| Embedding (ingestion) | High | Low (Celery async) | Moderate |
| Tagging | Medium | Low (async) | Moderate |
| Summarization | Low | Low (async) | High |
| MCP synthesis | Low | High (user-facing) | High |

We needed to choose: route all calls to a single provider or build provider abstraction from the start. Provider abstraction was chosen because:

1. **AWS Bedrock skill** — the project is explicitly a learning vehicle. Operating Bedrock (IAM, budget alarms, model access, regional availability) is a distinct and valuable skill.
2. **Cost optionality** — OpenAI and Bedrock pricing diverges depending on model choice and call volume. Measuring both providers against the eval harness (Layer 3) gives real numbers to make this decision.
3. **Vendor resilience** — a single-provider dependency is a downtime risk. Failover to a second provider is a one-config-key change with this architecture.

---

## Decision

### Architecture

All LLM calls route through `app/core/llm_client.py::LLMClient`. The singleton `llm_client` is the only import call sites need. Provider is selected by `settings.LLM_PROVIDER` ("openai" | "bedrock").

**Failover:** if the primary provider raises, the client logs a warning and retries on the fallback. This is best-effort resilience — it adds one retry latency but avoids hard failures during partial outages.

### Provider selection

| Task | `LLM_PROVIDER=openai` | `LLM_PROVIDER=bedrock` |
|---|---|---|
| Embeddings | text-embedding-3-small (1536 dims) | Always OpenAI (EMBED_PROVIDER=openai) |
| Tagging | gpt-4o-mini (`LLM_MODEL_TAGGING_OPENAI`) | nova-micro (`LLM_MODEL_TAGGING_BEDROCK`) |
| Summarization | gpt-4o-mini (`LLM_MODEL_SUMMARY_OPENAI`) | nova-lite (`LLM_MODEL_SUMMARY_BEDROCK`) |
| SQL generation | gpt-4o (`LLM_MODEL_SQL_GEN_OPENAI`) | claude-sonnet (`LLM_MODEL_SQL_GEN_BEDROCK`) |
| Connection insight | gpt-4o-mini (`LLM_MODEL_INSIGHT_OPENAI`) | nova-micro (`LLM_MODEL_INSIGHT_BEDROCK`) |

Both embedding models produce 1536-dimensional vectors, so the existing pgvector HNSW index is compatible with both providers — no migration required when switching.

### Infrastructure

Bedrock credentials are IAM-scoped to `bedrock:InvokeModel` only. The Pulumi project (`infra/`) provisions:
- IAM user `sedi-bedrock-app` with attached policy
- IAM user `sedi-bedrock-dev` for local development (separate credentials)
- Budget alarm at $20/month (created before enabling any traffic)

### Prompt caching

Bedrock supports prompt caching on Claude models. The MCP system prompt (fixed across calls) is a good candidate. This is a `[decide when ready]` item — enable when the MCP call volume justifies the complexity.

---

## Alternatives considered

### Route everything through OpenAI
- **Pros:** Zero new infrastructure, one credential to manage, existing Braintrust traces continue to work.
- **Rejected because:** No AWS skill, no vendor resilience, no cost comparison data.

### Use Anthropic API directly (not Bedrock)
- **Pros:** Simpler than Bedrock (no IAM, no region config), prompt caching supported.
- **Cons:** Doesn't teach AWS infrastructure, no budget/cost-alarm integration with AWS Budgets.
- **Rejected because:** The learning goal is Bedrock specifically, not just Anthropic models.

### LiteLLM as provider abstraction
- **Pros:** Handles many providers, retry logic included, OpenAI-compatible interface.
- **Cons:** Another dependency, less control over retry/failover behavior, obscures the Bedrock API surface we want to learn.
- **Rejected because:** The abstraction layer is simple enough to own. LiteLLM becomes worth adding if we add 3+ providers.

---

## Consequences

- `boto3` added as a dependency. It's large (~9MB) but only imported lazily when `LLM_PROVIDER=bedrock`.
- Bedrock embeddings are one-at-a-time (Titan processes serially) — the client loops internally. At current ingestion volumes this is acceptable; if batch embed becomes a bottleneck, Titan supports batches via `batch_invoke_model`.
- JSON mode (`response_format={"type": "json_object"}`) is emulated on Bedrock by appending a JSON instruction to the last user message. This is less reliable than OpenAI's constrained decoding — monitor tagging eval scores when running on Bedrock.

---

## Migration trigger

Switch `LLM_PROVIDER` to `"bedrock"` in production when:
1. Eval scores (Layer 3 harness) are within 5% for both providers
2. Cost math shows ≥10% savings at current call volume
3. AWS budget alarm is in place and verified
