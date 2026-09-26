# 2026-07-17 — Research Memory + Test Cleanup

What shipped: Persistent cross-run research memory — after each completed ResearchRun the pipeline extracts per-sub-question memory entries with embeddings into a `research_memory` table; at planning time semantically similar past entries are injected into the planner, ending cold-start on repeated topics. Recurring knowledge gaps propagate to the user profile via the nightly consolidation task. Nine stale/duplicate/vacuous tests removed and one real test fixed (pagination rate-limit handling).

Product doc: docs/design/product/research-brief-workflow.md
API changes: additive — new `POST /research`, `GET /research/{run_id}`, `POST /research/{run_id}/resume` endpoints; two new DB tables (`research_runs`, `research_memory`); new `persistent_gaps` column on `user_profiles`
Deploy order: backend first (alembic upgrade required before frontend deploy)
