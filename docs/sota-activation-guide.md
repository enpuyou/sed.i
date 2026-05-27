# SOTA Stack Activation Guide

How to go from code-exists to actually-running for each implemented layer.
Covers local dev, the application website (localhost:3000/8000), and Railway production.

Reference: [sota-layer-plan.md](plans/sota-layer-plan.md) | [decisions/](decisions/)

---

## Status snapshot

| Layer | Name | Code | Local | Production |
|---|---|---|---|---|
| 0 | LLMClient | ✓ | ✓ active | ✓ active |
| 1 | Braintrust LLM tracing | ✓ | needs key | needs key |
| 2 | Sentry error tracking | ✓ | needs DSN | needs DSN |
| 2 | OTEL → Grafana tracing | ✓ | optional | needs endpoint |
| 3 | Eval harness | ✓ | ✓ runs via make test | needs GH secret |
| 4 | Bedrock provider | ✓ | needs pulumi + AWS | needs AWS keys |
| 6 | S3 PDF storage | ✓ | needs pulumi + bucket | needs bucket name |
| 8 | Prefect pipeline | ✓ | needs server + worker | needs Prefect Cloud |

---

## Layer 0 — LLMClient

Already active. `OPENAI_API_KEY` is set and all LLM calls route through `LLMClient`. Nothing to do.

---

## Layer 1 — Braintrust LLM tracing

**What it does:** Every OpenAI call (tagging, summarization, MCP synthesis) gets traced in Braintrust with cost, latency, input, and output. Visible at braintrust.dev dashboard.

### Local

