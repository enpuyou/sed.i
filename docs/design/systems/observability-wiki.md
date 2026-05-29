---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# sed.i Observability Wiki

What each tool tracks, when to look at it, and what it costs.

---

## The full stack

| Tool | Layer | What it tracks | When to look |
|---|---|---|---|
| **Braintrust** | LLM calls | Every OpenAI prompt, response, token count, cost, latency | Tagging/summarization quality degraded; cost spike |
| **Sentry** | Errors | Exceptions in FastAPI, Celery, SQLAlchemy — full stack trace | Anything returning 500; background task failures |
| **OTEL → Grafana** | Infra traces | Every HTTP request, SQL query, Celery task — timing spans | Diagnosing where latency comes from in a slow request |
| **Prefect** | Pipeline runs | Each ingestion step (extract → embed → tag → chunk) — timing, retries, failures per article | An article got stuck mid-pipeline; which step failed on which article |

---

## What Prefect tracks specifically

The ingestion pipeline runs as a Prefect flow (`app/workflows/ingestion.py`). Each article that is ingested creates one flow run with four steps:

| Step | What it does | Retries |
|---|---|---|
| `extract-full-content` | Re-fetches the URL and extracts full article HTML via trafilatura | 2 (15s delay) |
| `generate-embedding` | Generates the item-level 1536-dim semantic embedding via OpenAI | 3 (5s delay) |
| `generate-tags` | Extracts 4-6 semantic tags via LLM (`structured_chat`) | 2 (10s delay) |
| `generate-chunk-embeddings` | Splits article into chunks and embeds each for hybrid search | 2 (10s delay) |

You can see all runs at `http://localhost:4200` (local) or your Railway Prefect service URL.

**Prefect only runs when `PREFECT_ENABLED=true`.** When false, the same steps run as a plain Celery chain with no UI visibility.

---

## What Prefect is NOT

- Not a replacement for Sentry — Prefect shows which step failed; Sentry shows the full stack trace of why
- Not an LLM tracer — Braintrust handles prompt/response/cost logging for OpenAI calls
- Not a request tracer — OTEL handles HTTP + SQL spans

Prefect's unique value: **per-article pipeline visibility**. When a user's article never finishes processing, Prefect tells you exactly which step it got stuck on and whether retries were exhausted.

---

## Architecture: Celery + Prefect coexistence

```
User saves URL
    └─> FastAPI (HTTP request returns immediately)
          └─> Celery task: fetch_metadata (Phase 1)
                └─> [PREFECT_ENABLED=true]
                      └─> Prefect flow: ingest_content
                            ├─> extract-full-content  (retries=2)
                            ├─> generate-embedding    (retries=3)
                            ├─> generate-tags         (retries=2)
                            └─> generate-chunk-embeddings (retries=2)
```

Celery handles Phase 1 because it uses `DatabaseTask` (session management tied to Celery). Prefect takes over for Phase 2+ where the plain functions are clean entry points.

---

## Latency impact

| Tool | User-facing latency added | Background overhead |
|---|---|---|
| Braintrust | None | ~10ms per LLM call (tracing wrapper) |
| Sentry | None | ~1ms per error event |
| OTEL | None | ~1ms per span |
| Prefect | **None** | ~50-200ms per pipeline run (step tracking HTTP calls to Prefect server) |

All observability overhead is in background workers. The HTTP response to the user returns before any pipeline work begins.

---

## LLM task routing (as of multi-provider plan)

All LLM calls go through `LLMClient` in `app/core/llm_client.py`. Provider and model are controlled by env vars:

| Task | Default model | Env var override |
|---|---|---|
| Embeddings | `text-embedding-3-small` (OpenAI, always) | `EMBED_PROVIDER` (changing requires full re-embed) |
| Tagging | `gpt-4o-mini` / `nova-micro` | `LLM_MODEL_TAGGING_OPENAI` / `LLM_MODEL_TAGGING_BEDROCK` |
| Summarization | `gpt-4o-mini` / `nova-lite` | `LLM_MODEL_SUMMARY_OPENAI` / `LLM_MODEL_SUMMARY_BEDROCK` |
| MCP list summary | `gpt-4o-mini` / `nova-lite` | `LLM_MODEL_MCP_SUMMARY_OPENAI` / `LLM_MODEL_MCP_SUMMARY_BEDROCK` |
| SQL generation | `gpt-4o` / `claude-sonnet` | `LLM_MODEL_SQL_GEN_OPENAI` / `LLM_MODEL_SQL_GEN_BEDROCK` |
| Connection insight | `gpt-4o-mini` / `nova-micro` | `LLM_MODEL_INSIGHT_OPENAI` / `LLM_MODEL_INSIGHT_BEDROCK` |

Toggle the whole chat stack: `LLM_PROVIDER=openai` or `LLM_PROVIDER=bedrock`.

---

## Braintrust coverage gap

Braintrust wraps the OpenAI SDK (`braintrust.wrap_openai`). It traces:
- ✅ All embed calls (always OpenAI)
- ✅ All chat calls when `LLM_PROVIDER=openai`
- ❌ Chat calls when `LLM_PROVIDER=bedrock` (boto3 — not wrapped by Braintrust)

When using Bedrock for chat, task-level errors and timing are still covered by Sentry + OTEL, but there is no prompt/response/cost logging.
