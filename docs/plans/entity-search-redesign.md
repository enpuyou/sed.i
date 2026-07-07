# Plan: Entity Search Redesign — Scale-Robust _entity_search
Date: 2026-07-07
Status: Draft

## Goal

Replace the current `_entity_search` implementation with one that degrades
gracefully as library size grows — no hardcoded counts, no binary hub gates,
neighbor scoring based on actual query similarity, and scoring logic extracted
into a pure function so it can be tested and evolved without a live DB.

## Non-goals

- Changes to the RRF fusion layer in `hybrid_search()` (entity lane output
  format stays the same: `list[dict]` with `match_type="entity"`)
- Changes to entity extraction, embedding, or dedup tasks
- Frontend changes — entity lane is invisible to the UI
- Changing when entity search runs (`mode="full"` only — confirmed correct)
- Migrating to a dedicated graph DB

## Current state and its problems

`_entity_search` in `app/core/hybrid_search.py` (lines 378–565):

| Problem | Root cause | Effect at scale |
|---------|-----------|-----------------|
| `LIMIT 8` on entity candidates | Hardcoded | Silently drops relevant entities in large libraries |
| `_ENTITY_HUB_ARTICLE_CAP = 4` binary gate | Arbitrary constant | "Hub" means different things at 50 vs 5000 articles; exclusion is all-or-nothing |
| Neighbor sim = `0.5 × min_anchor_sim` | Proxy, not measured | Weak anchors poison all neighbor scores; no actual query comparison |
| Expansion gate mixes two concerns | High-sim AND low-article-count | An entity can be highly relevant AND appear in many articles; blocking it is wrong |
| Scoring loop is inside the try/except | Not unit-testable | Can only be verified end-to-end against a real DB |
| `_SECONDARY_WEIGHT = 0.3` hardcoded inline | No rationale | May be wrong at any scale |

Investigation finding (from `docs/design/systems/hub-cap-investigation.md`):
the entity_relations graph currently has only ~80 edges, so expansion
contributes almost nothing. All regressions come from direct entity→article
scoring, not expansion. Hub cap is inert for current failures.

## Architecture decisions

---

**Decision 1: How to select anchor entity candidates**

**Options**:
1. `LIMIT N` (current): take top N by similarity, hardcoded
2. Sim threshold only: take all entities above `_ENTITY_SIM_THRESHOLD`
3. Threshold + per-user adaptive cap: take all above threshold, cap at
   `max(20, total_user_entities * 0.05)` to bound query cost

**Recommendation**: Option 2 (threshold only). The threshold already gates
quality. A fixed count adds no protection that the threshold doesn't provide,
and makes the results non-deterministic with library size. If query cost becomes
a concern at very large libraries (10k+ entities), a percentile cap can be added
then — but don't optimize for a problem that doesn't exist yet.

**Reversibility**: Easy — one line change in the SQL.

---

**Decision 2: How to handle hub entities in expansion**

**Options**:
1. Binary cap (current): entities with article_count > 4 are ineligible for expansion
2. IDF-only: no expansion gate; let IDF dampening in the scoring formula
   naturally reduce hub contribution proportional to `log2(2 + article_count)`
3. Proportional cap: allow expansion but limit neighbor set size to
   `max(5, 20 / article_count)` neighbors per hub

**Recommendation**: Option 2. The investigation proved the binary cap is
ineffective for current regressions. IDF dampening already penalizes hub
entities in scoring; applying a separate binary gate on expansion adds
complexity without benefit. Simplify: expand from all high-confidence anchors
(sim >= expand threshold), let IDF handle the weight. If expansion fan-out
becomes a problem (graph is dense), revisit then.

**Reversibility**: Easy — remove the `article_count <= _ENTITY_HUB_ARTICLE_CAP`
condition from the expansion filter.

---

**Decision 3: How to score neighbor entities**

**Options**:
1. Proxy: `0.5 × min_anchor_sim` (current) — fast, inaccurate
2. Direct embed: embed neighbor name+description against query at search time —
   accurate but adds N LLM calls per search
3. Precomputed at extraction time: when an entity is embedded, also store
   pairwise similarities to its graph neighbors — complex, stale on graph changes
4. Batch cosine at query time in SQL: retrieve neighbor embeddings in the same
   query, compute cosine in Postgres using `<=>` — accurate, no extra API calls,
   one extra SQL join

**Recommendation**: Option 4. Postgres can compute cosine similarity between
the stored neighbor embedding and the query vector inside the mention-fetch SQL
using a lateral join. No extra API calls, no staleness. The query becomes one
round-trip instead of two (entity candidates + mentions).

**Reversibility**: Medium — schema unchanged, just query restructuring.

---

**Decision 4: Extract scoring as a pure function**

The scoring loop (`contribution = sim / log2(2 + count)`, capped-sum) is
currently inlined inside the try/except block. Extract it to a standalone
function `score_entity_articles(mention_rows, sim_map, secondary_weight)` that
takes plain data and returns `dict[str, float]`. This makes it unit-testable
without a DB fixture.

