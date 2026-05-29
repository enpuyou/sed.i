# Railway + Vercel Env Var Audit — PR #50

## What prompted this

PR #50 (`enhancement/sota-stack`) introduces Celery worker observability, Prefect pipeline
routing, and the LLM provider abstraction. Before merging, we audited Railway and Vercel
to confirm every new code path has the env vars it needs to actually fire in production.

## How we checked

Railway and Vercel MCP servers were set up in Claude Code (`~/.claude.json`, user scope)
and used to read live service variables directly without going to the dashboards.

Railway services audited:
- `content-queue Fast API` (fe420ef9)
- `celery` (d4c465ee)
- `prefect` (9741c288)

Vercel project: `content-queue` (<your-vercel-project-id>), domains `read-sedi.com`.

## Findings

### Vercel — no action needed

Frontend has `NEXT_PUBLIC_API_URL` set. PR #50 adds no new frontend env vars. The PR #50
preview deployment was already live at the `enhancement/sota-stack` branch URL.

### Railway shared vars — already well-stocked

The project-level shared variable pool already contained:
`LLM_PROVIDER`, `SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, `SEDI_ENV`, `BRAINTRUST_API_KEY`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_PROTOCOL`,
`OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SERVICE_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

### FastAPI service — gap: Prefect not wired

`PREFECT_ENABLED` and `PREFECT_API_URL` were missing. Without them the extraction task
silently falls back to the Celery chain even though PR #50 added the Prefect routing.

### Celery service — significant gap

PR #50's main observability win is `setup_worker_observability()` firing on
`worker_process_init`. But the Celery service had none of the observability vars set —
meaning every Sentry exception, every OTEL span, and every Braintrust log from the worker
was silently dropped. Prefect routing was also disabled.

The Celery service also lacked `LLM_PROVIDER`, so llm_client would have defaulted to
OpenAI implicitly rather than reading config explicitly.

## What was set

### FastAPI (2 vars added)

| Var | Value |
|-----|-------|
| `PREFECT_ENABLED` | `true` |
| `PREFECT_API_URL` | `http://<prefect-service>.railway.internal/api` |

### Celery (12 vars added)

| Var | Value |
|-----|-------|
| `LLM_PROVIDER` | `${{shared.LLM_PROVIDER}}` |
| `SENTRY_DSN` | `${{shared.SENTRY_DSN}}` |
| `SENTRY_AUTH_TOKEN` | `${{shared.SENTRY_AUTH_TOKEN}}` |
| `SEDI_ENV` | `${{shared.SEDI_ENV}}` |
| `OTEL_SERVICE_NAME` | `sedi-worker` (service-specific — keeps worker spans separate from API spans in Grafana) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `${{shared.OTEL_EXPORTER_OTLP_ENDPOINT}}` |
| `OTEL_EXPORTER_OTLP_HEADERS` | `${{shared.OTEL_EXPORTER_OTLP_HEADERS}}` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `${{shared.OTEL_EXPORTER_OTLP_PROTOCOL}}` |
| `OTEL_RESOURCE_ATTRIBUTES` | `${{shared.OTEL_RESOURCE_ATTRIBUTES}}` |
| `BRAINTRUST_API_KEY` | `${{shared.BRAINTRUST_API_KEY}}` |
| `PREFECT_ENABLED` | `true` |
| `PREFECT_API_URL` | `http://<prefect-service>.railway.internal/api` |

Shared-var references (`${{shared.*}}`) mean these stay in sync automatically if the
shared values are rotated — no need to update Celery separately.

## Prefect internal URL

Prefect's Railway private domain is `prefect.railway.internal`. We use the internal URL
(`http://<prefect-service>.railway.internal/api`) for FastAPI and Celery to keep traffic on
Railway's private network. The public URL (`prefect-production-b0dc.up.railway.app/api`)
is what the Prefect service itself advertises but should not be used for service-to-service
calls.

## Post-change state

Both services redeployed automatically when variables were saved. PR #50 is now fully
configured in production:

- Celery worker emits OTEL spans to Grafana, Sentry exceptions, and Braintrust eval logs
- FastAPI and Celery route new ingestion jobs through Prefect when `PREFECT_ENABLED=true`
- LLM provider is explicit (`openai`) on all services; switching to Bedrock requires only
  changing `LLM_PROVIDER` in shared vars + adding `AWS_REGION` if not already set
