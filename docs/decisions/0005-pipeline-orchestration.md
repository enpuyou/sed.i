# ADR-0005: Pipeline Orchestration — Celery vs Prefect vs Temporal

**Status:** Accepted
**Date:** 2026-05-22

---

## Context

sed.i uses three different background processing tools:

- **Celery** — handles in-request async work (ingestion triggers, extension callbacks)
- **Prefect** (Layer 8) — wraps the ingestion pipeline as an observable DAG
- **Temporal** (Layer 7, planned) — durable execution for the multi-step research agent

These solve different problems and are not interchangeable.

---

## Decision

Each tool fills a distinct role:

| Tool | Role | Why this tool |
|---|---|---|
| **Celery** | Immediate async dispatch; in-request work | Already in use; fast fire-and-forget; Redis broker already required |
| **Prefect** | Pipeline DAG visibility + per-step retry | Lightweight, local-first (no external state), great UI for observability |
| **Temporal** | Durable multi-step agent workflows | Survives restarts, built-in saga/compensation, required for 10+ step agents |

### Why not Celery chains for observability?

Celery chains (`chain(a.s(), b.s(), c.s())`) do provide sequencing, but:
- No UI visibility — you cannot see which step is running or how long each took
- Retries apply to the whole chain, not individual steps
- Error context is lost when a step deep in the chain fails

Prefect gives each step a named box in the UI with timing, retries, and log output. For a 5-step ingestion pipeline, this is high value at low cost.

### Why not Prefect for the research agent?

Prefect tasks are not durable — a worker restart loses in-flight flow state. A research agent that runs 5–10 LLM calls over several minutes cannot tolerate this. Temporal persists workflow state to a database and can resume from exactly where it left off after a crash.

### Why not Temporal for the ingestion pipeline?

Temporal requires its own server (temporal + elasticsearch + temporal-ui), adding 3 services to docker-compose. For a 5-step linear pipeline this overhead is not justified. Prefect's local server is a single process.

---

## Consequences

- Celery stays as the trigger layer — `extract_metadata.delay(item_id)` kicks off Phase 1, then hands to Prefect when `PREFECT_ENABLED=true`
- Prefect is opt-in (default: false) — existing Celery chain is the fallback
- The ingestion plain functions (`extract_full_content_for_item`, `generate_embedding_for_item`) are extracted from Celery tasks so Prefect can call them without triggering downstream `.delay()` chains
- Temporal is reserved for Layer 7 research agent — not used for ingestion

---

## Migration trigger

Switch `PREFECT_ENABLED=true` in production when:
1. Prefect server is deployed (local dev done, cloud or self-hosted decision made)
2. At least 10 production ingestion runs have been observed in the Prefect UI to verify the flow is stable
3. Alert/notification is wired for failed flows (Prefect supports webhook notifications)
