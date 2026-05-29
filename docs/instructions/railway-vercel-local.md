# Railway + Vercel from Local (MCP Reference)

Living document — add new patterns here as you discover them.

---

## Setup

Railway and Vercel MCP servers are registered in `~/.claude.json` (user scope, available
in all projects). They start automatically when Claude Code starts.

To verify they're connected:
```
claude mcp list
# railway: npx -y railway-mcp - ✓ Connected
# vercel: npx -y vercel-platform-mcp-server - ✓ Connected
```

To re-register if lost:
```
claude mcp add railway -s user -e RAILWAY_API_TOKEN=<token> -- npx -y railway-mcp
claude mcp add vercel  -s user -e VERCEL_TOKEN=<token>       -- npx -y vercel-platform-mcp-server
```

---

## Project IDs (look these up so you don't have to)

### Railway

| Resource | Name | ID |
|----------|------|----|
| Project | artistic-laughter | `d35cd489-0a37-4177-91c6-823e9737bed9` |
| Environment | production | `41eaea5a-63d9-489c-9576-9e24500b3948` |
| Service | content-queue Fast API | `fe420ef9-d4aa-410c-a5a1-36fd7b4a8a3c` |
| Service | celery | `d4c465ee-2eb4-4caf-b33f-c87c69053185` |
| Service | prefect | `9741c288-0bc9-4893-98d3-649bf04510e6` |
| Service | pgvector | `c5fe75c0-f35d-4896-a641-87144809d5cc` |
| Service | Redis | `eb4c485e-789e-4841-8a87-90868164df98` |

### Vercel

| Resource | Name | ID |
|----------|------|----|
| Project | content-queue | `<your-vercel-project-id>` |
| Production domain | read-sedi.com | — |
| Preview branch | enhancement/sota-stack | `content-queue-git-enhancement-sota-stack-enpu-yous-projects.vercel.app` |

---

## Common Railway operations

All of these work by asking Claude Code in natural language. The MCP tools will be called
automatically. Examples are phrased as prompts you can type.

### Read env vars

```
"show me all env vars for the celery service on Railway"
"show me the Railway shared project variables"
```

`list_service_variables` with no `serviceId` returns shared vars.
With a `serviceId` it returns service-specific vars only (shared refs not shown).

### Set env vars

```
"set PREFECT_ENABLED=true on the FastAPI and Celery Railway services"
"add SENTRY_DSN to Celery referencing the shared variable"
```

Use `${{shared.VAR_NAME}}` syntax to reference a shared var from a service.
Use literal values only when the service needs a different value than the shared one
(e.g. `OTEL_SERVICE_NAME=sedi-worker` on Celery vs `sedi` in shared).

### Check deployments and logs

```
"show me the last 5 deployments for content-queue Fast API on Railway"
"show me the build logs for the latest celery deployment"
"show me the last 50 runtime log lines for the FastAPI service"
```

Tools: `deployment_list`, `logs_build`, `logs_deployment`.

### Trigger a redeploy

```
"redeploy the content-queue Fast API service on Railway"
```

Tool: `deployment_trigger`. Useful after setting env vars if the auto-redeploy
didn't pick up correctly.

### Restart a service

```
"restart the celery service on Railway"
```

Tool: `service_restart`. Faster than a full redeploy for config-only changes.

---

## Common Vercel operations

### Check deployment status

```
"show me the latest production deployment on Vercel"
"show me all preview deployments for the enhancement/sota-stack branch"
```

Tool: `list_deployments` with `target: production` or filtering by `app`.

### Promote a preview to production

```
"promote the latest enhancement/sota-stack Vercel deployment to production"
```

Tool: `promote_deployment`. Use this to manually push a preview build to prod
without going through the Vercel dashboard.

### Roll back production

```
"roll back the Vercel content-queue production to the previous deployment"
```

Tool: `rollback_deployment`. Instant — no rebuild needed.

### Check runtime logs

```
"show me the last runtime logs for the content-queue Vercel project"
```

Tool: `get_runtime_logs`. Useful for debugging Next.js API routes and SSR errors.

---

## Env var conventions

| Pattern | When to use |
|---------|-------------|
| Shared var (`${{shared.X}}`) | Same value across FastAPI + Celery (most secrets) |
| Service-specific literal | Value differs per service (e.g. `OTEL_SERVICE_NAME`) |
| Vercel env var | Frontend-only, prefixed `NEXT_PUBLIC_` for client-side |

**Services that must have vars set explicitly** (do not auto-inherit shared):
- `celery` — always audit this service separately when adding new shared vars

**`OTEL_SERVICE_NAME` pattern:**
- FastAPI: `sedi` (set in shared, not overridden)
- Celery: `sedi-worker` (service-level override)
- This keeps Grafana traces for API and worker in separate streams

---

## Gotchas learned so far

**`railway-mcp` env var name**: The package reads `RAILWAY_API_TOKEN`, not `RAILWAY_TOKEN`.

**`project_list` tool is broken**: The railway-mcp `project_list` tool fails with a
GraphQL schema mismatch (`prEnvCopyVolData` field error) as of railway-mcp 2.2.0.
Workaround: use the known project ID directly with `service_list`, `project_environments`,
and `list_service_variables`.

**Railway shared vars are not auto-inherited**: Services do NOT automatically pick up
shared vars by virtue of being in the same project. You must either set the var directly
on the service or reference it with `${{shared.VAR_NAME}}`.

**Rate limits on bulk var updates**: `variable_bulk_set` can hit Railway's deployment rate
limit if called twice in rapid succession for the same project. Wait a few seconds and
retry — it's idempotent.

**Vercel env values are encrypted**: `list_projects` returns encrypted blobs for env var
values, not plaintext. You can see the key names but not the values. To read a value,
check it on Railway (where it's set) or look in the codebase.

**Prefect internal URL**: Use `http://<prefect-service>.railway.internal/api` for
service-to-service calls within Railway. The public URL
(`prefect-production-b0dc.up.railway.app/api`) works but routes through the public
internet unnecessarily.

---

## Adding to this document

When you discover a new pattern, gotcha, or useful prompt — add it here under the
relevant section. Include the tool name so future Claude sessions can find the right
MCP call.
