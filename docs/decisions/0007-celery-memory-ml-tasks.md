# 0007 — Celery Worker Memory: ML Tasks and Pool Strategy

## Why this doc exists

The Celery worker accumulated ~1.5GB of memory from torch/ultralytics (YOLO PDF
extraction), and the pool/concurrency settings had a confusing history. This document
records the decisions made, the trade-offs, and what to do next if the situation changes.

---

## History: how `--pool=solo --concurrency=2` happened

### The original spike (Jan 14–15, 2026)

`start-celery.sh` was created at **Jan 14 22:49** with the bare minimum:

```bash
poetry run celery -A app.core.celery_app worker --loglevel=info
```

This used Celery's defaults: `--pool=prefork`, concurrency = number of CPU cores on
the Railway host. On Railway, this spawned 4–8 worker subprocesses. Each subprocess
independently imported the full dependency set — SQLAlchemy, OpenAI, fitz, trafilatura,
numpy, etc. Memory multiplied by the number of workers:

```
default prefork, 4 CPUs: 4 processes × ~300MB = ~1.2GB baseline
```

The worker hit Railway's memory limit. **One hour later**, at Jan 15 00:35, the fix
landed in commit `ed5f3fc`:

```bash
celery worker --loglevel=info --concurrency=2 --pool=solo
```

### Why `--concurrency=2` is a no-op

The same commit also updated `nixpacks.celery.toml` with only `--concurrency=2`
(without `--pool=solo`). The engineer was iterating quickly: first tried limiting
prefork to 2 workers (half the memory), then also added `--pool=solo` to the shell
script to go further (1 process). Since solo pool ignores `--concurrency`, the `2`
became dead config the moment `--pool=solo` was added. It has no effect. It can be
removed without any behaviour change.

### Why `--beat` is in the same process

`--beat` was added in commit `4d23cb9` (Feb 22, 2026) with the PDF update. Running
beat inside the same process as the worker is convenient and works correctly with
`--pool=solo` since everything runs in one thread. With prefork it's discouraged
(Celery docs recommend a separate beat process when using prefork) but not broken.

### `worker_max_tasks_per_child=1000` is also a no-op

Set in `celery_app.py` — only applies to prefork and thread pools. Solo pool runs all
tasks in the main process thread with no child recycling. The setting is ignored.

---

## What happens if you switch pools

### Option A: stay on `--pool=solo` (current)

```
Memory: 1 process × ~300MB = ~300MB baseline
Concurrency: 1 task at a time (sequential)
Task recycling: none (worker_max_tasks_per_child does nothing)
Beat: runs in same process ✓
```

### Option B: `--pool=prefork --concurrency=1`

```
Memory: 1 process × ~300MB = ~300MB baseline (same as solo)
Concurrency: 1 task at a time (same as solo)
Task recycling: worker_max_tasks_per_child NOW WORKS — process restarts after N tasks
Beat: still works with -B in same command
```

This is essentially solo with recycling. The original memory problem was
`N CPUs × deps`, not prefork itself. Limiting to `--concurrency=1` removes the
multiplication entirely. The upside: memory genuinely drains over time instead of
accumulating indefinitely.

### Option C: `--pool=prefork --concurrency=2` (the nixpacks attempt)

```
Memory: 2 processes × ~300MB = ~600MB baseline
Concurrency: 2 tasks in parallel
Task recycling: works
Beat: works with -B but Celery docs recommend running beat separately with prefork
```

Doubles memory vs current. Only justified if task throughput becomes a bottleneck.

---

## Fork safety analysis for the current codebase

Switching to prefork requires verifying that nothing open at module-import time would
be corrupted when the parent process forks into workers.

| Singleton | Initialized at module level? | Connections at fork time? | Fork-safe? |
|---|---|---|---|
| `engine = create_engine(...)` | Yes | No — pool connects lazily on first `SessionLocal()` call, which only happens inside tasks | ✓ Safe |
| `llm_client = LLMClient()` | Yes | No — `_openai_client` and `_bedrock_client` are `None` until first use | ✓ Safe |
| `celery_app = Celery(...)` | Yes | Celery manages its own broker reconnect in each forked worker | ✓ Safe |
| Sentry + OTEL | **Not initialized in the worker** | `setup_observability()` is only called in FastAPI's lifespan (`main.py`). The Celery worker starts from `start-celery.sh` with no observability setup. No background export threads exist. | ✓ Safe |
| Threading / async | None in task code | No `threading.Thread` or `asyncio` event loops in any task file | ✓ Safe |

**Conclusion:** switching to `--pool=prefork --concurrency=1` is safe for the current
codebase. The risks that would make prefork dangerous (open DB connections at fork,
background threads, async loops) do not exist here.

The one thing to watch: if SQLAlchemy connections are ever established during Celery
startup (e.g., an `on_worker_ready` signal that queries the DB), those connections
would be inherited by forked workers. As a precaution, add a `worker_init` signal
handler that calls `engine.dispose()` if this is ever added.

---

## Industry patterns for memory-heavy ML tasks

### 1. Dedicated ML inference service (most common at scale)

Run a separate service that loads the model once at startup and accepts requests. The
main worker sends an HTTP call instead of running YOLO directly.

```
Celery worker (300MB)  →  POST /extract  →  YOLO service (1.5GB, always warm)
```

**Memory:** The 1.5GB stays resident permanently in the inference service, even when
no PDFs are being processed. This is more expensive at idle than subprocess isolation.
The main worker stays lean. Use this when PDFs are frequent enough that cold-start
latency per task matters.

### 2. Prefork + `--max-tasks-per-child`

Celery's built-in mechanism. Worker processes recycle after N tasks, releasing all
accumulated memory including any loaded models.

```bash
celery worker --pool=prefork --concurrency=1 --max-tasks-per-child=20 --beat
```

**Memory:** Baseline same as solo (1 process). Memory drains every N tasks instead of
accumulating indefinitely. No architecture change required.

### 3. Subprocess isolation per task (current approach)

Run the heavy task in a child process that exits after each use. Memory freed by the OS
when the subprocess exits.

**Memory:** torch (~1.5GB) only present during active PDF extraction. Between PDFs the
worker runs at ~300MB. Cheaper at idle than a dedicated service, adds 1–2s overhead
per PDF.

---

## Current decision (as of 2026-05-27)

**Using subprocess isolation (Option 3) for YOLO.**

Rationale:
- PDF extraction is rare — 1–2s subprocess overhead is acceptable
- torch only resident during extraction, not 24/7
- No new Railway service to operate
- Main worker baseline ~300MB

**If PDFs become frequent:** promote to Option 1 (dedicated service). `_yolo_worker.py`
is already a self-contained entry point — it becomes the FastAPI handler with minimal
changes.

**If memory accumulation from other tasks becomes a problem:** switch to
`--pool=prefork --concurrency=1 --max-tasks-per-child=50`. Fork-safe as analysed above.
Remove `--concurrency=2` (no-op) at the same time.

---

## Current flags and their actual effect

| Flag | Current value | Actual effect |
|---|---|---|
| `--pool` | `solo` | 1 process, sequential tasks, no recycling |
| `--concurrency` | `2` | **No-op with solo.** Remove when changing pool. |
| `--beat` | present | Beat scheduler runs in same process — fine with solo |
| `worker_max_tasks_per_child` | `1000` (in celery_app.py) | **No-op with solo.** Would work if pool changed to prefork. |