1. Sign up at [braintrust.dev](https://www.braintrust.dev) (free tier)
2. Settings → API Keys → create one
3. Add to `content-queue-backend/.env`:
   ```
   BRAINTRUST_API_KEY=bt-...
   ```
4. Restart backend — logs should show `"Braintrust tracing enabled for LLM calls"`

### Production (Railway)

Add `BRAINTRUST_API_KEY` to the backend service environment variables.

### Verify

Ingest an article and let tagging run → check the Braintrust dashboard for a new trace.

---

## Layer 2 — Sentry error tracking

**What it does:** Backend exceptions (FastAPI, Celery, SQLAlchemy) and frontend JS errors are captured in Sentry with full stack traces.

### Backend — local + production

1. Create a project at [sentry.io](https://sentry.io) → Python → FastAPI
2. Copy the DSN
3. Add to `content-queue-backend/.env`:
   ```
   SENTRY_DSN=https://...@o....ingest.sentry.io/...
   ```
4. Add `SENTRY_DSN` to Railway backend service

### Frontend — local + production

1. Create a second Sentry project → JavaScript → Next.js
2. Copy that DSN (different from the backend one)
3. Add to `frontend/.env.local` (create if it doesn't exist):
   ```
   NEXT_PUBLIC_SENTRY_DSN=https://...@o....ingest.sentry.io/...
   ```
4. For production source maps, also add:
   ```
   SENTRY_AUTH_TOKEN=<token from Sentry → Settings → Auth Tokens>
   ```
5. Add `NEXT_PUBLIC_SENTRY_DSN` and `SENTRY_AUTH_TOKEN` to Railway frontend service

### Verify

Raise a deliberate exception in the backend (or use Sentry's "Send test event" button) and confirm it appears in the Issues tab.

---

## Layer 2 — OTEL → Grafana Cloud tracing

**What it does:** Every FastAPI request, SQL query, and Celery task becomes a trace span exported to Grafana Cloud. Makes it possible to see where time is spent during ingestion.

**This is optional for now.** Without it, OTEL still runs locally with a console exporter — you'll see span JSON in the backend logs, which is useful for debugging but not worth setting up Grafana until you need it.

### Local (console exporter — no account needed)

Already active. Leave `OTEL_EXPORTER_OTLP_ENDPOINT` empty and traces print to stdout.

### Production (Grafana Cloud)

1. Sign up at [grafana.com](https://grafana.com) → free tier
2. Go to your stack → Connections → Add new connection → OpenTelemetry (OTLP)
3. Note the endpoint URL (format: `https://otlp-gateway-prod-us-east-0.grafana.net/otlp`)
4. Create an access token with **MetricsPublisher** + **TracesPublisher** scopes
5. Base64-encode your instance ID and token: `echo -n "instanceID:token" | base64`
6. Add to Railway backend service:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-0.grafana.net/otlp
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64 string>
   ```

---

## Layer 3 — Eval harness

**What it does:** Automated quality gates for MCP tools, search, and tagging. Runs in CI on PRs that touch retrieval or MCP code.

### Local

Already works. MCP behavioral evals run as part of `make test-backend` with no additional setup.

To run the full eval suite including LLM-graded evals:

```bash
# MCP behavioral (no API key needed — already part of make test-backend)
make test-backend

# Tagging quality (uses OpenAI)
cd content-queue-backend
PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run \
  pytest tests/evals/test_tagging_evals.py -v -s

# Search quality (uses OpenAI)
PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run \
  pytest tests/evals/test_search_evals.py -v -s
```

### CI (GitHub Actions)

The `.github/workflows/evals-ci.yml` triggers automatically on PRs that touch `hybrid_search.py`, `tagging.py`, or `mcp/tools/**`.

MCP behavioral evals run without any secrets. Tagging and search evals need:

1. Go to GitHub repo → Settings → Secrets and variables → Actions
2. Add `OPENAI_API_KEY` as a repository secret

---

## Layers 4 + 6 — Bedrock + S3 (do these together — one Pulumi run)

Layers 4 and 6 share the same AWS account and IAM users. Run Pulumi once and both are provisioned.

### Step 1 — Enable Bedrock model access (AWS Console)

1. Go to [console.aws.amazon.com](https://console.aws.amazon.com) → Bedrock → Model access
2. Request access to all three:
   - `Claude Haiku 4.5` — `us.anthropic.claude-haiku-4-5-20251001-v1:0`
   - `Claude Sonnet 4.5` — `us.anthropic.claude-sonnet-4-5-20251001-v1:0`
   - `Amazon Titan Embed Text v2` — `amazon.titan-embed-text-v2:0`
3. Access is approved within minutes (usually immediate for Titan and Haiku)

### Step 2 — Install Pulumi and configure AWS

```bash
brew install pulumi/tap/pulumi
pulumi login   # creates free Pulumi Cloud account for state storage

# Configure AWS credentials (needs IAM admin access to create users + policies)
aws configure  # or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in your shell
```

### Step 3 — Deploy infra

```bash
cd infra
poetry install
pulumi stack init dev   # first time only
pulumi config set aws:region us-east-1
pulumi up
```

This creates:
- IAM user `sedi-bedrock-app-dev` (for Railway) with Bedrock + S3 permissions
- IAM user `sedi-bedrock-dev-dev` (for local dev) with same permissions
- IAM policy scoped to the three specific model ARNs
- S3 bucket `sedi-assets-dev` with SSE-S3 encryption, public access blocked, lifecycle tiering
- AWS Budget alarm at $20/month for Bedrock costs — **this alarm is created before any traffic is enabled, which is the correct order**

### Step 4 — Copy credentials to .env

```bash
pulumi stack output env_snippet
```

This prints the exact lines to paste into `content-queue-backend/.env`:
```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=sedi-assets-dev
LLM_PROVIDER=bedrock   # add this when ready to switch — keep "openai" until evals pass
```

For now, add the `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_S3_BUCKET` lines but keep `LLM_PROVIDER=openai`. S3 upload works independently of the LLM provider.

### Step 5 — Test S3 locally

Restart the backend and ingest a PDF URL. Check the AWS S3 console — you should see `pdfs/<user_id>/<item_id>.pdf` appear in the `sedi-assets-dev` bucket.

### Step 6 — Test Bedrock locally (optional, before switching production)

```bash
cd content-queue-backend
LLM_PROVIDER=bedrock PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run \
  pytest tests/evals/test_mcp_evals.py -v
```

All 13 MCP behavioral evals should pass. If they do, Bedrock is working correctly.

Also run the tagging eval to compare quality:
```bash
LLM_PROVIDER=bedrock PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run \
  pytest tests/evals/test_tagging_evals.py -v -s
```

Record the score. Compare against `LLM_PROVIDER=openai`. Switch production only if the scores are within 5% (per ADR-0003).

### Step 7 — Production (Railway)

Add to the Railway backend service:
```
AWS_ACCESS_KEY_ID=<app user key — NOT the dev user>
AWS_SECRET_ACCESS_KEY=<app user secret>
AWS_REGION=us-east-1
AWS_S3_BUCKET=sedi-assets-dev
```

When ready to switch LLM provider:
```
LLM_PROVIDER=bedrock
BEDROCK_FAST_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
BEDROCK_SMART_MODEL=us.anthropic.claude-sonnet-4-5-20251001-v1:0
```

### The presigned PDF URL endpoint

Once `AWS_S3_BUCKET` is set and a PDF has been ingested, the endpoint works:
```
GET /content/{item_id}/pdf-url
→ { "url": "https://sedi-assets-dev.s3.amazonaws.com/pdfs/...?X-Amz-Signature=..." }
```

The URL expires after 1 hour (configurable via `AWS_S3_PRESIGN_EXPIRY`). No frontend wiring exists yet — you can call this from the reader when building a "download original PDF" button.

---

## Layer 8 — Prefect pipeline observability

**What it does:** The ingestion pipeline (fetch → extract → embed → tag → chunk-embed) runs as an observable Prefect flow with per-step timing, retry tracking, and failure isolation visible in a UI.

**Default is off** (`PREFECT_ENABLED=false`). The existing Celery chain runs unchanged until you enable this.

### Local

**Step 1 — Start Prefect server:**
```bash
docker compose --profile prefect up prefect-server -d
# UI at http://localhost:4200
```

**Step 2 — Start a Prefect worker** (in a separate terminal or background process):
```bash
cd content-queue-backend
PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run \
  prefect worker start -p default
```

**Step 3 — Enable in .env:**
```
PREFECT_ENABLED=true
```

**Step 4 — Restart backend:**
```bash
make backend
```

Ingest an article. Open [localhost:4200](http://localhost:4200) → Flow Runs. You'll see `ingest-content` with 4 named steps.

### Production (Railway)

Two options:

**Option A — Prefect Cloud (easier):**
1. `prefect cloud login` in your terminal
2. Create a workspace at [app.prefect.cloud](https://app.prefect.cloud)
3. Create an API key: Settings → API Keys
4. Add to Railway backend service:
   ```
   PREFECT_ENABLED=true
   PREFECT_API_URL=https://api.prefect.cloud/api/accounts/<account-id>/workspaces/<workspace-id>
   PREFECT_API_KEY=pnu_...
   ```
5. Deploy a Prefect worker as a separate Railway service running:
   ```
   prefect worker start -p default
   ```

**Option B — Self-hosted (more control):**
Deploy the `prefect-server` container as a Railway service using the `prefecthq/prefect:3-python3.11` image with the command `prefect server start --host 0.0.0.0`. Point `PREFECT_API_URL` in the backend at its Railway internal URL.

**Important:** If `PREFECT_ENABLED=true` but no Prefect server is reachable, ingestion will fail at the flow dispatch step. Keep it `false` until a server is confirmed running.

---

## Recommended activation order

1. **Braintrust** — 5 min, immediate value, no infrastructure
2. **Sentry** — 15 min, production safety, two DSNs (backend + frontend)
3. **Layer 4 + 6 together** — 30 min, one Pulumi run covers both; S3 activates immediately, Bedrock only after eval comparison
4. **OTEL/Grafana** — skip until you need to diagnose production latency
5. **Prefect** — skip until you want pipeline visibility; zero risk keeping it off

---

## Environment variable reference

All new variables added by the SOTA layers. Add missing ones to `content-queue-backend/.env` locally and to Railway in production.

| Variable | Layer | Required | Default | Notes |
|---|---|---|---|---|
| `BRAINTRUST_API_KEY` | 1 | No | `""` | Empty = tracing disabled |
| `SENTRY_DSN` | 2 | No | `""` | Backend only |
| `NEXT_PUBLIC_SENTRY_DSN` | 2 | No | `""` | Frontend only, in `frontend/.env.local` |
| `SENTRY_AUTH_TOKEN` | 2 | No | `""` | Frontend source maps in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 2 | No | `""` | Empty = console exporter |
| `OTEL_EXPORTER_OTLP_HEADERS` | 2 | No | `""` | `Authorization=Basic <base64>` |
| `LLM_PROVIDER` | 4 | No | `"openai"` | `"openai"` or `"bedrock"` |
| `AWS_ACCESS_KEY_ID` | 4/6 | No | `""` | From `pulumi stack output` |
| `AWS_SECRET_ACCESS_KEY` | 4/6 | No | `""` | From `pulumi stack output` |
| `AWS_REGION` | 4/6 | No | `"us-east-1"` | |
| `BEDROCK_FAST_MODEL` | 4 | No | `"us.anthropic.claude-haiku-4-5-20251001-v1:0"` | |
| `BEDROCK_SMART_MODEL` | 4 | No | `"us.anthropic.claude-sonnet-4-5-20251001-v1:0"` | |
| `AWS_S3_BUCKET` | 6 | No | `""` | Empty = S3 upload skipped |
| `AWS_S3_PRESIGN_EXPIRY` | 6 | No | `3600` | Seconds |
| `PREFECT_ENABLED` | 8 | No | `false` | Keep false until server is running |
| `PREFECT_API_URL` | 8 | No | `""` | Prefect Cloud or self-hosted URL |
| `PREFECT_API_KEY` | 8 | No | `""` | Prefect Cloud only |
