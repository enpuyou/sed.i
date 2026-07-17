# Plan: Persistent Research Memory
Date: 2026-07-17
Status: Draft

## Goal

Give the research brief planner semantic memory across runs. After each completed
`ResearchRun`, extract structured per-sub-question memory entries with embeddings
into a `research_memory` table. At planning time, retrieve the most semantically
similar past entries via pgvector and inject them into the planner's system message
so it knows what the library previously answered, where coverage was thin, and which
angles have already been explored. Long-term, connect to the nightly memory
consolidation task.

## Non-goals

- No UI changes. This is entirely backend.
- No change to the `UserProfile` / `user_memory_events` schema (nightly consolidation
  hookup is Phase 4, and it only reads `research_memory` as an additional input —
  it does not restructure existing memory tables).
- No cross-user memory. All lookups are scoped by `user_id`.
- No surfacing of past brief content to the user (no "you asked this before" UI).
- No backfill of historical `ResearchRun` rows into `research_memory` — starts cold,
  accumulates going forward.

## Current state

Each `ResearchRun` starts cold. The planner's system message has no knowledge of:
- Questions the user has researched before
- Sub-questions that returned `coverage_assessment: none` in past runs
- Which articles were used in prior syntheses
- Topics where the user's library has systematic gaps

The only cross-iteration context today is `prior_context` inside a single run
(current-run `none`-coverage sub-questions injected on resume). This is intra-run,
not cross-run.

The `UserProfile` / `user_memory_events` nightly consolidation exists but the
research pipeline does not read from it. The gap is a known item in
`docs/design/systems/agentic-features.md`.

## Architecture decisions

---

**Decision: Where to store research memory**

Options:
1. Query past `ResearchRun` rows directly at planning time
   - Pro: no new table; works today
   - Con: O(N) full JSON scan; no similarity search; grows unboundedly; surfaces
     unrelated runs; raw sub-question strings, not normalized
2. Dedicated `research_memory` table with pgvector embedding per entry
   - Pro: similarity search (sub-linear); only surfaces related entries; normalized;
     prunable; can feed into nightly consolidation
   - Con: new table + migration + extraction task

**Recommendation:** Option 2. The entire retrieval stack already uses pgvector;
adding one more table is consistent. The cost of option 1 compounds with usage.

**Reversibility:** Medium — migration is one-way but the table can be cleared or
ignored if the approach changes.

---

**Decision: Granularity of memory entries**

Options:
1. One row per `ResearchRun` (question-level)
   - Pro: simple; small table
   - Con: similarity search on the question string only — misses sub-question topics
     that are different from the top-level question
2. One row per sub-question per `ResearchRun`
   - Pro: embeddings per sub-question = higher precision retrieval; gaps and
     coverage status are inherently sub-question-level
   - Con: more rows; slightly more complex extraction

**Recommendation:** Option 2. Sub-questions are the unit of retrieval and coverage
assessment, so they are the natural unit of memory.

**Reversibility:** Easy — can aggregate to question-level in queries even if rows
are sub-question-level.

---

**Decision: Extraction timing**

Options:
1. Inline in `verify_synthesis` (same Celery task, before `status = "done"`)
   - Pro: atomic with the run completing; no second task
   - Con: adds latency to the user-visible completion event; extraction failure
     blocks the run from reaching `done`
2. Separate Celery task triggered by `verify_synthesis` (fire-and-forget)
   - Pro: extraction failure doesn't affect the run status; can be retried
     independently
   - Con: small window where the run is `done` but memory not yet written

**Recommendation:** Option 2 — fire `extract_research_memory.delay(run_id)` at
the end of `verify_synthesis` after the status is written. Memory is best-effort;
it should not gate the user-facing result.

**Reversibility:** Easy.

---

**Decision: Memory injection format in planning prompt**

Options:
1. Inject raw sub-question strings from past runs
   - Pro: literal and precise
   - Con: vocabulary varies across runs; wastes tokens on near-duplicate phrasing
2. Inject structured summary: topic, coverage quality, gap description
   - Pro: normalized; LLM can reason about coverage quality directly; more compact
   - Con: slightly more work to format

**Recommendation:** Option 2. Structure lets the planner make better decisions
("this angle was tried twice with no coverage — try a different framing") vs.
just knowing the raw past phrasing.

**Reversibility:** Easy — it's a prompt string; change the format function.

---

**Decision: How many past entries to retrieve**

Retrieve top-k by cosine similarity on the planning question embedding.

