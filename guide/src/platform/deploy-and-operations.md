---
layout: layouts/chapter.njk
title: Deploy and operations
part: 4
order: 5
summary: How a merged change reaches production on Vercel and Railway, which checks guard it on the way, and how the running system is watched.
verified: 2026-10-25
permalink: /platform/deploy-and-operations/
---

## In one minute

sed.i runs on four hosted pieces. The frontend is on Vercel. The API, the Celery worker, Postgres (with the pgvector extension) and Redis are services on Railway. A Cloudflare Worker sits in front of the API and answers the browser's cross-origin (CORS) preflight requests itself. Nothing in the repository's GitHub workflows deploys anything: the hosts watch the Git repository and build when `main` changes (inferred from the absence of deploy steps; the branch settings live in the hosts' dashboards and were not checked).

Before a change merges, GitHub Actions runs a backend workflow (lint, a check that `ARCHITECTURE.md` changed with the code, a single-Alembic-head check, the test suite against a real pgvector database) and a frontend workflow (lint, tests, a production build). On Railway the build uses Nixpacks, and a pre-deploy command runs database migrations once before the new API container starts. The running system reports errors to Sentry, traces to Grafana Cloud through OpenTelemetry, LLM calls to Braintrust and product events to PostHog. The honest summary of operations is that detection is thin: there are no alerts, no service-level objectives, and the health endpoint only says the process is up.

## The problem

The question this chapter answers: **how does a change get from a commit to production without breaking the running system, and how would anyone notice if it did?**

The simple design is to build on a laptop and copy the files to a server. It fails in specific ways that this project has met. A migration that runs inside every instance's start command runs once per instance and delays every boot. A build configuration that works on a laptop can be silently overridden on the host (the Procfile incident below). A deploy that starts the new container before stopping the old one briefly doubles the memory in use. And without alerting, a worker that has stopped doing work looks exactly like a quiet day.

This chapter has two halves. The first follows a change through the pipeline. The second lists what watches the result and where it does not.

## Mental model

Think of a restaurant that changes its menu without closing. The kitchen (CI) tastes every dish before it goes out. The host stand (Railway, Vercel) brings in the new line cook, has them prepare the new dishes once (the migration), and only then tells the dining room to use the new cook, while the old cook finishes the orders already on the grill (the overlap). A thermometer in the fridge (the health endpoint) says the fridge is on, not that the food is fresh.

| Term | Meaning here |
|---|---|
| CI | Continuous integration: GitHub Actions workflows that run on every pull request. |
| Nixpacks | The tool Railway uses to turn a source folder into a container image, reading `nixpacks.toml`. |
| Pre-deploy command | A command Railway runs once per deploy, after the build and before the new container serves traffic. Set in `railway.json`. |
| Alembic | The database migration tool. Each migration has a parent; two migrations with the same parent make two "heads", which breaks upgrades. |
| Rolling deploy | The new container starts, then the old one is stopped, so the service stays up. |
| Overlap | The time both containers run. Railway's `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` controls it. |
| Liveness / readiness | Liveness: "the process is running". Readiness: "the process can do its job" (database and Redis reachable). |
| SLO | Service-level objective: a stated target such as "99 percent of saves finish in 60 seconds". |

{% fig "From commit to production" %}
```text
pull request
  |  GitHub Actions: backend-ci, frontend-ci (evals-ci on some paths)
  v
merge to main
  |
  +--> Vercel: next build of frontend/ ----------> web
  |
  +--> Railway, API service
  |      Nixpacks build (nixpacks.toml)
  |      preDeploy: alembic upgrade heads
  |                 python scripts/post_deploy.py
  |      start: uvicorn ...  (old container kept until new starts)
  |
  +--> Railway, worker service
         same build; Start Command set in the dashboard:
         celery worker ... --beat   (overlap set to 0 seconds)

browser --> Cloudflare Worker (CORS) --> Railway API --> Postgres, Redis
```
{% endfig %}

## Walkthrough: pull request 58 to production

The input is a real change: pull request 58, "memory bounds" (branch `perf/memory-bounds`), merged on 2026-10-26 (date given by the project owner; the repository snapshot used for this guide is dated 2026-10-25, so it could not be checked here). It bounded how much memory the API and worker use per request, changed how URLs are stored, and added paging to the list page. The facts below come from the repository, `docs/audits/2026-10-24-memory-fix-results.md` and the project owner's notes on the deploy; where a fact was not recorded anywhere I checked, the text says so.

### Step 1: the branch

The newest commits before the merge, from the Git log at the time of writing:

{% fig "Captured: git log, newest first (subjects only)" %}
```text
c8678c7 Merge pull request #58 from enpuyou/perf/memory-bounds
7f35e43 docs(perf): production memory baseline, prod config changes, lazy-import finding
80587ce fix(api): keep highlighted duplicate in URL migration, trim fetch at the cap
f0de3fd chore(release): 0.6.0, changelog, memory-bounds conventions, strip local paths
15eaaaa fix(ui): list edits no longer discard pages loaded while they were in flight
```
{% endfig %}

The subjects follow the project's conventional-commit format (`type(scope): description`). The release commit set `VERSION` and `pyproject.toml` to 0.6.0 (both verified) and `frontend/package.json` shows 0.6.0 too. The project's rules also require `ARCHITECTURE.md` to change with the code: it has a section titled "Memory bounds (2026-10-24)" describing this work.

### Step 2: the CI checks

Two workflows run on every pull request to `main` or `develop`. A third, the eval gate, only runs when the change touches retrieval, tagging, research or MCP code. PR 58 changed chunking, fetching, list and content queries and the list page; none of the paths that trigger the eval gate (`hybrid_search.py`, `search_router.py`, `tagging.py`, `research.py`, `app/mcp/tools/`, the eval folders) appear in the change summary I read, so I infer it did not run (not verified against the pull request page).

| Workflow | What it checks |
|---|---|
| `backend-ci.yml` | ruff on `app/`; the `ARCHITECTURE.md` freshness check; the Alembic single-head check; `poetry check --lock`; an import smoke test of `app.main`; the golden-path tests; the full test suite against a pgvector Postgres container and a Redis service |
| `frontend-ci.yml` | `npm ci`, ESLint, Jest, and `next build` |
| `evals-ci.yml` | on selected paths only: MCP contract evals, search-quality evals, tagging evals, then a regression check against stored baselines; posts a summary comment on the pull request |

The two checks that encode project rules deserve a closer look.

{% fig ".github/workflows/backend-ci.yml: ARCHITECTURE.md freshness" %}
<!--src .github/workflows/backend-ci.yml-->
```bash
CHANGED=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || git diff --name-only HEAD 2>/dev/null || echo "")
APP_CHANGED=$(echo "$CHANGED" | grep -E "^(content-queue-backend/app|frontend/components|frontend/app)" || true)
ARCH_CHANGED=$(echo "$CHANGED" | grep "ARCHITECTURE.md" || true)
SKIP_FLAG=$(git log -1 --pretty=%s | grep "\[skip-arch\]" || true)
if [ -n "$APP_CHANGED" ] && [ -z "$ARCH_CHANGED" ] && [ -z "$SKIP_FLAG" ]; then
```
{% endfig %}

It fails the run when files under the backend `app/`, `frontend/components/` or `frontend/app/` changed in the latest commit and `ARCHITECTURE.md` did not, unless the commit subject contains `[skip-arch]`. It compares only the last commit against its parent (`fetch-depth: 2` fetches just that), so on a pull request it looks at the merge commit GitHub builds, and on a push of several commits it looks only at the last. It cannot tell a meaningful update from a whitespace edit, and it ignores `frontend/hooks/` and `frontend/lib/`. It is a reminder, not a proof that the document is true: the discrepancy notes for this guide list several places where `ARCHITECTURE.md` and the code disagree.

{% fig ".github/workflows/backend-ci.yml: one migration head" %}
<!--src .github/workflows/backend-ci.yml-->
```bash
HEAD_COUNT=$(poetry run alembic heads 2>/dev/null | grep -c "(head)" || true)
if [ "$HEAD_COUNT" -ne 1 ]; then
  echo "ERROR: alembic has $HEAD_COUNT heads (expected 1)."
  echo "Fix: poetry run alembic merge heads -m 'merge_heads'"
  exit 1
fi
echo "✓ alembic single head: OK"
```
{% endfig %}

Two branches that each add a migration from the same parent both pass their own tests and then break `alembic upgrade head` once merged. Counting heads catches that before the merge. The deploy command uses `alembic upgrade heads` (plural), which would apply both branches, so even this failure would not stop a deploy; the check keeps the history linear for the next person.

### Step 3: the merge

The merge commit is `c8678c7`. Nothing in the repository's workflows reacts to it by deploying. The hosts do.

### Step 4: Railway builds the API and the worker

Both Railway services point at the same folder, `content-queue-backend/`, so both build from the same `nixpacks.toml`. The install phase creates a virtual environment, installs a pinned Poetry, installs the dependencies, then swaps the GUI build of OpenCV for the headless build once at build time (the PDF layout model pulls in the GUI variant, which needs a graphics library the container does not have):

{% fig "content-queue-backend/nixpacks.toml: install phase" %}
<!--src content-queue-backend/nixpacks.toml-->
```toml
[phases.install]
cmds = [
  "python -m venv --copies /opt/venv && . /opt/venv/bin/activate && pip install poetry==2.4.1 && poetry install --no-root --only main",
  # ultralytics (YOLO PDF-layout extraction) transitively requires
  # opencv-python (the GUI variant, pulls in libGL). Poetry has no clean
  # mechanism to exclude a transitive dependency, so both variants get
  # installed side by side, pointing at the same cv2 import path — whichever
  # installs last wins. This forces headless to win, once at build time
  # (not on every boot, which was the previous behavior via the Procfile).
  ". /opt/venv/bin/activate && poetry run pip uninstall -y opencv-python opencv-python-headless",
  ". /opt/venv/bin/activate && poetry run pip install --no-deps opencv-python-headless==4.11.0.86"
]
```
{% endfig %}

The API's start command is in the same file (`uvicorn app.main:app` on `$PORT`). The worker's start command is not: it is set in the Railway dashboard, for the reason in "How it fails" below. The build log for these two builds is not stored in the repository, so build times and sizes are not reported here.

### Step 5: the pre-deploy migration

After the build and before the new API container serves traffic, Railway runs the command in `railway.json`:

{% fig "content-queue-backend/railway.json" %}
<!--src content-queue-backend/railway.json-->
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "preDeployCommand": ". /opt/venv/bin/activate && poetry run alembic upgrade heads && poetry run python scripts/post_deploy.py",
    "restartPolicyType": "ON_FAILURE"
  }
}
```
{% endfig %}

`alembic upgrade heads` applies every migration the database has not seen. PR 58 added `a7d3e9c15b42`, which rewrites stored article URLs into their normalized form (lowercase host, tracking parameters removed) so that the duplicate check can be a single indexed lookup instead of a scan of the whole library. Rows that become identical after rewriting are collapsed: one is kept and the others are soft-deleted. The migration takes a table lock for the few seconds it runs, so a save cannot slip in between the snapshot and the updates:

{% fig "alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py: lock, then normalize" %}
<!--src content-queue-backend/alembic/versions/a7d3e9c15b42_normalize_stored_content_urls.py-->
```python
def upgrade() -> None:
    conn = op.get_bind()
    # Block concurrent writes for the few seconds this takes: a row inserted
    # between the snapshot SELECT and the UPDATEs could otherwise collide with a
    # rewrite on uq_content_items_user_url_active and abort the deploy. Reads are
    # unaffected (SHARE ROW EXCLUSIVE does not block SELECT).
    conn.execute(sa.text("LOCK TABLE content_items IN SHARE ROW EXCLUSIVE MODE"))
    normalize_active_urls(conn)


