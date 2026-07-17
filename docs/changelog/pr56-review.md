# PR #56 Review — feat(research): persistent cross-run memory

Reviewed by: fresh code-reviewer subagent (no session context)
Date: 2026-07-17

---

## High (fix before merge)

### H1 · `run_research_lead` missing `.delay` alias
**File:** `app/tasks/research.py`

Every other pipeline function has a `.delay` alias (e.g. `synthesize_run.delay = synthesize_run_task.delay`). `run_research_lead` does not. Any external caller dispatching a new run would get `AttributeError`. Asymmetric and a latent trap.

**Fix:** add `run_research_lead.delay = run_research_lead_task.delay` after line ~608.

---

### H2 · SQL interval interpolated via f-string, not parameterized
**File:** `app/tasks/research.py`, `_fetch_research_memory`, lines ~386-402

```python
cutoff = f"now() - interval '{max_age_days} days'"
rows = db.execute(text(f"... AND created_at > {cutoff} ..."), ...)
```

`max_age_days` comes from Pydantic config so it's not user-controlled today — but this violates parameterized query hygiene and will fail any security scanner.

**Fix:**
```python
rows = db.execute(
    text("... AND created_at > now() - interval :age_interval ..."),
    {..., "age_interval": f"{max_age_days} days"},
)
```

---

### H3 · `extract_research_memory_task` `max_retries=2` never calls `self.retry()` — retries don't fire on early-return
**File:** `app/tasks/research_memory.py`; `app/tasks/research.py`, lines ~1205-1218

The task is dispatched immediately after `db.commit()` in `verify_synthesis`. Under DB replication lag or connection pool stale reads the task can arrive before the commit is visible, hits the "run not ready" guard, and **returns without retrying** — silently dropping all memory entries for that run. `max_retries` only applies to uncaught exceptions, not early returns.

**Fix:** use `apply_async(..., countdown=5)` instead of `.delay()` to give the commit time to propagate:
```python
extract_research_memory_task.apply_async((run_id,), countdown=5)
```
Or add an explicit `self.retry(countdown=30)` in the "run not ready" branch.

---

### H4 · No `POST /research` endpoint — feature is not user-triggerable
**File:** `app/api/research.py`

The API only has `GET /research/{run_id}`. There is no endpoint to create a `ResearchRun` and dispatch `run_research_lead_task`. The pipeline is fully wired end-to-end in the worker but unreachable via the API.

If this is a deliberate scope cut, the PR description should say so explicitly.

---

## Medium

### M1 · All-or-nothing commit in `extract_research_memory` — one bad entry rolls back the whole run
**File:** `app/tasks/research_memory.py`, lines ~100-167

All entries are `db.add()`-ed before a single `db.commit()`. A FK violation or constraint error on any one entry rolls back all entries for the run. A single cascade-delete race on `user_id` would silently discard the entire run's memory.

**Fix:** flush + commit per entry (or use savepoints), skipping failures individually.

---

### M2 · Similarity threshold applied after SQL `LIMIT k` — can under-return relevant entries
**File:** `app/tasks/research.py`, `_fetch_research_memory`

```python
rows = db.execute("... ORDER BY embedding <=> :q LIMIT :k").fetchall()
return [r for r in rows if r.similarity >= 0.75]
```

LIMIT fires before the Python filter. If top-k rows include 3 below threshold and 2 above, only 2 are returned even though entries at k+1, k+2 might qualify.

**Fix:** push the threshold into SQL (`WHERE 1 - (topic_embedding <=> ...) >= 0.75`) or fetch `k * 2` and filter in Python.

---

### M3 · Memory injection code path never exercised in tests
**File:** `tests/test_research_tasks.py`, full pipeline test

`mock_llm.embed.return_value.embeddings` is a `MagicMock`, not a list. Iterating it to build the embedding string raises `TypeError` — caught silently by the `try/except` in `run_research_lead`, so `prior_context` is always `""` in tests. Memory injection is never tested.

**Fix:** add to mock setup:
```python
mock_llm.embed.return_value.embeddings = [[0.1] * 1536]
```

---

## Low

### L1 · `ResearchRun.user_id` has no FK to `users.id`
**File:** `app/models/research.py`, line 24

`ResearchMemory` has `ForeignKey("users.id", ondelete="CASCADE")`. `ResearchRun` does not. Deleted-user runs will never be cleaned up by cascade.

---

## Notes (not bugs)

- Migration docstring in `b2c3d4e5f6a7` says `Revises: a1b2c3d4e5f6` but variable is `c1d2e3f4a5b6` — stale comment, not a functional issue.
- `persistent_gaps` from nightly consolidation will lag one cycle behind the most recent research run due to the async fire-and-forget pattern — acceptable but worth a comment.
- `topic_embedding = NULL` rows are correctly excluded by the `IS NOT NULL` guard in `_fetch_research_memory` but there is no counter/metric tracking how many null-embedding rows accumulate.
