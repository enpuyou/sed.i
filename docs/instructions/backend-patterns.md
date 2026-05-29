---
type: instruction
status: active
last_updated: 2026-05-28
consumer: agent
---

# Backend Patterns

Load this when doing any backend (FastAPI / SQLAlchemy / Celery) work.

---

## Error response shape — always `{ detail: string }`

Every error response must use `detail`. Global exception handlers in `app/main.py`
sanitize 422 (validation) and 500 (server) into this shape. Custom errors:

```python
raise HTTPException(status_code=404, detail="Content item not found")

# For structured payloads (e.g. 409 duplicate), JSON-encode into detail:
raise HTTPException(
    status_code=409,
    detail=json.dumps({"message": "Already in your library", "existing_id": str(id)})
)
```

The frontend parses `detail` and may `JSON.parse()` it if structured data is needed.

---

## CORS headers in error responses

CORS middleware must be included in ALL responses including 429 and 500. The global
exception handlers in `app/main.py` add CORS headers manually for these cases. If you
add a new middleware that can short-circuit the response, ensure it also sets CORS headers.

---

## Running backend commands (local dev)

The Bash tool does NOT source `~/.zshrc`, so `poetry` resolves to the wrong pyenv shim.
**Always prefix backend commands with:**

```bash
cd content-queue-backend && PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec poetry run <command>
```

Or use the Makefile targets: `make test-backend`, `make lint`, `make migrate`.

---

## SQLAlchemy — query patterns

Use ORM queries, not raw `text()` SQL, except for:
- pgvector cosine similarity operations (no ORM support)
- tsvector full-text search (no ORM support)

When using `text()`, always use named bind parameters (`:param`), never f-strings.

Avoid N+1 queries: bulk-load related objects with `.filter(Model.id.in_(ids))`, not per-row
fetches inside a loop.

---

## Celery tasks

- Always use `.delay(item_id)` — pass only serializable primitives (IDs, not ORM objects).
- Tasks fetch their own DB session via `SessionLocal()`.
- Tasks should be idempotent — safe to retry on failure.
- Rate limiting uses in-memory storage and resets on worker restart.
- Check backend logs (`poetry run celery worker` stdout) for task status.

---

## Alembic migrations

```bash
# Generate
make migrate-generate MSG="describe_what_changes"
# Apply
make migrate
# Rollback one step
cd content-queue-backend && poetry run alembic downgrade -1
```

For custom SQL (pgvector indexes, partial indexes), use `op.execute()` and write manual
`upgrade()` / `downgrade()` — autogenerate won't capture these.

---

## Soft delete pattern

ContentItems are never hard-deleted. Set `deleted_at = datetime.utcnow()` to delete.
All queries that should exclude deleted items must filter `WHERE deleted_at IS NULL`.
SQLAlchemy: `.filter(ContentItem.deleted_at.is_(None))`.

---

## Rate limiting

Implemented in `app/middleware/rate_limit.py`. Limits are per-user, in-memory.
Resets on server restart. CORS headers must be present in 429 responses.

---

## Debugging

- Console errors in browser are normal when JavaScript `throw`s (browser always logs them).
- Check Celery worker stdout for task status, not the FastAPI logs.
- CORS errors on 500s usually mean the global exception handler is missing CORS headers.