- k = 5 entries by default (configurable via `RESEARCH_MEMORY_K` setting)
- Similarity threshold: 0.75 (don't inject irrelevant memories)
- Entries older than `RESEARCH_MEMORY_MAX_AGE_DAYS` (default: 90) excluded

This keeps the injected context compact. At 5 entries × ~80 tokens each = ~400
tokens per planning call — well within the prefix cache window.

---

## Schema

```sql
CREATE TABLE research_memory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id          UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    sub_question    TEXT NOT NULL,
    topic_embedding VECTOR(1536),        -- text-embedding-3-small
    coverage        TEXT NOT NULL,        -- "full" | "partial" | "none"
    topic_summary   TEXT,                 -- 1-2 sentences: what the library said
    gap_description TEXT,                 -- only when coverage="none"
    source_item_ids UUID[],               -- article IDs that contributed
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON research_memory USING ivfflat (topic_embedding vector_cosine_ops);
CREATE INDEX ON research_memory (user_id, created_at);
```

Model: `app/models/research_memory.py`
Migration: `alembic/versions/<rev>_add_research_memory.py`

---

## Phases

### Phase 1 — Schema + model (Foundation)

**Goal:** Create the `research_memory` table and SQLAlchemy model.
**Entry criteria:** Nothing special — this is the foundation.

**Changes:**
1. `app/models/research_memory.py` (new): SQLAlchemy model with pgvector `VECTOR`
   column, all fields above, standard UUID PK.
2. `app/models/__init__.py`: register the new model.
3. `alembic/versions/<rev>_add_research_memory.py` (new): `CREATE TABLE` +
   `CREATE INDEX USING ivfflat` migration.

**Exit criteria:**
- [ ] `alembic upgrade head` runs without error
- [ ] `alembic downgrade -1` drops the table cleanly
- [ ] Model importable: `from app.models.research_memory import ResearchMemory`

**Estimated scope:** 3 files, ~80 lines

---

### Phase 2 — Extraction task (Post-synthesis writer)

**Goal:** After `verify_synthesis` marks a run `done`, extract per-sub-question
memory entries and write them to `research_memory`.
**Entry criteria:** Phase 1 complete and migrated.

**Changes:**
1. `app/tasks/research_memory.py` (new):
   - `extract_research_memory(run_id, db)` — reads the completed `ResearchRun`,
     iterates `subagent_results`, generates a compact `topic_summary` (LLM call,
     `gpt-4o-mini`, 1-2 sentences per entry), embeds `sub_question` via
     `llm_client.embed`, writes one `ResearchMemory` row per sub-question.
   - `extract_research_memory_task` — Celery wrapper.
   - Uses `TASK_MEMORY_RESEARCH` task tag (new constant in `llm_client.py`).
2. `app/core/llm_client.py`: add `TASK_MEMORY_RESEARCH = "memory_research"` constant
   and `LLM_MODEL_MEMORY_RESEARCH_OPENAI / _BEDROCK` settings keys.
3. `app/core/config.py`: add the two new model settings with defaults
   (`gpt-4o-mini`, `amazon.nova-lite-v1:0`).
4. `app/tasks/research.py`: in `verify_synthesis`, after `run.status = "done"` and
   `db.commit()`, fire `extract_research_memory.delay(run_id)`.

**Extraction logic per sub-question:**
```python
for sr in run.subagent_results:
    sub_question = sr["sub_question"]
    coverage = sr["coverage_assessment"]      # "full" | "partial" | "none"
    article_ids = [a["id"] for a in sr.get("articles", [])]

    # LLM call only for full/partial coverage (none = no articles to summarize)
    topic_summary = None
    if coverage != "none" and sr.get("articles"):
        topic_summary = _summarize_for_memory(sr, sub_question)   # ~50 tokens

    gap_description = None
    if coverage == "none":
        gap_description = sr.get("articles", [{}])[0].get("article_summary") or (
            f"No articles in library address: {sub_question}"
        )

    embedding = llm_client.embed(sub_question).embeddings[0]

    db.add(ResearchMemory(
        user_id=run.user_id,
        run_id=run.id,
        sub_question=sub_question,
        topic_embedding=embedding,
        coverage=coverage,
        topic_summary=topic_summary,
        gap_description=gap_description,
        source_item_ids=[uuid.UUID(i) for i in article_ids],
    ))
db.commit()
```

**Exit criteria:**
- [ ] After a live research run completes, `research_memory` rows appear in DB
- [ ] Each row has a non-null `topic_embedding`
- [ ] `coverage` values match the corresponding `subagent_results` entry
- [ ] Unit test: mock `ResearchRun` with 3 sub-questions (full/partial/none),
      call `extract_research_memory`, assert 3 rows written with correct `coverage`

**Estimated scope:** 3 files, ~120 lines

---

### Phase 3 — Planning-time retrieval + injection (The actual fix)

**Goal:** At the start of each new `run_research_lead`, retrieve semantically
similar past `research_memory` entries for this user and inject them into the
planner's system message.
**Entry criteria:** Phase 2 complete; at least one completed run has written memory.

**Changes:**
1. `app/tasks/research.py`:
   - New helper `_fetch_research_memory(user_id, question_embedding, db, k, max_age_days)`:
     pgvector cosine similarity search on `topic_embedding`, filtered by `user_id`
     and `created_at > now() - interval`, returns top-k rows sorted by similarity.
   - New helper `_format_memory_context(entries)`: formats rows into a compact
     injection block for the planner:
     ```
     Past research context (from your library, previous sessions):
     - "does AI reduce cognitive load?" → covered (2 articles). Summary: ...
     - "empirical AI safety benchmarks" → no coverage. Gap: library lacks empirical studies on this angle.
     - "transformer attention mechanisms" → covered (4 articles). Summary: ...
     ```
   - In `run_research_lead`: before the planning call, embed `run.question`, call
     `_fetch_research_memory`, format the result, prepend to the planner's `prior_context`.
     Skip on resume (resume already has intra-run context; cross-run context injected
     only on the first iteration).
2. `_PLANNING_PROMPT` system message: add a section after the existing rules:
   ```
   Past research context (if provided):
   - Use it to avoid re-generating sub-questions for topics the library has already answered well.
   - If a past topic had "no coverage", try a different vocabulary or narrower framing — do not re-ask the identical sub-question.
   - Past context is informational only — it does not constrain the sub-questions you generate.
   ```
3. `app/core/config.py`: add `RESEARCH_MEMORY_K: int = 5` and
   `RESEARCH_MEMORY_MAX_AGE_DAYS: int = 90` settings.

**Exit criteria:**
- [ ] On a second run with the same (or similar) question, the planner's Braintrust
      span shows `memory_entries_injected > 0` in its input metadata
- [ ] Unit test: mock `_fetch_research_memory` to return 2 entries, assert the
      formatted block appears in the messages passed to `structured_chat`
- [ ] If no entries exist (first-ever run), `prior_context` is empty string — no
      change in behavior

**Estimated scope:** 2 files, ~80 lines

---

### Phase 4 — Nightly consolidation hookup (Long-term)

**Goal:** The nightly `consolidate_memory` task reads recent `research_memory`
entries as an additional input signal when building/updating `UserProfile`.
**Entry criteria:** Phase 3 complete and running in production with data.

**Changes:**
1. `app/tasks/memory.py`: in the activity assembly step, query `research_memory`
   for entries since `last_consolidated` and include a `research_gaps` section
   in the activity payload:
   ```
   Recent research gaps (topics not covered by library):
   - "empirical AI safety benchmarks" (3 searches, still no coverage)
   - "transformer interpretability at scale" (2 searches, no coverage)
   ```
2. `_CONSOLIDATION_PROMPT` (both bootstrap and incremental): add a `research_gaps`
   input section and a `persistent_gaps` output field in `UserProfile` — free-form
   text summarizing systematic gaps in the user's library based on research history.
3. `app/models/memory.py`: add `persistent_gaps` column to `UserProfile`.
4. Migration: add the column.

**Exit criteria:**
- [ ] After nightly consolidation runs, `UserProfile.persistent_gaps` is populated
      for users with research history
- [ ] The field reflects recurring `none`-coverage sub-questions, not one-off gaps

**Estimated scope:** 3 files, ~60 lines (on top of existing consolidation logic)
**Note:** Phase 4 can be deferred until Phase 3 has been running long enough to
accumulate meaningful data (suggested: after 2 weeks in production).

---

## Risks

**Risk:** pgvector IVFFlat index requires `lists` parameter tuning for accuracy.
Default `lists=100` is fine for < 1M rows. At current scale this is not a concern.
**Likelihood:** Low. **Impact:** Low (degraded recall, not an error).
**Mitigation:** Start with `lists=100`; re-index if the table grows past 500k rows.

---

**Risk:** Embedding call in extraction task adds latency and cost per sub-question.
5 sub-questions × `text-embedding-3-small` = ~$0.0001 per run. Negligible.
**Likelihood:** N/A. **Impact:** Negligible.

---

**Risk:** Memory injection makes the planner more conservative (avoids re-asking
already-covered topics even when a follow-up question warrants it).
**Likelihood:** Medium. **Impact:** Medium (briefs miss angles the user actually wants).
**Mitigation:** The injected block says "informational only — does not constrain".
If regressions appear in evals, the injection can be gated behind a `use_memory`
flag in the budget dict.
**Detection:** Watch `question_fidelity` dimension in subsequent eval runs.

---

**Risk:** Extraction task fires but the run result is not yet committed (race).
**Likelihood:** Low — `verify_synthesis` commits before firing the task.
**Impact:** Low — task reads from DB; if the row is missing it logs and exits.
**Mitigation:** Task guards with `if not run or run.status != "done": return`.

---

## Verification strategy

### Automated
- Unit tests in `tests/test_research_memory.py`:
  - Extraction: mock `ResearchRun`, call `extract_research_memory`, assert rows + embeddings
  - Retrieval: insert 3 `ResearchMemory` rows with known embeddings, call
    `_fetch_research_memory` with a query embedding, assert top-k ordering
  - Injection: mock retrieval, assert formatted block appears in planning messages
- Existing `tests/test_golden_paths.py` must still pass (no regression in pipeline behavior)
- `ruff check` + `pytest tests/ -x -q` must pass

### Manual
- Run two research questions on the same theme, 5 minutes apart
- Check Braintrust: second run's planning span should show memory entries injected
- Verify the second brief does not re-ask sub-questions the first run answered fully
- Verify the second brief reformulates (different vocab) any sub-question the first run
  found no coverage for

---

## Open questions

None — scope is clear. Ready to start Phase 1.
