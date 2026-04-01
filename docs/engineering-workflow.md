# Engineering Workflow Playbook

Status: Proposed standard for this repository.
Scope: Local development, CI, Railway deployment, and coding-agent operating rules.

---

## 1) Goals

This workflow is optimized for:

- **Reliability**: deterministic installs and reproducible deploys.
- **Simplicity**: one obvious way to run backend/frontend/services.
- **Speed**: fast feedback in local dev and PR checks.
- **Longevity**: low cognitive overhead as the team and codebase grow.

---

## 2) Current-state audit (from this repo)

### What is already good

- Backend dependency management is centralized in `content-queue-backend/pyproject.toml` + `poetry.lock`.
- Backend and frontend each have CI workflows:
  - `.github/workflows/backend-ci.yml`
  - `.github/workflows/frontend-ci.yml`
- Runtime services are clearly split:
  - Web API process (FastAPI/Uvicorn)
  - Worker process (Celery)
- Local infra via `docker-compose.yml` is straightforward (Postgres+pgvector, Redis).

### Current risks / friction points

1. **Environment fragility around heavy ML wheels**
   - `torch`/`torchvision` resolution can fail on some local platforms/ABIs.
   - When `poetry install` fails mid-run, core tools like `uvicorn` may appear missing.

2. **Runtime boot does install-time work**
   - Railway start flow still performs installation and package mutation at boot in some paths (`Procfile`, shell scripts).
   - Boot-time install increases startup variance and failure surface.

3. **CI redundancy and drift risk**
   - Backend CI runs dependency install twice (`--no-root` then `--with dev`), which is redundant.
   - Frontend CI still contains debugging-oriented lockfile checks; useful temporarily, noisy long-term.

4. **Documentation gap**
   - Root `README.md` references a non-existent `DEPLOYMENT.md`.

---

## 3) Recommended long-term standard

## 3.1 Package management standard (backend)

Use **Poetry as the canonical project manager** for now (short-to-medium term), with these rules:

- Keep `pyproject.toml` + `poetry.lock` as the single source of truth.
- Keep `virtualenvs.in-project=true` and standardize on `content-queue-backend/.venv`.
- Use `poetry run ...` instead of requiring `poetry shell` in docs/commands.
- Never install backend dependencies with plain `pip` except for explicit, documented emergency hotfixes.

### Dependency policy

- Core runtime dependencies must install cleanly in Linux (CI and Railway) and macOS (developer machines).
- For platform-sensitive packages (`torch`, `torchvision`, `onnxruntime`, OpenCV):
  - prefer explicit source/index and markers where needed;
  - treat macOS + Linux lock/install compatibility as a release gate.
   - for this repo specifically: Intel macOS (`darwin` + `x86_64`) is pinned to
      `torch==2.2.2` / `torchvision==0.17.2`; Linux and Apple Silicon stay on
      the newer pins.

---

## 3.2 Local development standard

### Backend

```bash
cd content-queue-backend
poetry install --with dev
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Worker (separate terminal):

```bash
cd content-queue-backend
poetry run celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

### Infra

```bash
docker-compose up -d
```

### Local quality gate before pushing

Backend:

```bash
cd content-queue-backend
poetry run black --check app tests
poetry run ruff check app tests
poetry run pytest tests/ -q
```

Frontend:

```bash
cd frontend
npm run lint
npm test -- --watchAll=false
npm run build
```

---

## 3.3 CI standard (GitHub Actions)

### Branch policy

- Run both backend and frontend CI on PRs targeting `main`.
- Protect `main` with required checks:
  - `backend-ci / lint-and-test`
  - `frontend-ci / lint-and-build`

### Backend CI policy

- Use a **single Poetry install step**: `poetry install --with dev --no-interaction --no-root`.
- Start ephemeral Postgres+pgvector in CI; keep DB URL explicit.
- Avoid requiring external paid keys for default CI paths where possible:
  - tests that need OpenAI should be mocked and/or grouped behind optional markers.

### Frontend CI policy

- Keep strict `npm ci` + lint + test + build.
- Remove temporary “debug lockfile presence” step after lockfile reliability is confirmed.

---

## 3.4 Railway deployment standard

### Process model

Use **two Railway services** from the same backend codebase:

