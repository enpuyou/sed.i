# Testing Standards

Load this when writing or running tests.

---

## Run commands

```bash
# All tests (uses Makefile)
make test

# Backend only
make test-backend
# or directly:
cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/ -x -q

# Frontend only
make test-frontend
# or directly:
cd frontend && npx jest --ci --passWithNoTests

# Single backend test file
cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/test_content_api.py -v
```

---

## Backend test conventions

- Tests live in `content-queue-backend/tests/`
- Test files: `test_<module>.py`
- Framework: pytest with a shared DB session (xdist disabled — tests share one DB)
- **Do not mock the database** — tests use a real test DB. This caught real migration bugs.
- Test isolation: use unique emails/URLs per test (e.g. `f"user_{uuid4()}@test.com"`)
- Auth: create a test user and get a JWT token in a fixture or at the top of each test

---

## Frontend test conventions

- Tests live in `frontend/__tests__/`
- Framework: Jest + React Testing Library
- Mock external API calls via `jest.mock('../../lib/api')`
- Test through the component interface (what the user sees), not internal state
- Don't test implementation details — tests should survive refactors

---

## What to test

For new backend endpoints:
- [ ] Happy path (201/200 with correct response shape)
- [ ] Auth required (401 without token)
- [ ] Not found (404 for missing resource)
- [ ] Cross-user isolation (user A cannot access user B's data)
- [ ] Validation (422 for missing required fields)

For new frontend components:
- [ ] Renders without crashing
- [ ] Shows loading state during async ops
- [ ] Shows error state on API failure
- [ ] Happy path callback is called with correct args

---

## Lint before committing

```bash
make lint
# Which runs:
# - ruff check app/ (backend)
# - tsc --noEmit (frontend type check)
# - eslint . --max-warnings=0 (frontend lint)
```
