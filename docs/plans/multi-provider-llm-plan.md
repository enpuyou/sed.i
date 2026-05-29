---
type: plan
status: active
last_updated: 2026-05-28
consumer: agent
---

# Multi-Provider LLM Plan: sed.i

**Goal:** Make sed.i compatible with OpenAI and AWS Bedrock (Nova, Claude) with per-task model routing and a single env-var toggle. Embeddings are pinned to OpenAI permanently to eliminate vector space risk.

---

## 1. Decision: Library vs Custom Adapter

### Verdict: Keep the custom adapter — no LiteLLM

**Why not LiteLLM:**
- We have exactly two providers. LiteLLM's value is 100+ providers — overkill here.
- Adds ~20MB of transitive dependencies to an already heavy image (torch, onnxruntime, ultralytics).
- Braintrust wrapping (`braintrust.wrap_openai`) requires the raw OpenAI SDK client object — incompatible with LiteLLM's proxy interface.
- boto3 is already wired. LiteLLM's Bedrock support calls the same boto3 underneath.

**What to borrow from the ecosystem:**
- `instructor` library for structured JSON outputs from Nova/Claude (replaces manual backtick stripping).
- The routing-table pattern — implemented as env vars loaded into Pydantic Settings.

---

## 2. Architecture

```
call sites (8 locations)
        |
        | llm_client.chat(messages, task="tagging")
        | llm_client.embed(texts)
        v
┌─────────────────────────────────────────────────┐
│                 LLMClient (facade)              │
│                                                 │
│  embed() → always OpenAI (EMBED_PROVIDER=openai)│
│                                                 │
│  chat():                                        │
│  1. resolve_model(task, LLM_PROVIDER)           │
│  2. call primary provider                       │
│  3. on exception → call fallback provider       │
└──────────┬────────────────────┬─────────────────┘
           │                    │
    embed (always)       chat (toggleable)
           │                    │
  ┌────────▼────────┐  ┌───────▼────────────────┐
  │  OpenAI SDK     │  │  LLM_PROVIDER=openai   │
  │ (braintrust-    │  │  or                     │
  │  wrapped)       │  │  LLM_PROVIDER=bedrock   │
  │                 │  │                         │
  │ embed:          │  │  openai chat:           │
  │  text-embed-    │  │   gpt-4o-mini (default) │
  │  3-small        │  │   gpt-4o (sql_gen)      │
  │                 │  │                         │
  └─────────────────┘  │  bedrock chat:          │
                       │   nova-micro (tagging)  │
                       │   nova-lite (summary)   │
                       │   claude-haiku (fast)   │
                       │   claude-sonnet (SQL)   │
                       └─────────────────────────┘
```

### Task → Model Routing Table

| Task | OpenAI default | Bedrock default |
|------|----------------|-----------------|
| `embed` | `text-embedding-3-small` | _(always OpenAI — see below)_ |
| `tagging` | `gpt-4o-mini` | `amazon.nova-micro-v1:0` |
| `summary` | `gpt-4o-mini` | `amazon.nova-lite-v1:0` |
| `mcp_summary` | `gpt-4o-mini` | `amazon.nova-lite-v1:0` |
| `sql_gen` | `gpt-4o` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| `insight` | `gpt-4o-mini` | `amazon.nova-micro-v1:0` |

---

## 3. Embedding Strategy: Pinned to OpenAI

### Decision: Embeddings always use OpenAI

`EMBED_PROVIDER` defaults to `"openai"` and is not expected to change in normal operation.

**Why:**
- All existing content is indexed with `text-embedding-3-small` vectors (1536 dims).
- Switching embed providers silently poisons search: old and new vectors live in different spaces. Cosine similarity between them returns wrong rankings with no error.
- Amazon Titan Embed v2 caps at 1024 dims — requires an Alembic migration + full re-embed of all content.
- Amazon Titan Embed v1 is 1536-dim compatible but costs 5× more than OpenAI ($0.10 vs $0.02/1M tokens).
- Braintrust traces OpenAI embed calls — Titan calls would be invisible.

**Toggleability:** `EMBED_PROVIDER` is an env var so it can be changed if a future migration is planned. Changing it requires re-embedding all content (`content_items`, `highlights`, `content_chunks`, `tag_embeddings`).

```bash
# Default — always OpenAI for embeddings
EMBED_PROVIDER=openai

# Only change this if you have run a full re-embed migration
# EMBED_PROVIDER=bedrock
```

**Titan v2 future path** (if cost becomes a driver at scale):
1. Alembic: add `embedding_1024 vector(1024)` to all four tables
2. Backfill script: re-embed all content through Titan v2
3. Add `EMBED_DIM=1024` config; all pgvector queries read it
4. Drop old column, rename new one

