# Deploy to Production

## Overview

| Layer | Platform | Trigger |
|-------|----------|---------|
| Frontend | Vercel | Auto-deploys on push to `main` |
| Backend API | Railway (`content-queue Fast API`) | Auto-deploys on push to `main` |
| Celery worker | Railway (`celery`) | Auto-deploys on push to `main` |
| Prefect server | Railway (`prefect`) | Separate service, rarely redeployed |
| Postgres + Redis | Railway (managed) | No deploys — persistent volumes |

All Railway services redeploy automatically when their linked branch updates. Vercel
does the same. There is no manual deploy step for normal merges.

---

## Pre-merge checklist

Before merging any PR to `main`, confirm:

- [ ] `/finalize` skill has been run — lint, types, tests all pass
- [ ] CI is green (backend-ci, frontend-ci, evals-ci if triggered)
- [ ] `ARCHITECTURE.md` updated in the same commit as any feature change
- [ ] Feature doc written for any customer-facing change (`docs/features/<name>.md`)
- [ ] **Env var audit** — any new `settings.*` field or new `os.getenv()` call in the PR
      must have a corresponding variable set on Railway before merging (see below)

---

## Env var audit (before merging)

Run this check for any PR that touches `app/core/config.py`, adds a new integration,
or changes LLM/observability/storage config:

1. Open Claude Code and ask: "audit env vars for PR #N — check Railway and Vercel"
2. Claude will use the Railway and Vercel MCP tools to read live service vars and compare
   against what the new code requires
3. Add any missing vars via MCP before merging (see `railway-vercel-local.md`)

**Services that each need vars set independently:**
- `content-queue Fast API` — API process
- `celery` — worker process (does NOT auto-inherit Railway shared vars via env; must be
  set explicitly or as `${{shared.VAR}}` references)

**Shared vars** (project-level, referenced by services): use these for any var that applies
to more than one service. Set once, reference with `${{shared.VAR_NAME}}`. See
`railway-vercel-local.md` for how to read and write them.

---

## Merge

1. Squash-merge PR into `main` on GitHub
2. Railway picks up the push within ~30 seconds and queues builds for API and Celery
3. Vercel picks up the push within ~30 seconds and queues a production build

---

## Verify the deployment

**Railway:**
```
# From Claude Code — ask:
"check deployment status for content-queue Fast API and celery on Railway"

# Or via CLI:
railway status --service "content-queue Fast API"
railway logs --service "content-queue Fast API" --tail
```

**Vercel:**
```
# From Claude Code — ask:
"show me the latest production deployment on Vercel for content-queue"
```

**Manual smoke test:**
- `https://api.read-sedi.com/health` → `{"status": "ok"}`
- `https://www.read-sedi.com` → loads without error
- Add a URL in the app and confirm the processing pipeline completes

---

## Rollback

**Railway** — redeploy the previous deployment:
```
# From Claude Code:
"roll back content-queue Fast API on Railway to the previous deployment"
# Claude will use mcp__railway__deployment_trigger on the prior deployment ID
```

**Vercel** — instant rollback to previous production deployment:
```
# From Claude Code:
"roll back the Vercel content-queue production deployment"
# Claude will use mcp__vercel__rollback_deployment
```

Both platforms keep deployment history and support instant rollback without a new build.

---

## Database migrations

Migrations are NOT run automatically on deploy. Run them manually after the API service
is healthy:

```bash
# SSH into Railway or run via railway CLI:
railway run --service "content-queue Fast API" \
  poetry run alembic upgrade head
```

Always run migrations after deploying schema-changing code, before the new code
serves traffic (or coordinate with zero-downtime migration patterns if the table is large).

---

## First deploy of a new service

If PR #50 introduces a new Railway service or a new environment variable category:

1. Create the service on Railway (via MCP or dashboard)
2. Wire up all required vars — use `${{shared.VAR}}` references where the value is shared
3. Set service-specific overrides (e.g. `OTEL_SERVICE_NAME` differs per service)
4. Trigger a manual deploy to confirm startup before merging
5. Document the new service/vars in `docs/changelog/YYYY-MM-DD-<topic>.md`
