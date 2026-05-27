# Memory Usage Fixes — 2026-05-26

## Background

Railway production was reporting 3GB memory on both the FastAPI and Celery services
immediately after redeploys. The spike was tracked across two separate root causes;
neither was caused by application logic changes or the new env vars added in
`enhancement/sota-stack`.

---

## Fix 1: Startup venv reinstall removed

**Files changed:** `start.sh`, `start-celery.sh`

**Root cause:** Both startup scripts contained:
```bash
rm -rf .venv
poetry install --no-root --only main
```
Railway uses railpack, which builds and bakes the virtualenv into the Docker image at
image-build time. The startup scripts then immediately destroyed it and reinstalled all
147 packages from scratch on every container boot. This included `torch` (800MB+),
`ultralytics`, `onnxruntime`, `scipy`, `opencv`, and all other heavy dependencies —
reinstalled on every deploy even when nothing changed.

**Memory impact:** pip's install phase allocates large amounts of heap for unpacking
wheels. Railway's memory metric captured this allocation as container memory, showing
2-3GB during the install window on every boot.

**Fix:** Removed the `rm -rf .venv && poetry install` block from both scripts. The
railpack-built venv is now used as-is. Only the opencv headless swap is kept, since
`newspaper3k` pulls in `opencv-python` (GUI) but Railway is headless-only and needs
`opencv-python-headless`.

**Side effect:** Deploy time drops by ~30 seconds (no redundant installs).

---

## Fix 2: YOLO model lazy-load moved to startup

**Files changed:** `start-celery.sh`, `app/tasks/extraction_implementations.py`

**Root cause:** `_process_pdf` calls `_get_yolo_model()`, which lazily imports
`ultralytics` and `torch` on first use. This import chain initializes the full torch
runtime and loads the `yolov8n-doclaynet` model from the HuggingFace cache. Because no
application log fires until after the load completes, the spike appeared completely
silent in the Railway logs — no entries between the last beat task at 01:39:42 UTC and
the spike at 01:40 UTC.

**Memory impact:** torch (CPU) + ultralytics + model weights consume approximately
1-3GB of RSS on first load. With the solo Celery pool, this memory persists for the
life of the worker process.

**Fix (A — pre-warm):** `start-celery.sh` now runs a one-shot Python process before
starting the worker that calls `_get_yolo_model()` directly. This loads the torch +
ultralytics runtime during the deploy startup phase, where the spike is visible in
Railway's deploy logs rather than occurring silently 10-15 minutes into production
traffic.

The pre-warm uses `|| true` so a HuggingFace connectivity failure doesn't block the
worker from starting.

**Logging added:** `_get_yolo_model()` now logs a `WARNING` before and after the load
with RSS delta (from `/proc/self/status`) and peak Python allocations (via
`tracemalloc`). This makes the cost of each cold-load visible in structured logs and
searchable in Grafana.

---

## Remaining baseline memory

After both fixes, expected steady-state memory:

| Service | Expected RSS | Notes |
|---------|-------------|-------|
| FastAPI | ~200-350MB | SQLAlchemy pool, OTEL SDK, Sentry, MCP session manager |
| Celery worker | ~1.5-2.5GB | torch + ultralytics loaded at startup (pre-warmed), stays resident |

The Celery worker baseline is high because torch is a large runtime. If PDF extraction
is not a core feature, switching from YOLO → `pymupdf-layout` (onnxruntime-based,
already installed) would drop Celery's baseline to ~300-400MB. That is tracked as a
future option but was not implemented here.

---

## What was ruled out

- **Chunk embedding tasks** — confirmed not running during the 01:40 spike.
  `process_all_missing_embeddings` ran at 01:34 and 01:39 and found zero missing
  embeddings both times.
- **Env var additions** — adding new env vars to Railway has zero memory cost. The
  `pydantic-settings` `Settings` object is negligible.
- **Rolling deploy overlap** — Railway's "Sum" memory metric counts all replicas.
  During a rolling deploy, old + new containers briefly overlap, doubling the apparent
  metric. This is expected behavior, not a leak.
