# sed.i — Dev Makefile
# Encodes the pyenv shim workaround so agents and humans don't need to rediscover it.
# Requires: pyenv at /usr/local/opt/pyenv, Python 3.11.7, Poetry 2.x, pnpm, docker

.PHONY: dev backend worker frontend migrate migrate-generate test test-backend test-frontend lint ruff tsc generate-types safari-sync safari-open help

PYENV_RUN = cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run

# ── Dev stack ──────────────────────────────────────────────────────────────────

## Start all three services in parallel (requires make 4.x)
dev:
	$(MAKE) -j3 backend worker frontend

## Start FastAPI on :8000
backend:
	$(PYENV_RUN) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

## Start Celery worker + beat scheduler
worker:
	$(PYENV_RUN) celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat

## Start Next.js on :3000
frontend:
	cd frontend && pnpm dev

# ── Database ───────────────────────────────────────────────────────────────────

## Apply all pending Alembic migrations
migrate:
	$(PYENV_RUN) alembic upgrade heads

## Generate a new migration (usage: make migrate-generate MSG="add_foo_column")
migrate-generate:
	$(PYENV_RUN) alembic revision --autogenerate -m "$(MSG)"

# ── Tests ──────────────────────────────────────────────────────────────────────

## Run all tests (backend + frontend)
test: test-backend test-frontend

## Run backend pytest suite
test-backend:
	$(PYENV_RUN) pytest tests/ -x -q

## Run frontend Jest suite
test-frontend:
	cd frontend && npx jest --ci --passWithNoTests

# ── Lint & type-check ──────────────────────────────────────────────────────────

## Run all linters (ruff + tsc + eslint)
lint: ruff tsc
	cd frontend && npx eslint . --max-warnings=0

## Ruff lint on backend
ruff:
	$(PYENV_RUN) ruff check app/

## TypeScript type-check on frontend
tsc:
	cd frontend && npx tsc --noEmit

# ── Type generation ────────────────────────────────────────────────────────────

## Regenerate frontend types from backend OpenAPI schema (backend must be running)
generate-types:
	cd frontend && pnpm generate-types 2>/dev/null || echo "Note: run 'make backend' first, then retry"

# ── Safari extension ───────────────────────────────────────────────────────────

SAFARI_RESOURCES = safari-extension/sed.i/sed.i Extension/Resources
SAFARI_PROJECT   = safari-extension/sed.i/sed.i.xcodeproj

## Sync Chrome extension changes into Safari Resources folder
safari-sync:
	rsync -a --delete extension/background/ "$(SAFARI_RESOURCES)/background/"
	rsync -a --delete extension/content/    "$(SAFARI_RESOURCES)/content/"
	rsync -a --delete extension/icons/      "$(SAFARI_RESOURCES)/icons/"
	rsync -a --delete extension/lib/        "$(SAFARI_RESOURCES)/lib/"
	rsync -a --delete extension/popup/      "$(SAFARI_RESOURCES)/popup/"
	cp extension/manifest.json "$(SAFARI_RESOURCES)/manifest.json"
	@echo "Synced extension/ → $(SAFARI_RESOURCES). Rebuild in Xcode (⌘B) to apply."

## Open the Safari extension Xcode project
safari-open:
	open "$(SAFARI_PROJECT)"

# ── Help ───────────────────────────────────────────────────────────────────────

help:
	@grep -E '^## ' Makefile | sed 's/## /  /'
