---
layout: layouts/chapter.njk
title: Decision log
part: 6
order: 2
anatomy: custom
summary: The eight architecture decision records in plain language, what is still true of each in the code today, and ten significant decisions that exist in the code with no record.
verified: 2026-10-26
permalink: /talking/decision-log/
---

## In one minute

The repository has eight architecture decision records (ADRs) in `docs/decisions/`, all written between May and July 2026 and all still marked "Accepted". This chapter restates each in plain language (the question, the options, the decision, the consequences) and then checks it against the code on 2026-10-26. The result: one is still true as written (service layer), five are true with details that drifted (vector storage, observability, object storage, text-to-SQL, Celery memory), one is narrower than it says (LLM providers), and one is partly superseded (pipeline orchestration: Temporal was never built and Prefect is built but off).

The second half lists ten decisions that shape the system and have no record, each with the file that shows it and the alternative that was not taken, and says which deserve a record first. Use this chapter when you are asked "why did you build it this way", and answer from the record when there is one and from the code when there is not.

## How to read this log

Status vocabulary, used in every entry:

| Status | Meaning |
|---|---|
| Still true | The code does what the record says. |
| Still true, details drifted | The decision holds, but a number, a name or a claim in the record no longer matches the code or a measurement. |
| Narrower than recorded | The decision holds for part of what the record describes. |
| Partly superseded | Part of the record was replaced by something else, or was never built. |

"Verified" means checked by reading the code in this repository on 2026-10-26, not by looking at the production services. Anything that depends on a live setting says "not verified". The mismatches are also listed in the guide's discrepancies folder, file 6.1-6.2.

## The eight records

### ADR 0001: Vector storage (`docs/decisions/0001-vector-storage.md`)

**Question.** Where should the 1,536-number embeddings live and be searched?

**Options.** pgvector inside the existing Postgres; Qdrant (self-hosted or cloud); Pinecone; Weaviate; Chroma.

**Decision.** Stay on pgvector with HNSW indexes. Reasons recorded: the corpus is far below one million vectors; one database means no dual write and no second system; keyword-and-vector fusion is one SQL query; no extra cost. Qdrant was a close second.

**Consequences accepted.** Index builds are slow and done offline; no native tenant filtering, so every query needs `WHERE user_id`; index memory grows with the corpus. The record lists triggers for moving to a dedicated store: more than 5 million vectors, p95 search latency above 200 ms, multi-vector indexing at scale, or more than 100 writes per second.