1. **web**: FastAPI API process
2. **worker**: Celery worker process

### Build vs release vs runtime boundaries

- **Build phase**: install dependencies from lockfile only.
- **Release phase**: run migrations (`alembic upgrade head`).
- **Runtime phase**: start app process only.

Avoid install/uninstall package mutations during process startup.

### Runtime commands

Web:

```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Worker:

```bash
poetry run celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat
```

This matches the current production worker invocation used by Railway.

### One-project-one-env storage rule

To keep local storage efficient and avoid duplicate Poetry environments for this
project:

- keep `content-queue-backend/poetry.toml` committed with `in-project = true`;
- always run Poetry from `content-queue-backend/`;
- use `poetry env list --full-path` to confirm a single active env;
- if an old cached env exists from earlier setups, remove it once:

```bash
poetry env remove --all
poetry install --with dev
```

### Fast recovery command (shell vs `.venv` drift)

If your shell points at the wrong environment (for example, `uvicorn` missing
after a successful install), run:

```bash
cd content-queue-backend
./scripts/reset_backend_env.sh
```

This script removes stale Poetry env registrations for this project, rebuilds
`content-queue-backend/.venv`, and verifies `uvicorn` is present.

### Environment variables

Use Railway variable groups and keep parity with local `.env` naming.
Minimum backend set:

- `DATABASE_URL`
- `REDIS_URL`
- `SECRET_KEY`
- `OPENAI_API_KEY`
- `POSTHOG_API_KEY` (if analytics enabled)
- `POSTHOG_HOST` (if analytics enabled)
- email provider secrets where applicable

---

## 3.5 Release workflow standard

1. PR opened from feature branch.
2. CI green (backend + frontend).
3. Reviewer confirms:
   - architecture/docs updated for behavioral changes;
   - migrations included and safe;
   - no runtime install hacks introduced.
4. Merge to `main`.
5. Railway deploy triggers from `main`.
6. Post-deploy smoke checks:
   - `/health` endpoint
   - login flow
   - content submission path
   - one Celery task completion

---

## 4) Coding-agent operating contract

These rules apply to Copilot/Claude-style coding agents working in this repo.

### 4.1 Scope and change size

- Prefer small, reviewable patches.
- Solve root cause, not symptom-only edits.
- Do not modify unrelated files opportunistically.

### 4.2 Required checks before handoff

For backend-affecting changes:

- `poetry run ruff check app tests`
- `poetry run pytest` (or targeted tests + explanation)

For frontend-affecting changes:

- `npm run lint`
- `npm test -- --watchAll=false`
- `npm run build` for route/render changes

### 4.3 Documentation discipline

- If behavior/API/schema changes: update `ARCHITECTURE.md` in same commit.
- If workflow changes: update this playbook.

### 4.4 Safety rules

- Never commit secrets.
- Never hardcode tokens/keys.
- Never bypass auth/authorization checks without explicit task requirement.
- Do not add direct `pip install ...` startup hacks for production paths.

### 4.5 PR summary format for agent output

Every agent PR summary should include:

- What changed
- Why changed
- How validated
- Risks / follow-ups

---

## 5) Roadmap for adoption (simple, low-risk)

## Phase 1 (Now)

- Adopt this document as the workflow source of truth.
- Update README links and onboarding commands to match.
- Keep Poetry as-is; stabilize installs.

## Phase 2 (Next)

- Clean backend CI duplicate install step.
- Remove temporary frontend CI debugging step.
- Ensure OpenAI-dependent tests are mocked or optional.

## Phase 3 (Later, optional)

- Evaluate `uv` in a branch for speed and lock/sync ergonomics.
- Migrate only if it materially reduces failure rate and complexity for this repo.

---

## 6) Decision record

Why this is the recommended default now:

- It minimizes disruption by keeping current tools (Poetry, GitHub Actions, Railway).
- It addresses observed pain points (partial installs, startup mutation, doc drift).
- It creates one consistent contract for humans and coding agents.

If this document and existing scripts/config disagree, update scripts/config to match this document in incremental PRs.

---

## Companion execution plan

For a phased, approval-gated implementation plan focused on UX/state/error consistency, see:

- `docs/product-quality-execution-plan.md`