def downgrade() -> None:
    # Irreversible data normalization; nothing to undo.
    pass
```
{% endfig %}

At the end it logs the counts it produced. On the production deploy the log line reported (as relayed by the project owner; the deploy log is not in the repository, so this cannot be re-checked):

{% fig "Reported: the migration's log line in production" %}
```text
normalize_stored_content_urls: {'scanned': 98, 'rewritten': 21,
                                'duplicates_soft_deleted': 0}
```
{% endfig %}

(Wrapped to fit.) That is 98 active rows examined, 21 rewritten to the normalized form, and no collisions. Compare the development database in the audit: 71 active rows, 7 to rewrite, 0 collisions. The audit also says to run the read-only report `scripts/check_url_normalization.py` against production before deploying, so these counts could be predicted. The downgrade is empty: this migration cannot be undone, which matters for rollback below.

After the migration, `scripts/post_deploy.py` builds the four HNSW vector indexes with `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (concurrent builds cannot run inside a transaction, so they live outside Alembic). The script checks `pg_indexes` first and skips any index that exists, so on an ordinary deploy it does nothing.

### Step 6: the new containers start, the old ones stop

Railway starts the new API container, then stops the old one; for a while both run. For the API this overlap is what gives a deploy without downtime. The `railway.json` above sets no health check path, so I found nothing in the repository that makes Railway wait for `/health` before switching traffic (inferred; the dashboard could hold a setting the file does not, not verified). The API's `/health` returns a fixed body:

