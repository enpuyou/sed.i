---
type: instruction
status: active
last_updated: 2026-05-28
consumer: both
---

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
PYENV_VERSION=3.11.12 pyenv exec poetry run \
  pytest tests/evals/test_tagging_evals.py -v -s

# Search quality (uses OpenAI)
PYENV_VERSION=3.11.12 pyenv exec poetry run \
  pytest tests/evals/test_search_evals.py -v -s
```

### CI (GitHub Actions)

The `.github/workflows/evals-ci.yml` triggers automatically on PRs that touch `hybrid_search.py`, `tagging.py`, or `mcp/tools/**`.

MCP behavioral evals run without any secrets. Tagging and search evals need:

1. Go to GitHub repo → Settings → Secrets and variables → Actions
2. Add `OPENAI_API_KEY` as a repository secret

---

## Layers 4 + 6 — Bedrock + S3 (do these together — one Pulumi run)

Layers 4 and 6 share the same AWS account and IAM users. One Pulumi run provisions both.

**What this gives you:**
- Bedrock: swap LLM provider from OpenAI to Anthropic Claude/Amazon Titan running in AWS — same API surface, different backend
- S3: PDFs ingested from URLs are stored in S3 instead of Railway ephemeral disk — survives redeploys, accessible via presigned URLs

---

### Step 1 — Create an AWS account and get admin credentials

If you don't have an AWS account: [aws.amazon.com](https://aws.amazon.com) → Create account. Free tier is sufficient to start.

You need credentials with permission to create IAM users, policies, S3 buckets, and Budgets. The easiest way for a personal project:

**In AWS Console:**
1. Go to IAM → Users → Create user
2. Name it `sedi-admin` (or use your root account credentials temporarily — not recommended for production)
3. Attach the `AdministratorAccess` managed policy
4. Go to the user → Security credentials → Create access key → select "Local code" use case
5. Copy the `Access key ID` and `Secret access key` — you only see the secret once

**In terminal:**
```bash
# Install AWS CLI if you don't have it
brew install awscli

# Configure with the admin credentials from above
aws configure
# AWS Access Key ID: AKIA...
# AWS Secret Access Key: ...
# Default region: us-east-1
# Default output format: json

# Verify it works
aws sts get-caller-identity
# Should print your account ID and user ARN
```

---

### Step 2 — Bedrock model access

As of 2025, the Model access page has been retired. Serverless foundation models are **automatically enabled** when first invoked — no manual activation needed.

One exception: **Anthropic models** may ask first-time users to submit use case details before the first successful call. If you get an access error when testing Bedrock, go to:
AWS Console → Bedrock → Model catalog → find Claude Haiku or Sonnet → open in Playground once. That triggers the one-time use case prompt if required.

Everything else (Titan Embed) works immediately with no action needed.

---

### Step 3 — Install Pulumi

Pulumi is infrastructure-as-code — it reads `infra/__main__.py` and creates all the AWS resources declaratively. Think of it like Terraform but in Python.

```bash
brew install pulumi/tap/pulumi

# Create a free Pulumi Cloud account — this stores your infrastructure state
# (which resources exist, what their IDs are) so Pulumi can manage updates/deletes
pulumi login
# Opens browser to create account at app.pulumi.com
```

Why Pulumi stores state: without state, Pulumi can't know what already exists vs what to create. Pulumi Cloud is free for personal projects and encrypts secrets.

---

### Step 4 — Deploy the infrastructure

```bash
cd infra
PYENV_VERSION=3.11.12 pyenv exec poetry install

pulumi stack init dev   # first time only — "dev" is the environment name
pulumi config set aws:region us-east-1
pulumi up
```

Pulumi will show a preview of what it's about to create and ask for confirmation. Review it, then type `yes`.

**What gets created (from `infra/__main__.py`):**

| Resource | Name | Purpose |
|---|---|---|
| IAM Policy | `sedi-bedrock-dev` | Allows `bedrock:InvokeModel` on 3 specific model ARNs only — no wildcard |
| IAM Policy | `sedi-s3-dev` | Allows `s3:PutObject/GetObject/DeleteObject/ListBucket` on the sedi bucket only |
| IAM User | `sedi-bedrock-app-dev` | Credentials for Railway production — has both policies |
| IAM User | `sedi-bedrock-dev-dev` | Credentials for local dev — has both policies |
| Access Keys | (2 pairs) | One keypair per user — generated by Pulumi, stored encrypted in state |
| S3 Bucket | `sedi-assets-dev` | Private bucket for PDFs — all public access blocked |
| Bucket encryption | SSE-S3 | AES256 encryption at rest — no extra cost (vs KMS which charges per request) |
| Bucket lifecycle | tiered-storage | Standard → Infrequent Access after 90 days → Glacier after 365 days (cost reduction) |
| AWS Budget | `sedi-bedrock-monthly-dev` | Alerts at 80% of $20/month actual spend and 100% forecasted — email to youenpu@gmail.com |

The IAM policies are **least-privilege** — the credentials Pulumi creates can only invoke the specific 3 models and access the specific 1 bucket. They can't create EC2 instances, read other S3 buckets, or do anything else in your account.

---

### Step 5 — Copy credentials to .env

```bash
pulumi stack output env_snippet
```

This prints the exact lines to paste into `content-queue-backend/.env`:
```
AWS_ACCESS_KEY_ID=AKIA...        # dev user key — for local use
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=sedi-assets-dev
```

**Do not set `LLM_PROVIDER=bedrock` yet.** Add the three lines above but keep `LLM_PROVIDER=openai`. S3 upload is independent of the LLM provider — PDFs will start going to S3 immediately while you continue using OpenAI for inference.

---

### Step 6 — Verify S3 is working

Restart the backend and ingest any PDF URL (e.g. an arxiv paper). Then check:

```bash
aws s3 ls s3://sedi-assets-dev/pdfs/ --recursive
# Should show: pdfs/<user_id>/<item_id>.pdf
```

Or in the AWS Console: S3 → sedi-assets-dev → pdfs/

The presigned URL endpoint is also now live:
```
GET /content/{item_id}/pdf-url
→ { "url": "https://sedi-assets-dev.s3.amazonaws.com/pdfs/...?X-Amz-Signature=..." }
```
URLs expire after 1 hour (configurable via `AWS_S3_PRESIGN_EXPIRY`).

---

### Step 7 — Test Bedrock (before switching production)

Only do this after S3 is confirmed working. Run the eval suite against Bedrock to verify quality parity:

```bash
cd content-queue-backend

# MCP behavioral evals (13 tests, no LLM scoring)
LLM_PROVIDER=bedrock PYENV_VERSION=3.11.12 pyenv exec poetry run \
  pytest tests/evals/test_mcp_evals.py -v

# Tagging quality evals (LLM-scored — compare against OpenAI baseline)
LLM_PROVIDER=bedrock PYENV_VERSION=3.11.12 pyenv exec poetry run \
  pytest tests/evals/test_tagging_evals.py -v -s
```

Record the tagging score. Run again with `LLM_PROVIDER=openai`. Switch production only if within 5% (per ADR-0003).

---

### Step 8 — Production (Railway)

Use the **app user** credentials (not dev) — they have the same permissions but are a separate keypair so you can rotate them independently.

```bash
# Get the app user credentials
pulumi stack output app_access_key_id
pulumi stack output app_secret_access_key --show-secrets
```

Add to Railway backend service environment variables:
```
AWS_ACCESS_KEY_ID=<app user key>
AWS_SECRET_ACCESS_KEY=<app user secret>
AWS_REGION=us-east-1
AWS_S3_BUCKET=sedi-assets-dev
```

When ready to switch LLM provider to Bedrock in production:
```
LLM_PROVIDER=bedrock
```

Model selection is controlled per-task via `LLM_MODEL_*_BEDROCK` env vars (see the env var reference table below). The defaults are production-ready; override individual tasks to pin specific model versions.

---

## Layer 8 — Prefect pipeline observability

**What it does:** The ingestion pipeline (fetch → extract → embed → tag → chunk-embed) runs as an observable Prefect flow with per-step timing, retry tracking, and failure isolation visible in a UI.

**Default is off** (`PREFECT_ENABLED=false`). The existing Celery chain runs unchanged until you enable this.

**Architecture:** Prefect has two parts — a **server** (stores flow state, serves the UI) and a **worker** (polls the server and executes flows). In production, the server runs as a Railway service and the worker runs as a second Railway service. Locally, both run on your machine.

---

### Local

The server runs via Docker (already in `docker-compose.yml` under the `prefect` profile). The worker runs via Poetry in a separate terminal.

**Step 1 — Start the Prefect server:**
```bash
docker compose --profile prefect up prefect-server -d
# Server API at http://localhost:4200/api
# UI at http://localhost:4200
```

This starts `prefecthq/prefect:3-python3.11` with `prefect server start --host 0.0.0.0`. It's already configured in `docker-compose.yml` — no changes needed.

**Step 2 — Point the worker at the local server:**

Before starting the worker, tell Prefect where the server is:
```bash
export PREFECT_API_URL=http://localhost:4200/api
```

Or add this to your shell profile so it persists across terminals.

**Step 3 — Start the worker** (separate terminal, keep it running):
```bash
cd content-queue-backend
PREFECT_API_URL=http://localhost:4200/api PYENV_VERSION=3.11.12 pyenv exec poetry run \
  prefect worker start -p default
```

The worker connects to the local server and polls for flow runs to execute. You'll see `Worker 'ProcessWorker' started!` when it's ready.

**Step 4 — Enable in `.env`:**

```env
PREFECT_ENABLED=true
PREFECT_API_URL=http://localhost:4200/api
```

**Step 5 — Restart backend and worker:**

```bash
make backend
# worker terminal: Ctrl-C and re-run the Step 3 command (picks up new env)
```

**Step 6 — Verify:**

Ingest any article. Then open [localhost:4200](http://localhost:4200) → Flow Runs. You should see `ingest-content` with 4 tasks: `extract-full-content`, `generate-embedding`, `generate-tags`, `generate-chunk-embeddings`.

If the flow run shows as `Scheduled` but never moves to `Running`, the worker isn't connected — check that `PREFECT_API_URL` matches in both the backend env and the worker command.

---

### Production (Railway — self-hosted)

Deploy Prefect server as a Railway service, then the worker as a second service. Both use the same Docker image that's already in `docker-compose.yml`.

**Step 1 — Create the Prefect server Railway service:**

In Railway → New Service → Docker Image:

- Image: `prefecthq/prefect:3-python3.11`
- Start command: `prefect server start --host 0.0.0.0`
- Port: `4200`
- Add environment variable:

  ```env
  PREFECT_UI_URL=https://<your-prefect-service>.railway.app
  PREFECT_API_URL=https://<your-prefect-service>.railway.app/api
  ```

Railway will give the service a public URL. Note it — you'll use it for the backend and worker.

**Step 2 — Add env vars to the Railway backend service:**

```env
PREFECT_ENABLED=true
PREFECT_API_URL=https://<your-prefect-service>.railway.app/api
```

No API key needed for self-hosted. The server has no auth by default.

**Step 3 — Create the Prefect worker Railway service:**

In Railway → New Service → Docker Image:

- Image: `prefecthq/prefect:3-python3.11`
- Start command: `prefect worker start -p default`
- Add environment variable:

  ```env
  PREFECT_API_URL=https://<your-prefect-service>.railway.app/api
  ```

The worker doesn't need a public port — it only makes outbound connections to the server.

**Step 4 — Deploy all three services** (backend, prefect-server, prefect-worker) and ingest an article.

Open `https://<your-prefect-service>.railway.app` → Flow Runs to monitor.

**Important:** If `PREFECT_ENABLED=true` but the worker is down, ingestion jobs queue forever and never execute. The Celery fallback is **not** automatic once Prefect is enabled — keep `PREFECT_ENABLED=false` until the worker service is confirmed running.

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
| `LLM_PROVIDER` | 4 | No | `"openai"` | `"openai"` or `"bedrock"` — controls chat tasks only |
| `EMBED_PROVIDER` | 4 | No | `"openai"` | Always `"openai"` — change only after full re-embed migration |
| `AWS_ACCESS_KEY_ID` | 4/6 | No | `""` | From `pulumi stack output` |
| `AWS_SECRET_ACCESS_KEY` | 4/6 | No | `""` | From `pulumi stack output` |
| `AWS_REGION` | 4/6 | No | `"us-east-2"` | Must match Pulumi stack region |
| `LLM_MODEL_TAGGING_BEDROCK` | 4 | No | `"amazon.nova-micro-v1:0"` | Per-task Bedrock model override |
| `LLM_MODEL_SUMMARY_BEDROCK` | 4 | No | `"amazon.nova-lite-v1:0"` | Per-task Bedrock model override |
| `LLM_MODEL_SQL_GEN_BEDROCK` | 4 | No | `"us.anthropic.claude-sonnet-4-5-20250929-v1:0"` | Per-task Bedrock model override |
| `LLM_MODEL_INSIGHT_BEDROCK` | 4 | No | `"amazon.nova-micro-v1:0"` | Per-task Bedrock model override |
| `AWS_S3_BUCKET` | 6 | No | `""` | Empty = S3 upload skipped |
| `AWS_S3_PRESIGN_EXPIRY` | 6 | No | `3600` | Seconds |
| `PREFECT_ENABLED` | 8 | No | `false` | Keep false until server is running |
| `PREFECT_API_URL` | 8 | No | `""` | Prefect Cloud or self-hosted URL |
| `PREFECT_API_KEY` | 8 | No | `""` | Prefect Cloud only |

---

## Production deployment checklist — Railway + Vercel

What to do once per service to activate all SOTA layers in production. No code changes needed — all layers are already deployed. This is env vars + service config only.

### Railway — Backend service

Add all of these to the backend service environment variables (Settings → Variables):

```env
# Layer 1 — Braintrust
BRAINTRUST_API_KEY=bt-...

# Layer 2 — Sentry (backend)
SENTRY_DSN=https://...@o....ingest.sentry.io/...
SEDI_ENV=production

# Layer 2 — OTEL → Grafana Cloud
OTEL_SERVICE_NAME=sedi
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-west-0.grafana.net/otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64 instance:token>
OTEL_RESOURCE_ATTRIBUTES=service.namespace=my-sedi-group,deployment.environment=production

# Layer 4 — Bedrock (keep openai until eval comparison done)
LLM_PROVIDER=openai
AWS_ACCESS_KEY_ID=<app user key from: pulumi stack output app_access_key_id>
AWS_SECRET_ACCESS_KEY=<app user secret from: pulumi stack output app_secret_access_key --show-secrets>
AWS_REGION=us-east-2

# Layer 6 — S3
AWS_S3_BUCKET=sedi-assets-prod   # or sedi-assets-dev if using single stack
AWS_S3_PRESIGN_EXPIRY=3600

# Layer 8 — Prefect (keep false until worker service is running)
PREFECT_ENABLED=false
PREFECT_API_URL=https://api.prefect.cloud/api/accounts/<account-id>/workspaces/<workspace-id>
PREFECT_API_KEY=pnu_...
```

No new Railway scripts are needed. The backend service already runs FastAPI + Celery from `make backend` equivalent.

### Railway — Worker service (Celery)

The worker service needs the **same env vars** as the backend service. The easiest way: Railway supports shared variable groups — copy them or use a reference.

The worker start command should already be set. If not: `celery -A app.core.celery_app worker --loglevel=info`

### Vercel — Frontend service

Add to Vercel project settings → Environment Variables:

```env
# Layer 2 — Sentry (frontend)
NEXT_PUBLIC_SENTRY_DSN=https://...@o....ingest.sentry.io/...
SENTRY_AUTH_TOKEN=<from Sentry → Settings → Auth Tokens>
```

No other SOTA vars are needed on the frontend — it talks to the Railway backend API which handles all LLM/observability calls server-side.

### Activation order (production)

Do these in order — each one is independent and can be done on a separate day:

| Step | What | Time | Risk |
| --- | --- | --- | --- |
| 1 | Add `BRAINTRUST_API_KEY` to Railway backend | 2 min | Zero — disabled by default if key absent |
| 2 | Add `SENTRY_DSN` to Railway backend + `NEXT_PUBLIC_SENTRY_DSN` to Vercel | 5 min | Zero |
| 3 | Add OTEL vars to Railway backend | 5 min | Zero — console exporter if absent |
| 4 | Add `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_S3_BUCKET` to Railway backend | 5 min | Zero — S3 skipped if `AWS_S3_BUCKET` empty |
| 5 | Run Bedrock evals locally, compare score, then set `LLM_PROVIDER=bedrock` | 30 min | Low — OpenAI fallover on error |
| 6 | Add Prefect vars + deploy worker service + set `PREFECT_ENABLED=true` | 20 min | Low — Celery fallback if Prefect unreachable |

Steps 1–4 can be done in one Railway deploy. Steps 5–6 should be done separately after verification.