**Reversibility**: Easy — pure refactor, no behavior change.

---

## Phases

### Phase 1 — Extract and unit-test the scoring function (foundation)

**Goal**: Isolate scoring logic so it can be tested and changed safely.

**Entry criteria**: Current tests pass (`538 passed`).

**Changes**:

1. `app/core/hybrid_search.py`:
   - Extract inner scoring loop into module-level function:
     ```python
     def _score_entity_articles(
         mention_rows: list,          # rows with .article_id, .entity_id, .entity_article_count
         sim_map: dict[str, float],   # entity_id → query cosine sim
         secondary_weight: float = 0.3,
     ) -> dict[str, float]:
         """Pure function: article_id → score. No DB, no I/O."""
     ```
   - `_entity_search` calls this function instead of inlining the loop.
   - No behavior change.

2. `tests/test_entity_embedding.py` (or new `tests/test_entity_scoring.py`):
   - Unit tests for `_score_entity_articles` covering:
     - Single entity, single article: score = sim / log2(2 + count)
     - Multiple entities on same article: best + 0.3 × sum(rest)
     - Hub entity (count=50) scores lower than precise entity (count=1) at same sim
     - Neighbor entity (lower sim) loses to anchor entity at same article
     - Empty input returns empty dict

**Exit criteria**:
- [ ] `_score_entity_articles` is importable and pure (no DB, no side effects)
- [ ] Unit tests pass without a DB fixture
- [ ] All existing 538 tests still pass

**Risks**: None — pure refactor.
**Estimated scope**: 1 file, ~40 lines.

---

### Phase 2 — Remove LIMIT 8, use threshold gate only

**Goal**: Candidate selection scales with library size.

**Entry criteria**: Phase 1 complete.

**Changes**:

1. `app/core/hybrid_search.py` — entity candidate SQL:
   - Remove `LIMIT 8`
   - Add `HAVING 1 - (e.embedding <=> CAST(:q AS vector)) >= :threshold` to filter
     in SQL (avoids fetching and discarding rows in Python)
   - Keep the Python-side threshold check as a safety net for exact-match rows
   - Update the gate: `top_sim < _ENTITY_SIM_THRESHOLD` is now redundant (SQL
     already enforces it) but keep for exact-match path correctness

2. Constants: rename `_ENTITY_HUB_ARTICLE_CAP` → document it as expansion-only
   (Phase 3 removes it entirely).

**Exit criteria**:
- [ ] A user with 500 entity nodes gets all qualifying anchors, not just 8
- [ ] SQL EXPLAIN shows threshold filter applied before any Python processing
- [ ] Existing entity search tests still pass

**Risks**: Query returns more rows → slightly more DB work. Acceptable: the
threshold at 0.40 means usually <20 entities qualify even in large libraries.
**Estimated scope**: 1 file, ~15 lines changed.

---

### Phase 3 — Replace binary hub gate with IDF-only dampening

**Goal**: Remove `_ENTITY_HUB_ARTICLE_CAP` as an expansion condition.

**Entry criteria**: Phase 2 complete.

**Changes**:

1. `app/core/hybrid_search.py`:
   - Remove `row.article_count <= _ENTITY_HUB_ARTICLE_CAP` from expansion filter
   - Keep `row.sim >= _ENTITY_EXPAND_THRESHOLD` — high-confidence only expands
   - Delete `_ENTITY_HUB_ARTICLE_CAP` constant
   - Update docstring

2. Tests: add a test that a hub entity (article_count=20) with high sim still
   triggers expansion, and that its score is appropriately dampened by IDF.

**Exit criteria**:
- [ ] `_ENTITY_HUB_ARTICLE_CAP` does not exist in codebase
- [ ] Entity with sim=0.90, article_count=20 still contributes to expansion
- [ ] Its per-article contribution is `0.90 / log2(22) ≈ 0.196` (dampened)
- [ ] Entity with sim=0.90, article_count=1 contributes `0.90 / log2(3) ≈ 0.568`
- [ ] All existing tests pass

**Risks**: Expansion from hub entities could add noise. Mitigated by:
- IDF dampening makes hub contributions small
- `_ENTITY_EXPAND_THRESHOLD = 0.45` still gates expansion to high-confidence anchors
- The graph currently has ~80 edges — expansion fan-out is negligible in practice

**Estimated scope**: 1 file, ~10 lines removed.

---

### Phase 4 — Compute neighbor sims against the query directly

**Goal**: Neighbor entity scores are based on actual query similarity, not a proxy.

**Entry criteria**: Phase 3 complete.

**Changes**:

1. `app/core/hybrid_search.py` — restructure the mention fetch SQL:

   Current flow: fetch anchor candidates → call `get_entity_neighbors()` separately
   → fetch mentions for both anchor + neighbor entity IDs → score with proxy sim.

   New flow: single SQL that fetches mentions for anchor entities AND does a
   lateral join to compute cosine sim for neighbor entities in one round-trip:

   ```sql
   -- Anchor mentions + their sim (already in sim_map)
   SELECT em.content_item_id AS article_id,
          em.entity_id,
          :anchor_sim AS entity_sim,  -- from sim_map, passed per-entity
          COUNT(*) OVER (PARTITION BY em.entity_id) AS entity_article_count
   FROM entity_mentions em
   WHERE em.entity_id = ANY(:anchor_ids)
     AND em.user_id = :uid

   UNION ALL

   -- Neighbor mentions: sim computed from stored embedding vs query vector
   SELECT em.content_item_id AS article_id,
          em.entity_id,
          1 - (e.embedding <=> CAST(:q AS vector)) AS entity_sim,
          COUNT(*) OVER (PARTITION BY em.entity_id) AS entity_article_count
   FROM entity_mentions em
   JOIN entities e ON e.id = em.entity_id
   WHERE em.entity_id = ANY(:neighbor_ids)
     AND em.user_id = :uid
     AND e.embedding IS NOT NULL
   ```

   The Python code passes anchor sims as individual params (via JSON array or
   repeated rows), or the anchor sim lookup remains in `sim_map` and neighbors
   get their real sim from the UNION ALL branch.

   Simpler alternative (recommended for Phase 4): keep two queries but add a
   second query just for neighbor sims:
   ```sql
   SELECT e.id, 1 - (e.embedding <=> CAST(:q AS vector)) AS sim
   FROM entities e
   WHERE e.id = ANY(:neighbor_ids) AND e.embedding IS NOT NULL
   ```
   Then merge into `sim_map` before calling `_score_entity_articles`. This
   avoids restructuring the UNION ALL and is easier to test.

2. `app/core/entity_graph.py:get_entity_neighbors` — no change to signature.

3. `_score_entity_articles` — no change needed; it already accepts any sim_map.

**Decision**: Use the simpler two-query approach (separate neighbor sim fetch).
Keeps the SQL readable. One extra round-trip, but it's a small query by UUID
array — negligible overhead.

**Exit criteria**:
- [ ] Neighbor entities' scores in `sim_map` are real cosine similarities, not proxy
- [ ] A neighbor with sim=0.15 to the query scores lower than one with sim=0.60
- [ ] Test: two neighbors at different sims → correct relative scoring
- [ ] All existing tests pass

**Risks**: Neighbor sims may be low (they weren't directly matched) → neighbor
articles rank below anchors. This is correct behavior, not a bug.
**Estimated scope**: 1 file, ~30 lines changed + new test.

---

### Phase 5 — Eval and tune

**Goal**: Verify the redesign improves or doesn't regress on the 45-query eval dataset.

**Entry criteria**: Phases 1–4 complete. Production DB accessible.

**Changes**:

1. Run `make eval` against production DB before and after the changes.
2. Compare R@10 and MRR on the full 45-query set and the 5 regression queries
   from `hub-cap-investigation.md`.
3. Adjust `_ENTITY_SIM_THRESHOLD` and `_ENTITY_EXPAND_THRESHOLD` if warranted
   by eval results. Document in `docs/design/systems/entity-retrieval-eval.md`.

**Exit criteria**:
- [ ] R@10 on full 45-query set ≥ current baseline (S3 numbers in eval-retrieval-dataset.md)
- [ ] No regression vs current baseline on any tier
- [ ] Constants are justified by eval numbers, not intuition

**Risks**: Removing hub cap may introduce new regressions on hub-entity queries.
Detection: the eval suite covers `anthropic_products_direct` and
`anthropic_claude_products` which are the hub-entity failure cases.

---

## Risks

**Risk**: Removing LIMIT 8 causes slow queries for users with many entities.
**Likelihood**: Low — threshold 0.40 limits qualifying rows.
**Mitigation**: Add EXPLAIN analysis in Phase 2. If query time > 50ms, add a
`LIMIT` after threshold filtering (e.g. LIMIT 50) as a safety valve.

**Risk**: Real neighbor sims are all low, making expansion useless.
**Likelihood**: Medium — the graph is sparse and neighbors may not be
semantically related to the query.
**Mitigation**: Expected and acceptable. Expansion should only help when
neighbors are genuinely related. Low-sim neighbors get low weight via IDF.

**Risk**: Eval shows regression after removing hub cap.
**Likelihood**: Low — investigation proved hub cap is inert for current failures.
**Mitigation**: Phase 5 catches this. Revert Phase 3 if eval regresses.

---

## Verification

### Automated
- `_score_entity_articles` unit tests (Phase 1) — no DB needed
- Integration tests in `test_entity_embedding.py` — E2E roundtrip still passes
- `make test` full suite — 538 tests continue to pass after each phase

### Eval (Phase 5)
- `make eval` — R@10 and MRR on 45-query production corpus
- Per-regression-query analysis for the 5 failing queries

### What does NOT need manual testing
- Frontend — entity lane output format is unchanged
- API — `hybrid_search()` interface is unchanged

---

## Open questions

None — all decisions are made above. Ready to implement Phase 1.