Not in scope for this plan.

---

## 4. Per-Task Config Schema

### New env vars (all optional — defaults match current behavior)

```bash
# Provider toggles
LLM_PROVIDER=openai          # "openai" | "bedrock" — controls chat tasks
EMBED_PROVIDER=openai        # always openai; change only after full re-embed

# Chat models — OpenAI
LLM_MODEL_TAGGING_OPENAI=gpt-4o-mini
LLM_MODEL_SUMMARY_OPENAI=gpt-4o-mini
LLM_MODEL_MCP_SUMMARY_OPENAI=gpt-4o-mini
LLM_MODEL_SQL_GEN_OPENAI=gpt-4o
LLM_MODEL_INSIGHT_OPENAI=gpt-4o-mini

# Chat models — Bedrock
LLM_MODEL_TAGGING_BEDROCK=amazon.nova-micro-v1:0
LLM_MODEL_SUMMARY_BEDROCK=amazon.nova-lite-v1:0
LLM_MODEL_MCP_SUMMARY_BEDROCK=amazon.nova-lite-v1:0
LLM_MODEL_SQL_GEN_BEDROCK=us.anthropic.claude-sonnet-4-5-20250929-v1:0
LLM_MODEL_INSIGHT_BEDROCK=amazon.nova-micro-v1:0
```

### Routing lookup (in LLMClient)

```python
def _resolve_model(self, task: str, provider: str) -> str:
    attr = f"LLM_MODEL_{task.upper()}_{provider.upper()}"
    return getattr(settings, attr, _HARDCODED_DEFAULTS[provider][task])
```

### Call site change (backward-compatible)

```python
# Before (still works — task=None falls back to prefer_quality logic)
llm_client.chat(messages=messages, prefer_quality=True)

# After (task drives model selection)
llm_client.chat(messages=messages, task="sql_gen")
```

---

## 5. JSON Mode Reliability

### Problem

Nova Micro/Lite don't have native JSON mode. Current workaround (append instruction + strip backticks) fails when models wrap JSON in prose or add explanation text.

### Solution: `instructor` for structured task outputs

`instructor` wraps any SDK and retries with validation error feedback when the response doesn't match the Pydantic schema.

```python
# New structured_chat() method on LLMClient
def structured_chat(
    self,
    messages: list[dict],
    *,
    response_model: type[BaseModel],
    task: str,
    max_tokens: int = 512,
    max_retries: int = 2,
) -> BaseModel: ...
```

**Tagging** uses `TagResponse(BaseModel)` with `tags: list[str]`. All other tasks (SQL generation, freeform summarization) keep the existing instruction-injection approach — they return prose, not structured JSON.

Add to `pyproject.toml`: `instructor (>=1.7.0,<2.0.0)`

---

## 6. Implementation Phases

### Phase 1 — Pin Embeddings to OpenAI ⏱ 30m

**Goal:** Guarantee embeddings always use OpenAI regardless of `LLM_PROVIDER`. Eliminates the vector space mismatch risk entirely.

Files:
- `app/core/config.py`: add `EMBED_PROVIDER: str = "openai"`
- `app/core/llm_client.py`: in `embed()`, use `settings.EMBED_PROVIDER` instead of `self._provider` as the primary; remove fallback to Bedrock for embed

```python
def embed(self, texts: list[str] | str, *, model: str | None = None) -> EmbedResult:
    provider = settings.EMBED_PROVIDER  # always "openai" by default
    return self._embed_with(provider, texts, model=model)
```

No fallback to Bedrock for embed — if OpenAI is down, embeddings fail loudly rather than silently producing incompatible vectors.

---

### Phase 2 — Per-Task Routing Config ⏱ 3-4h

Files:
- `app/core/config.py`: add all `LLM_MODEL_*` fields with defaults
- `app/core/llm_client.py`:
  - Add task name constants (`TASK_TAGGING`, `TASK_SUMMARY`, etc.)
  - Add `_resolve_model(task, provider)` helper
  - Add `task: str | None = None` to `chat()` signature
  - Add startup warning if `LLM_PROVIDER=bedrock` and `AWS_REGION` outside US

---

### Phase 3 — Update Call Sites ⏱ 1-2h

| File | Change |
|------|--------|
| `app/tasks/tagging.py` line 322 | add `task="tagging"` |
| `app/tasks/summarization.py` line 63 | add `task="summary"` |
| `app/mcp/tools/summarize.py` line 156 | add `task="mcp_summary"` |
| `app/mcp/tools/query.py` lines 248, 297 | add `task="sql_gen"` |
| `app/api/search.py` ~line 460 | refactor `_call_openai_insight()` → `llm_client.chat(task="insight")` |
| `app/core/embedding_cache.py` | rename `call_openai_embedding()` → `call_embed()`, delegate to `llm_client.embed()` |
| `app/core/hybrid_search.py` line 301 | remove `if not settings.OPENAI_API_KEY` guard (embed is always OpenAI, key is always required) |