{% fig "content-queue-backend/app/main.py: the health endpoint" %}
<!--src content-queue-backend/app/main.py#193-195-->
```python
@app.get("/health")
def health_check():
    return {"status": "healthy"}
```
{% endfig %}

The audit records that after the configuration redeploys on 2026-10-25 "the API's `/health` returned 200 afterwards". For the PR 58 deploy itself I found no recorded health result in the repository; the production memory comparison the audit asks for ("Comparing after PR #58 is deployed") had not been written down at the time of this chapter.

### Step 7: the worker overlap

The worker is where overlap matters. Production memory was queried from Railway's metrics (5-minute samples over 7 days, 2026-10-19 to 2026-10-26). The worker's maximum was 840 MB, against a median of 482 MB. The audit explains the maximum: at one sample the old and new containers were both counted, 493 MB plus 339 MB, and the next sample read 339 MB. Overlap time is controlled by `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS`; on 2026-10-25 it was set to 0 on the worker service only, and the audit reports the deploy peak fell from 840 MB to 402 MB and lasted about a minute. The 402 MB figure is reported in the audit without a stated sampling method; I did not re-measure it.

The same overlap had a second effect. The worker process runs the Celery beat scheduler inside itself (`--beat`), so while two worker containers overlapped, two schedulers existed and could fire the same periodic task twice. The beat tasks that sweep for missing work take a Redis lock (`app/core/task_locks.py`) so a second firing exits, which limits the damage; with overlap 0 the window is about a minute instead of however long the old container lingered. The API keeps the default overlap on purpose, since a longer overlap is what makes its deploys seamless and its peak (434 MB) is small.