**Status today: still true, details drifted.** Vectors are `vector(1536)` columns with HNSW indexes (`m=16`, `ef_construction=64`) built by `content-queue-backend/scripts/post_deploy.py`. Three claims have changed. The record says HNSW gives sub-50 ms p95 search; no latency measurement exists in the repository, and the main search lane does not use HNSW at all (it scans a user's chunks exactly, 5 ms for 557 chunks, measured). The record says an index build adds about one second per 10,000 vectors; a scratch build of 20,000 vectors took 127.7 s (measured, default build memory). And none of the migration triggers has been tested against production data. See [RAG from first principles](/retrieval/rag-from-first-principles/).

### ADR 0002: Observability stack (`docs/decisions/0002-observability-stack.md`)

**Question.** How are LLM calls, requests, tasks and errors observed?

**Options.** Per concern: Braintrust, Langfuse, Phoenix, Helicone or LangSmith for LLM calls; OpenTelemetry to Grafana Cloud, Datadog, Honeycomb or New Relic for traces; Sentry, Rollbar or Highlight for errors.

**Decision.** Braintrust for LLM calls, OpenTelemetry exported to Grafana Cloud for traces, Sentry for errors. One best-fit tool per concern instead of one vendor.

**Consequences accepted.** Three tools, three dashboards; data leaves the system; free-tier limits. Stated triggers for change: Langfuse if self-hosting is required, Honeycomb if trace debugging hurts, Datadog only with a team and budget.

**Status today: still true, details drifted (extended, partly delivered).** `content-queue-backend/app/core/llm_client.py` wraps the OpenAI client with Braintrust, `content-queue-backend/app/core/observability.py` sets up Sentry and OpenTelemetry (both switch off without their keys), and the frontend reports to Sentry. PostHog product analytics was added and is not in the record. The record's fourth goal, dashboards for request rate, latency and spend, cannot be verified from the repository, and no metrics or alerts are emitted by the code. The record's "10 percent" appears in code only as Sentry's `traces_sample_rate`; the OpenTelemetry provider sets no sampler. Which backend receives the OpenTelemetry traces in production is not verified. See [Deploy and operations](/platform/deploy-and-operations/).

### ADR 0003: LLM provider strategy (`docs/decisions/0003-llm-provider-strategy.md`)

**Question.** Call one provider, or build a provider layer from the start?

**Options.** Route everything through OpenAI; use the Anthropic API directly; use LiteLLM; build a small own layer with OpenAI and Amazon Bedrock behind it.

**Decision.** One class, `LLMClient`, selects the provider from `LLM_PROVIDER` (OpenAI by default). If the primary raises, retry on the fallback. Embeddings always use OpenAI. A per-task model table picks cheap or larger models. Bedrock access and a $20 per month budget alarm are provisioned with Pulumi. A stated reason is learning Bedrock operations.

**Consequences accepted.** `boto3` dependency; Bedrock embeddings one at a time; JSON mode emulated on Bedrock and less reliable. Trigger to switch the default: evaluation scores within 5 percent, at least 10 percent savings, alarm verified.

**Status today: narrower than recorded.** The class exists and is the only call path (a test enforces it: 27 call sites, 15 files). Failover with one retry applies to plain `chat()` only. `structured_chat`, used by 10 of the 27 call sites including tagging and analysis, calls the `instructor` library directly and has no fallback. The record says changing embedding providers needs no migration because both vectors have 1,536 dimensions; the code says the opposite, that a change needs a full re-embed because the vector spaces differ. Since the record, the code added a per-user daily budget of $5 in Redis and the call-site guard test, neither of which is recorded. The default provider is still OpenAI; the Pulumi file defines the budget alarm (`infra/__main__.py`), but whether it is applied, or Bedrock credentials exist in production, is not verified. See [The LLM gateway](/llm/gateway/).

### ADR 0004: Object storage (`docs/decisions/0004-object-storage.md`)

**Question.** Where do original PDFs live, given that only the extracted text was kept?

**Options.** S3, Postgres `bytea`, Railway volumes, Cloudflare R2.

**Decision.** A private S3 bucket managed by Pulumi, key `pdfs/{user_id}/{item_id}.pdf`, encrypted with SSE-S3, lifecycle to Infrequent Access at 90 days and Glacier at 365. Upload right after extraction, with failures logged and ignored. Reads through a presigned URL. Leave the layout-model weights on the Hugging Face cache.

**Consequences accepted.** Items saved before the feature have no stored PDF. With no bucket configured, every storage operation does nothing.

**Status today: still true, details drifted (off by default).** `S3_STORAGE_ENABLED` defaults to `False` and `AWS_S3_BUCKET` to empty in `content-queue-backend/app/core/config.py`, and `content-queue-backend/app/core/storage.py` does nothing unless both are set. That is stricter than the record, which mentions only the bucket (a comment in `content-queue-backend/app/tasks/extraction.py` repeats the older wording). The upload path, the presign endpoint (`GET /content/{id}/pdf-url`) and the Hugging Face weights download are all in the code as recorded. One gap the record does not mention: nothing deletes stored objects when an item or account is deleted, which matters once the flag is on ([Gaps and roadmap](/reliability/gaps-and-roadmap/), item 4). Whether production has the flag on is not verified; the architecture notes treat it as off.

### ADR 0005: Pipeline orchestration (`docs/decisions/0005-pipeline-orchestration.md`)

**Question.** Which tool runs which background work?

**Options.** Celery chains; Prefect; Temporal.

**Decision.** Three tools, three roles. Celery triggers and runs immediate asynchronous work. Prefect wraps the ingestion pipeline as an observable graph with per-step retries, switched on with `PREFECT_ENABLED`. Temporal is reserved for the durable multi-step research agent. Prefect was rejected for the agent because its state is not durable; Temporal was rejected for ingestion because it needs several extra services.

**Consequences accepted.** Prefect is opt-in with the Celery chain as the fallback. Triggers to turn Prefect on: a deployed server, at least 10 observed production runs, alerts wired.

**Status today: partly superseded.** What is true in production: ingestion runs as a plain Celery chain (`extract_metadata`, then `extract_full_content`, then `generate_embedding`, then analysis and chunking). The Prefect version exists in `content-queue-backend/app/workflows/ingestion.py` and `content-queue-backend/app/tasks/extraction.py` can submit to it when `PREFECT_ENABLED` is true, falling back to the Celery chain if submission fails. The flag defaults to false and, per the architecture notes and the 2026-10-25 memory audit, is false in production with the Prefect service stopped (not verified against the live project). The record's turn-on triggers were not met; the service was stopped instead, which freed about 222 MB (reported). Temporal was never built: the repository has no Temporal code, dependency or compose entry. The research agent runs as a Celery chord with its state in a `research_runs` row and a five-minute sweep that marks stalled runs partial ([The research agent](/llm/research-agent/)). So one third of the decision was implemented and switched off, and one third was replaced by a simpler design.

### ADR 0006: Text-to-SQL security (`docs/decisions/0006-text-to-sql-security.md`)

**Question.** How do you let a model write SQL over a user's library when an article in that library may contain instructions meant to hijack the model?

**Options.** Trust the prompt; parameterized generation; a sandbox database; a read-only role; validate every statement structurally.

**Decision.** Parse every generated statement with sqlglot, accept one `SELECT` only, allow seven named tables, scope to the user with a bound `:user_id` parameter, and run with a 500 ms statement timeout. A regex check is the fallback if the parser is missing.

**Consequences accepted.** Errors come back as readable messages; the schema shown to the model is a static list so the allow-list cannot drift; unlisted columns such as embeddings are invisible to the model.

**Status today: still true, details drifted (code stricter than recorded, one gap).** `content-queue-backend/app/mcp/tools/query.py` implements all of it. Differences: the junction table is `content_list_membership`, not `list_items`; the validator also requires a direct `user_id` (or `owner_id`) equality predicate on every user-scoped table, where the record describes only a prompt instruction and says the worst case is leaking the user's own data; and the code caps results at 50 rows, which the record does not mention. The gap is that `deleted_at` appears nowhere in the file, so trashed items and their chunks can be returned. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/).

### ADR 0007: Celery worker memory and ML tasks (`docs/decisions/0007-celery-memory-ml-tasks.md`)

**Question.** How do you keep a worker that sometimes needs torch and a layout model from holding a gigabyte or more all day?

**Options.** Stay on the `solo` pool; prefork with one process; prefork with two; run the heavy task in a child process that exits; a dedicated inference service; recycling with `--max-tasks-per-child`.

**Decision.** Keep `--pool=solo` with beat in the same process, and run PDF layout extraction in a subprocess per PDF so torch is resident only while a PDF is being processed. Promote to a dedicated service if PDFs become frequent; switch to prefork with one process and task recycling if other memory accumulates. The record also documents that `--concurrency=2` and `worker_max_tasks_per_child` do nothing under solo.

**Status today: still true, numbers drifted.** The worker still runs solo with beat (the start command is a dashboard setting recorded in `ARCHITECTURE.md`), and PDF extraction still runs in an isolated subprocess (`content-queue-backend/app/tasks/extraction_implementations.py`). Three numbers differ from measurements in this guide: the subprocess overhead is about 12 s fixed cost per PDF, not 1 to 2 s; the child's memory floor measured about 635 MB, not 1 to 1.5 GB; and the worker's production median is 482 MB (reported), above the roughly 300 MB the record assumes for the baseline. The recycling and prefork options were not taken, so time limits and child recycling remain inactive, which is the top reliability gap ([Background work](/platform/background-work/), [PDFs](/ingestion/pdfs/)).

### ADR 0008: Service-layer threshold (`docs/decisions/0008-service-layer-threshold.md`)

**Question.** When does route logic move out of an API router into `app/services/`?

**Options.** Extract everything; scope the layer to content ingestion only; no rule.

**Decision.** Extract when the logic is called from more than one entry point, or when a single handler holds more than roughly 80 lines of real logic. Do not retrofit existing routers.

**Consequences accepted.** The 80-line threshold is a judgement call. The record names the connections and insight code in `search.py` as the next candidate, to be extracted when it is next touched.

**Status today: still true.** `content-queue-backend/app/services/` still holds one module, `content.py`, which the REST route and the MCP tool both call, matching the record. The listed router files have grown (`search.py` is still 870 lines; `content.py` is 795 lines against 767 in the record) and the connections code has not been extracted, which the record allows. This is a convention, not enforced by tooling.

### Summary

| ADR | Topic | Status today |
|---|---|---|
| 0001 | pgvector with HNSW | Still true, details drifted |
| 0002 | Braintrust, OpenTelemetry, Sentry | Still true, details drifted: extended, dashboards unverified |
| 0003 | LLM provider layer | Narrower than recorded |
| 0004 | S3 for original PDFs | Still true, details drifted: off by default |
| 0005 | Celery, Prefect, Temporal | Partly superseded |
| 0006 | Text-to-SQL guard | Still true, details drifted: code stricter, one gap |
| 0007 | Solo pool, PDF subprocess | Still true, numbers drifted |
| 0008 | Service-layer threshold | Still true |

## Decisions with no record

Ten decisions that shape the system and appear in no ADR. Each row gives the evidence, what was done, and the alternative. The record column says whether any existing document covers it.

| Decision | Evidence | Alternative not taken | Record |
|---|---|---|---|
| Soft delete everywhere (a timestamp, purged after 7 days) | `deleted_at` in `content-queue-backend/app/models/content.py` and `content-queue-backend/app/models/vinyl.py`; purge in `content-queue-backend/app/tasks/cleanup.py` | Hard delete, or an archive table | None. Three paths ignore it, so the rule is a convention, not a mechanism |
| One worker process: `solo` pool with beat embedded | `content-queue-backend/app/core/celery_app.py`; start command in `ARCHITECTURE.md` | Prefork with a separate beat service | Partly: ADR 0007 records the pool history and why beat shares the process |
| Cookie auth plus CSRF for the web app, Bearer token for the extension and MCP | `content-queue-backend/app/core/deps.py`, `content-queue-backend/app/core/auth_cookies.py`, `content-queue-backend/app/middleware/csrf.py` | One token kept in browser storage for every client | None |
| Hybrid search with a regex query router, exact semantic lane, rank fusion | `content-queue-backend/app/core/search_router.py`, `content-queue-backend/app/core/hybrid_search.py` | Always run every lane; a model-based router; the HNSW index for the main lane; a learned reranker | None. The reranker's removal is documented only in the changelog |
| Fail open when Redis is unreachable | `content-queue-backend/app/middleware/rate_limit.py`, `content-queue-backend/app/core/task_locks.py`, `content-queue-backend/app/core/llm_client.py`, `content-queue-backend/app/core/hybrid_search.py` | Fail closed and refuse requests | None. Stated in code comments only |
| One default Celery queue | `content-queue-backend/app/core/celery_app.py` (no `task_routes` or queue settings) | Separate interactive and bulk queues | None |
| Deferred heavy columns and bounded downloads | `content-queue-backend/app/services/content.py` (`defer_heavy_columns`), `content-queue-backend/app/core/fetch.py` | Model-level deferral; reject oversized pages | Audit reports and `ARCHITECTURE.md` section on memory bounds, not an ADR |
| URL normalization as the duplicate key | `normalize_url` in `content-queue-backend/app/services/content.py`; unique index in `content-queue-backend/alembic/versions/5534fbac2811_add_unique_constraint_content_url.py`; rewrite in `content-queue-backend/alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py` | A `normalized_url` column; keep the library scan | The migration docstring explains the rewrite rules; no ADR |
| `PREFECT_ENABLED` false and the service stopped | `content-queue-backend/app/core/config.py`, `content-queue-backend/app/tasks/extraction.py`, `ARCHITECTURE.md` section 3 | Turn Prefect on as ADR 0005 planned, or delete it | Contradicts ADR 0005's plan; the audit and `ARCHITECTURE.md` record the change |
| Optional `skip` and `limit` on lists, with infinite scroll in the web list page | `content-queue-backend/app/api/lists.py`, `frontend/hooks/usePagedListContent.ts` | Required paging; a new endpoint | None. The main content list still requests one page of 50 and never pages ([Gaps and roadmap](/reliability/gaps-and-roadmap/), item 6) |

### Which deserve an ADR

Ranked by how often the question is asked and how costly a wrong assumption is.

1. **Fail open versus fail closed.** It decides what happens to limits, budgets and locks in every Redis outage, and it is spread over four files with no statement of the rule or its exceptions. A record should say when failing closed is required (for example, once the LLM budget is a hard cost control).
2. **Auth: cookies, CSRF and Bearer.** Two paths must stay consistent, and the CSRF exemption rules have a known gap. A record would hold the threat model that the code only implies.
3. **Worker topology and queues.** Combine the solo pool, embedded beat and the single queue into one record that supersedes the relevant part of ADR 0007, and records the plan for the pool change.
4. **Soft delete.** A written rule would say which paths must honour `deleted_at`, and would have caught the three that do not.
5. **Search design: router, exact lane, fusion, no reranker.** It holds the reasons for the evaluation-driven choices that now live in a changelog and in an eval report.
6. **Update ADR 0005 rather than add a new one.** Record that Temporal was dropped, Prefect is off, and the research agent runs on Celery chords.

The URL-key decision, deferred columns, optional paging and the Prefect flag are adequately covered by migration docstrings, audits and `ARCHITECTURE.md`; a one-line pointer from the decisions folder would be enough.

## What is weak or missing

Ranked by how likely each is to mislead a reader.

1. **Every record still says "Accepted" and `status: active`**, and the last update on any of them is from May to July 2026, so a reader cannot tell which are stale. A "Status today" line, as in this chapter, is the smallest fix.
2. **ADR 0005 describes a plan, not the system.** Anyone who repeats it says that Temporal runs the research agent and Prefect runs ingestion; neither is true.
3. **Several recorded numbers were never measured.** The sub-50 ms p95, the 1 to 2 s subprocess cost and the one second per 10,000 vectors do not match the measurements in this guide.
4. **No record covers evaluation, auth, deletion or queue design**, the areas where the most incidents and open gaps sit.
5. **Production-dependent status is not verified.** Whether S3 or Bedrock is configured and which backend takes the traces depends on dashboards this guide did not read.

{% say %}
There are eight decision records and they are all dated May to July. Most still hold: vectors stay in Postgres with pgvector, observability is Braintrust plus OpenTelemetry plus Sentry, the text-to-SQL tool validates every statement with a parser, the worker is a single solo process with PDFs in a subprocess, and routers only move logic into services when it is shared or long. One is narrower than it reads: the LLM layer only falls back for plain chat, not structured calls. S3 for PDFs is off by default. One is partly superseded: the record says Temporal runs the research agent and Prefect runs ingestion, but Temporal was never built, the research agent is a Celery chord, and Prefect is built but switched off and stopped in production. Separately there are ten decisions with no record, like soft delete, fail open, cookie plus CSRF auth and the single queue. I would write records first for fail-open, auth, worker topology and soft delete.
{% endsay %}

## Check yourself

{% qa "A reviewer says your ingestion runs on Prefect and your research agent on Temporal, citing your decision record. What do you answer?" %}
The record is a plan from May. Ingestion runs as a plain Celery chain; the Prefect flow exists and `PREFECT_ENABLED` is false, with the Prefect service stopped in production. Temporal was never built. The research agent is a Celery chord with its state in a database row and a sweep that marks stalled runs partial.
{% endqa %}

{% qa "ADR 0001 says HNSW gives sub-50 ms p95 search. Why is that not a safe thing to repeat?" %}
No latency measurement exists in the repository, and the main search lane does not use the HNSW index: it computes exact distances over one user's chunks (5 ms for 557 chunks, measured). The index serves nearest-k queries only. The honest claim is that exact search is fast at the current size and the triggers for a dedicated store have not been approached.
{% endqa %}

{% qa "ADR 0003 says failover to Bedrock is a one-key change. Where does that stop holding?" %}
It holds for plain chat calls: one retry on the primary, then the fallback. Structured calls (tagging, analysis, memory) go straight through the `instructor` library with no fallback, and embeddings never fall back because vectors from two models are not comparable. It also needs Bedrock credentials, which were not verified in production.
{% endqa %}

{% qa "ADR 0006 says the worst case of a missing user filter is leaking the user's own data. What does the code do instead?" %}
It requires the filter. The validator checks that every user-scoped table in the query has a direct equality predicate on the user id (or owner id for lists) and rejects the statement otherwise. The open gap is a different one: it never requires the soft-delete filter, so trashed items can be returned.
{% endqa %}

{% qa "Which undocumented decision would you write down first, and why?" %}
Fail open versus fail closed. It is applied in four files, it determines behaviour in every Redis outage, and it will need to change if the spend cap becomes a hard cost control. A record would state the rule and name the cases where failing closed is required.
{% endqa %}