Update test mock paths in `tests/test_embedding_cache.py` (patch target changes from `call_openai_embedding` to `call_embed`).

---

### Phase 4 — Structured Outputs with instructor ⏱ 2-3h

Files:
- `app/core/llm_schemas.py` (new): `TagResponse`, future structured output types
- `app/core/llm_client.py`: add `structured_chat()` method
- `app/tasks/tagging.py`: use `structured_chat(response_model=TagResponse, task="tagging")`, remove manual `json.loads()` + duck-typing

---

### Phase 5 — Cross-Region Inference Validation ⏱ 30m

- Add boto3 `Config(connect_timeout=5, read_timeout=30)` to Bedrock client
- Add startup warning for region outside `{us-east-1, us-east-2, us-west-2}` when using `us.*` Claude models
- Document in `ARCHITECTURE.md`: cross-region profiles route within US, not pinned to one region

---

## 7. Backward Compatibility Guarantees

| Concern | Guarantee |
|---------|-----------|
| Existing 1536-dim embeddings | No change. Embeddings always use OpenAI `text-embedding-3-small`. |
| `chat(prefer_quality=True)` callers | `prefer_quality` still works when `task=None`. |
| `LLM_PROVIDER=openai` (default) | Behavior identical to today. No model changes. |
| `LLM_PROVIDER=bedrock` without `LLM_MODEL_*` | Falls back to hardcoded defaults matching the routing table. |
| Braintrust tracing | OpenAI path unchanged (embed always + chat when `LLM_PROVIDER=openai`). Bedrock chat has no Braintrust tracing — only OTEL task-level spans. |
| Test mocks | One-line patch path update in `test_embedding_cache.py`. All other mocks unaffected. |

---

## 8. Risk Register

### Risk 1: Nova Micro JSON instability on tagging

**Description:** Nova Micro may produce higher JSON formatting failure rates than Claude, even with `instructor` retries.

**Mitigation:**
- `instructor` retries up to `max_retries=2` with validation error feedback.
- Tagging failure returns `[]` gracefully — no article blocked.
- Single env var swap (`LLM_MODEL_TAGGING_BEDROCK=amazon.nova-lite-v1:0`) to upgrade without deploy.
- Add Sentry counter for tag extraction failures by model; alert if >5% over 24h.

---

### Risk 2: Cross-region inference latency spikes

**Description:** `us.*` Claude model IDs route across us-east-1/us-east-2/us-west-2. Regional congestion can add 2-5s to SQL generation calls (the only synchronous LLM call in the MCP path).

**Mitigation:**
- Celery tasks (tagging, summary, embed) are async — latency spikes invisible to users.
- Add 30s read timeout on Bedrock client; surface as structured MCP error.
- Monitor P95 in Braintrust; fall back to OpenAI if Bedrock consistently exceeds 15s.

---

### Risk 3: Bedrock chat observability gap

**Description:** Braintrust only wraps the OpenAI SDK. Bedrock boto3 calls are invisible to Braintrust — no prompt/response/cost logging for chat tasks when `LLM_PROVIDER=bedrock`.

**Mitigation:**
- Embeddings stay on OpenAI, so Braintrust retains full visibility into the embed path.
- OTEL/Sentry capture Celery task errors and timing for Bedrock chat tasks.
- AWS Bedrock Model Invocation Logging can be enabled (CloudWatch/S3) if deeper audit is needed.
- Acceptable tradeoff at current scale — revisit if Bedrock becomes the primary chat provider.

---

## Files to Create or Modify

**New:**
- `app/core/llm_schemas.py` — Pydantic structured output types

**Modified:**
- `app/core/config.py` — `EMBED_PROVIDER`, all `LLM_MODEL_*`
- `app/core/llm_client.py` — embed pinning, routing, `structured_chat()`
- `app/core/embedding_cache.py` — `call_openai_embedding()` → `call_embed()`
- `app/core/hybrid_search.py` — remove OpenAI-key guard for embed
- `app/tasks/tagging.py` — `task=`, `structured_chat()`
- `app/tasks/summarization.py` — `task=`
- `app/mcp/tools/summarize.py` — `task=`
- `app/mcp/tools/query.py` — `task=`
- `app/api/search.py` — `_call_openai_insight()` → `llm_client.chat(task="insight")`
- `tests/test_embedding_cache.py` — update mock patch path
- `pyproject.toml` — add `instructor`