### Step 8: the frontend

Vercel builds `frontend/` with `next build`. `frontend-ci.yml` runs the same build with `NEXT_PUBLIC_API_URL` set to a local address to prove it compiles; the production value comes from Vercel's environment settings. The frontend folder has no Vercel configuration file, so the settings are the dashboard's. For PR 58 the frontend change was the paged list page. The ordering note in the changelog says to deploy the backend first, and the frontend falls back to the number of loaded items if the API response lacks the new `content_count` field, so a frontend that arrives early still works. No Vercel deploy record is stored in the repository, so build time and the deploy id are not reported.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/nixpacks.toml` | Build for both Railway services; start command for the API |
| `content-queue-backend/railway.json` | Builder, pre-deploy command, restart policy |
| `content-queue-backend/nixpacks.celery.toml` | A worker build file that Railway never reads (see below) |
| `content-queue-backend/scripts/post_deploy.py` | Concurrent HNSW index builds, idempotent |
| `.github/workflows/backend-ci.yml`, `frontend-ci.yml`, `evals-ci.yml` | The pull-request gates |
| `.pre-commit-config.yaml`, `.githooks/pre-push`, `scripts/run_backend_pytest_precommit.sh` | Local gates |
| `cloudflare-worker/worker.js`, `cloudflare-worker/wrangler.toml` | The CORS proxy in front of the API |
| `content-queue-backend/app/core/observability.py` | Sentry and OpenTelemetry setup |
| `docs/decisions/0002-observability-stack.md` | Why those tools |

### Hosts and the proxy

The Cloudflare Worker answers CORS preflight (`OPTIONS`) requests itself from an allow-list of origins (the production web domain, local development, the Vercel domain, two Claude domains and the browser-extension schemes), and forwards every other request to Railway with the `Origin` header removed. The code comment gives the reason: Railway's edge rejected cross-origin requests with its own error. The Worker then sets the CORS headers on the response, including `Access-Control-Allow-Credentials`, which cookie authentication needs. It buffers request bodies so a redirect can replay them, and returns 502 with a JSON body if the upstream is unreachable. The origin it forwards to is a Worker secret, not in the repository.

### Environment variables

Settings are read from the environment by `content-queue-backend/app/core/config.py`; this lists names only. The groups, with examples:

| Group | Names |
|---|---|
| Core | `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `DEBUG`, `FRONTEND_URL`, `API_BASE_URL`, `COOKIE_DOMAIN` |
| Providers | `OPENAI_API_KEY`, `LLM_PROVIDER`, `EMBED_PROVIDER`, `RESEND_API_KEY`, `DISCOGS_TOKEN`, many `LLM_MODEL_*` names, `LLM_DAILY_BUDGET_USD_PER_USER` |
| Storage | `S3_STORAGE_ENABLED`, `AWS_S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| Fetch limits | `FETCH_HTML_MAX_BYTES`, `FETCH_PDF_MAX_BYTES`, `FETCH_MAX_SECONDS` |
| Observability | `SENTRY_DSN`, `BRAINTRUST_API_KEY`, `POSTHOG_API_KEY`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SERVICE_NAME`, `SEDI_ENV` |
| Pipeline | `PREFECT_ENABLED` (false in production, per the audit), `ENTITY_SEARCH_ENABLED` |
| Railway platform | `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` (worker only) |
| Frontend (public, baked at build) | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SENTRY_DSN`, `NEXT_PUBLIC_POSTHOG_KEY`, the `NEXT_PUBLIC_SHOW_*` flags |

Secrets live in Railway's variables (the project has a shared pool that services draw from) and in Vercel's settings, never in the repository. The pre-commit hook `detect-private-key` is the only automated secret check I found; there is no secret scanner in CI. Because `NEXT_PUBLIC_` values are inlined into the browser bundle at build time, a secret must never carry that prefix, and changing one needs a rebuild ([The frontend](/platform/frontend/) explains why).

### Local gates

`.pre-commit-config.yaml` runs, on commit: whitespace and merge-conflict checks, `check-added-large-files` (1,000 KB), `detect-private-key`, black and ruff (backend), the Alembic single-head check, `poetry check --lock`, a backend pytest smoke run, Prettier, ESLint with zero warnings allowed, and the frontend Jest suite. `.githooks/pre-push` (installed by `make install-hooks`) runs ruff and `tsc --noEmit`. The backend hook runs one test file by default (`tests/mcp/test_content_tools.py`) to stay fast; the full suite is left to CI.

The backend hook takes a lock so two runs do not collide on the test database. Its cleanup is wrong in the common case:

{% fig "scripts/run_backend_pytest_precommit.sh: where the lock is, and where cleanup runs" %}
<!--src scripts/run_backend_pytest_precommit.sh-->
```bash
LOCK_DIR="$(git rev-parse --git-dir)/.precommit-pytest-lock"
```
{% endfig %}

`git rev-parse --git-dir` prints a path relative to the current directory (git's documentation: "The path shown, when relative, is relative to the current working directory"), which in a normal checkout run from the repository root is `.git`. The script later runs `cd "$BACKEND_DIR"`, and its `EXIT` trap calls `rm -rf "$LOCK_DIR"` from that directory, where `.git/.precommit-pytest-lock` does not exist. The removal silently does nothing, and the lock directory stays behind. Every later run then fails at `mkdir` with "Another backend pytest pre-commit run is already in progress" until someone deletes the directory by hand. The script's own message even tells you to. In a Git worktree the path is absolute and the bug does not appear, which may be why it was not noticed. I did not run Git for this chapter, so this is reasoned from the script and git's documented behaviour; the shell part is reproduced below with the same structure (a relative lock path, a `cd`, an exit trap). Captured:

{% fig "Captured: the same lock pattern in a scratch folder (simulation, not the real script)" %}
```text
$ ./t.sh
run body ok
exit=0
$ ls -d .git/.precommit-pytest-lock
.git/.precommit-pytest-lock
$ ./t.sh
Another run is already in progress.
exit=1
```
{% endfig %}

The fix is one line: resolve the path to an absolute one (`git rev-parse --absolute-git-dir`) before the `cd`. A checkout where `.git/.precommit-pytest-lock` is absent right now only shows that the directory was removed, not that the script cleans up.

## Observability

The decision record is `docs/decisions/0002-observability-stack.md` (2026-05-22): one tool per concern instead of one vendor.

| Concern | Tool | Wired in |
|---|---|---|
| Errors | Sentry | `setup_sentry` in `app/core/observability.py` for the API and worker; `frontend/sentry.client.config.ts` for the browser |
| Request and task traces | OpenTelemetry, exported to Grafana Cloud | `setup_tracing`; instruments FastAPI, SQLAlchemy and Celery |
| LLM calls | Braintrust | `app/core/llm_client.py` (see [The LLM gateway](/llm/gateway/)) |
| Product analytics | PostHog | `frontend/lib/posthog.ts`, `app/main.py`, `app/api/auth.py` |

Each tool is off when its key is empty, so development and tests run without accounts. `setup_observability` is called in the API's start-up and `setup_worker_observability` when each worker process starts, and both catch exceptions so a broken observability setup cannot stop the service.

One number needs a correction. The task brief for this chapter, and the decision record's spirit, suggest 10 percent trace sampling for OpenTelemetry. The code sets 10 percent in one place:

{% fig "app/core/observability.py: the 10 percent" %}
<!--src content-queue-backend/app/core/observability.py-->
```python
sentry_sdk.init(
    dsn=dsn,
    ...
    # Capture 10% of transactions for performance monitoring (free tier friendly)
    traces_sample_rate=0.1,
```
{% endfig %}

The line `traces_sample_rate=0.1` is Sentry's performance sampling: 10 percent of transactions. The OpenTelemetry `TracerProvider` is created with no sampler, so unless an `OTEL_TRACES_SAMPLER` variable is set in production (which I could not check) the SDK's default applies, which keeps every trace. The frontend Sentry setup samples 10 percent of sessions for replay and 100 percent of sessions with an error, and sets no trace rate. So "10 percent" is true for Sentry transactions and replays, and unverified for Grafana. It is recorded as a discrepancy.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Hosts build from Git; no deploy step in CI | A CI job that builds an image and pushes it; infrastructure as code for all services | Least machinery for one maintainer. Cost: CI status and deploys are separate, so a red CI run does not by itself stop a deploy (whether Railway waits for checks is a dashboard setting I did not see). There is a Pulumi project in `infra/`; I did not examine what it manages. |
| Migrations in `preDeployCommand` | Run in the start command of every instance; a manual step | Once per deploy, not once per instance, and a failed migration stops the deploy before new code serves. The old code runs against the new schema for the duration, so migrations must stay compatible with the previous release. |
| Concurrent index builds in `scripts/post_deploy.py` | Inside Alembic | `CREATE INDEX CONCURRENTLY` cannot run in a transaction. Idempotent, so it costs nothing on a normal deploy. |
| One Nixpacks file for both services; worker command in the dashboard | A separate build file per service; a Procfile | Railway cannot give two services in the same folder different build files (see the incident). The cost is a start command that lives outside Git. |
| Overlap 0 on the worker only | Default overlap everywhere | The worker is a single process with a scheduler inside; two at once duplicate the scheduler and double peak memory. The API gains from overlap and is small. |
| Sentry plus OpenTelemetry plus Braintrust plus PostHog | One vendor | [ADR 0002](https://github.com/) is not linked by URL here; the record lists the alternatives (Langfuse, Honeycomb, Datadog). Each tool is the common default for its job and has a free tier. Cost: four dashboards and no single alert path. |
| `/health` as a constant | A readiness check that queries Postgres and Redis | Simple and cannot fail for a slow dependency. It also cannot tell a dead database from a healthy API. |

## How it fails

| Incident | Date | What happened | What was learned or changed |
|---|---|---|---|
| Memory 3 GB right after deploys | 2026-05-26 | Start scripts deleted the baked virtual environment and reinstalled 147 packages on every boot, including large ML libraries; the install phase was counted as container memory. Separately, the PDF layout model loaded lazily on first use and spiked memory silently. | Scripts changed to use the built environment; the model load was moved to start-up so any spike shows in deploy logs (`docs/changelog/2026-05-26-memory-fixes.md`). |
| Observability variables missing from the worker | 2026-05-28 | A pull request added worker tracing, but the worker service had none of the variables, so every worker error and span was dropped. Found by reading live Railway and Vercel settings through their MCP servers before merging. | Twelve variables were added to the worker. Lesson: new code paths need new variables in each service, and the audit of live settings belongs in the merge checklist (`docs/changelog/2026-05-28-railway-env-audit.md`). |
| 6.5 million queued messages | 2026-07-28 | Found by accident during failure-injection testing on local Redis: one task was dispatched once per analysed article instead of once per user. | The per-article dispatch was deleted (`docs/changelog/2026-07-28-failure-injection-fixes.md`). It was local, not production. |
| The Procfile that ran the wrong command | 2026-09-27 | A leftover `Procfile` defined only a `web` process. Nixpacks lets a Procfile override `nixpacks.toml`'s start command, so both the API and the Celery service ran the web server. The worker never ran Celery, and background extraction, tagging and embedding stopped making progress. | See below. |
| Two schedulers and an 840 MB peak at deploy | seen 2026-10-24 | Old and new worker containers overlapped. | `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0` on the worker (2026-10-25). |

### The Procfile incident as a lesson

The record is in `ARCHITECTURE.md` section 3, "Deploy sequencing". The belief that caused it was "Railway prefers Nixpacks config when both are present". It was wrong: a Procfile's process definitions override the `[start]` section. The file was assumed to be harmless dead code, so nobody checked what it did, and it ran in production for both services. The incident also involved other fixes (activating the virtual environment, replacing `psycopg2` with `psycopg`, clearing a dashboard Start Command that overrode the API).

Deleting the Procfile did not repair the worker. It exposed a second fact: two Railway services that share a Root Directory cannot use two different Nixpacks files. `nixpacks.celery.toml` and a `NIXPACKS_CONFIG_FILE` variable were an attempt to; that variable is not a real Nixpacks setting, since the config file is a command-line flag that Railway does not expose. Both services always built from `nixpacks.toml`. The working fix is that the worker's Start Command is set in the Railway dashboard to the Celery command with `--beat`. `nixpacks.celery.toml` stays in the repository for local `nixpacks build -c` testing only. The 2026-09-27 incident was noticed by observation, not by an alert: the distributed-systems review says an ingestion-freshness alert "would have paged within 10 minutes".

The lessons that generalise: a file that looks unused is not unused until the platform's precedence rules say so; the production start command for the worker is configuration that does not live in Git, so a rebuilt environment would not reproduce it unless the dashboard setting is recorded (it is, in `ARCHITECTURE.md`, which is why that document matters); and "the service is green" does not mean "the service does its job".

### Rollback

What the documents record, from lowest to highest cost:

1. **Configuration changes** have a written rollback: `docs/audits/2026-10-24-memory-fix-results.md` gives the commands to restore `PREFECT_ENABLED`, delete the overlap variable and bring back the stopped Prefect service.
2. **Code** can be rolled back by redeploying the previous deployment in Railway or Vercel, or by reverting the merge. Both hosts keep earlier deployments; I found no written procedure in the repository and did not test one.
3. **Migrations** are the hard part. `alembic upgrade heads` runs forward only on deploy. PR 58's migration has an empty `downgrade()`, so rolling back the code leaves the rewritten URLs in place. That is safe here because the previous code treats normalized URLs as valid input, but it is not a general property. Anything destructive needs a database backup first, which leads to the gaps below.

## What is weak or missing

Known operational gaps with their current status in the documents and the code, in order of how likely they are to matter:

| Gap | Status | Evidence |
|---|---|---|
| No tested restore | Open. The distributed-systems review records ("no documented backup, no tested restore") and suggests targets of 24 hours of data loss and 4 hours to recover, meaningful only after a timed restore. Whether Railway backups are enabled is not recorded. | `docs/design/systems/distributed-systems-guide.md` section 4.8 |
| No alerts | Open. None found in the repository; Grafana or Railway dashboards were not inspected. The September outage was found by observation. | the distributed-systems review, section 6 |
| No SLOs | Open. None defined; candidate SLIs (for example the age of the oldest unfinished save) are proposed, not built. | same |
| Liveness-only health | Open. `/health` returns a constant. There is no readiness endpoint, and `railway.json` sets no health check path. The fix is a `/ready` that runs `SELECT 1` and a Redis ping with short timeouts, keeping `/health` as liveness. | `app/main.py`, `railway.json` |
| Worker time limits do not work | Open and documented. Under the solo pool that production uses, task time limits are not enforced, and the single worker slot is shared with beat, so one hung task stops all background work. | `ARCHITECTURE.md` section 9, "Known gap" |
| Unsafe default for `DEBUG` | Open in code. `DEBUG` defaults to true in `app/core/config.py`; the debug-only test route is mounted when it is true. Production must set it false; whether it does was not checked. | `app/core/config.py`, `app/main.py` |
| Failing pre-commit lock | Open. See above. | `scripts/run_backend_pytest_precommit.sh` |
| CI is not a deploy gate | Unverified. Whether Railway or Vercel wait for GitHub checks is a dashboard setting. | no config in the repository |
| Start command outside Git | Mitigated by documentation in `ARCHITECTURE.md`; not reproducible from the repository alone. | section 3 |
| No secret scanning or dependency audit in CI | Open. Only the pre-commit private-key check exists. | `.pre-commit-config.yaml` |
| Frontend has no error boundaries | Open. See [The frontend](/platform/frontend/). | `frontend/app/` |
| Metrics for memory live only in Railway's charts | Mitigated for now by the dated baseline in the audit; no stored history in the repository. | `docs/audits/2026-10-24-memory-fix-results.md` |

Two caveats on this table. It describes the repository, not the live dashboards, which I did not and could not look at. And several rows were first noted in an earlier audit; I rechecked the ones that can be checked in code (the health endpoint, the `DEBUG` default, the missing error boundary files, the pre-commit lock script) and they still hold.

{% say %}
The frontend deploys on Vercel and the API, Celery worker, Postgres and Redis run on Railway, with a Cloudflare Worker in front of the API handling CORS. GitHub Actions gates every pull request with lint, tests against a real pgvector database, a check that the architecture doc changed with the code, and a single-migration-head check; the hosts then build from main with no deploy step in CI. Railway builds with Nixpacks and runs migrations once in a pre-deploy command, before the new API container starts. Errors go to Sentry, traces to Grafana through OpenTelemetry, LLM calls to Braintrust, and events to PostHog. The honest part is detection: there are no alerts or SLOs, the health check only says the process is alive, and there is no tested restore. The two real lessons are the Procfile incident, where a file everyone thought was dead overrode the worker's start command so Celery never ran, and the deploy overlap, where old and new worker containers together hit 840 megabytes until I set the overlap to zero on the worker.
{% endsay %}

## Check yourself

{% qa "Why do migrations run in the pre-deploy command rather than in the API's start command?" %}
The pre-deploy command runs once per deploy, before any new instance serves traffic. A start command runs on every instance and every restart, so migrations would repeat, delay boots, and with several instances could race. The cost of the pre-deploy approach is that old code briefly runs against the new schema, so migrations must be compatible with the previous release.
{% endqa %}

{% qa "The worker's peak memory was 840 MB although it normally uses about 480 MB. Was that a leak?" %}
No. The audit shows the peak was the old container (493 MB) and the new one (339 MB) both running during a deploy, and the next sample read 339 MB. Setting the overlap to 0 seconds on the worker removed most of it (reported peak 402 MB). A leak would not drop back after the old container stopped.
{% endqa %}

{% qa "Both Railway services build from the same folder. How does the worker run Celery while the API runs uvicorn?" %}
Both build from `nixpacks.toml`, whose start command is uvicorn. The worker's Start Command is overridden in the Railway dashboard with the Celery command. Railway cannot give two services in one folder different build files, so the per-service difference lives in the dashboard, not in Git.
{% endqa %}

{% qa "The health endpoint returns 200. What does that tell you, and what does it not?" %}
It tells you the API process is running and answering HTTP. It does not tell you that Postgres or Redis is reachable, that the worker is processing jobs, or that saves complete. A readiness check that queries the database and pings Redis, plus a metric for the age of the oldest unfinished save, would cover those.
{% endqa %}

{% qa "A migration has an empty downgrade. What does that change about rolling back a bad release?" %}
Rolling back the code does not undo the data change. You must be sure the previous code works with the migrated data, or restore from a backup, and the backup restore has never been tested here. Destructive migrations therefore need a verified backup before they ship.
{% endqa %}

{% qa "The pre-commit pytest script cleans up its lock directory on exit. Why does the lock stay behind?" %}
The lock path is computed as a relative path (`.git/...`) before the script changes directory into the backend folder; the cleanup runs after that change, so it removes a path that does not exist. Using an absolute git directory path fixes it.
{% endqa %}
