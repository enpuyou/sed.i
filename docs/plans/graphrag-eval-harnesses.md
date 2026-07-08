---
type: plan
status: in-progress
date: 2026-07-01
---

# Plan: Eval Harnesses for GraphRAG & Multi-Agent Features

## Goal

Establish a complete, layered eval harness covering retrieval quality, entity
extraction, synthesis faithfulness, and agent loop behavior. The harness must
integrate with the existing Braintrust wiring, expose hard cases that prove
current retrieval breaks on multi-hop queries, and run as both a pytest
regression gate and a standalone Braintrust experiment script.

## Non-goals

- Implementing any of the GraphRAG features themselves (Features A–I are separate sprints).
- Changing the existing search evals (`test_search_evals.py`) — those stay as-is.
- Building a custom eval framework.
- PostHog event schema changes — those are wired when each feature ships.

## Prior art

- `tests/evals/test_search_evals.py` — pattern to follow: module-scoped DB
  fixture, seeded dataset, printed report, assertion thresholds, `baselines.json`
  for regression gates.
- `tests/evals/test_tagging_evals.py` — LLM-dependent evals with skip guard.
- `app/core/llm_client.py` — Braintrust wiring: `braintrust.wrap_openai(client)` +
  `braintrust.init_logger(project="sedi")`. Traces every OpenAI call. Does not
  yet log structured experiment results — that requires `braintrust.Experiment`.

## Current Braintrust integration state

| What's wired | What's missing |
| --- | --- |
| `braintrust.wrap_openai` traces every LLM call | No `Experiment` objects — no scored eval runs |
| `braintrust.init_logger(project="sedi")` | No eval datasets registered |
| `BRAINTRUST_API_KEY` set in `.env` | No per-run metric history |

Tracing logs what happened in a call. Experiments track whether quality improved
or regressed across runs. We need both; currently only tracing is wired.

## Current eval simulation fidelity

The existing eval in `test_search_evals.py` runs against a real Postgres test
DB with the tsvector trigger installed. What it simulates accurately and what
it doesn't:

| Layer | In production | In eval today |
| --- | --- | --- |
| `tsvector` / keyword | Real trigger on insert | Real trigger on insert ✓ |
| Item-level embedding | `text-embedding-3-small` via Celery | Same model, called inline ✓ |
| **Chunk embeddings** | `generate_chunk_embeddings` task, contextual prefix | **Not generated — `content_chunks` is empty** ✗ |
| **Contextual prefix** | Prepended before embedding each chunk | Missing entirely ✗ |
| Redis embedding cache | Used in production | Bypassed ✗ |

The critical gap: `hybrid_search` uses chunk embeddings when available, falling
back to item-level only when `content_chunks` is empty. Every eval query today
runs against the fallback path. Multi-hop retrieval depends on chunks — a chunk
in article B can mention a bridging concept from article A's topic. Without
chunks, that signal is invisible.

The eval articles also have 2–3 sentence `full_text` fields, which makes
item-level embeddings low quality even for simple queries.

**This is why the eval dataset must be built from actual retrieval failures**,
not hypothetical cases. See Phase 0.

---

## What to evaluate and why

### Eval A — Retrieval quality (extend existing + add NDCG@k + chunk fidelity)

**What**: Does hybrid search surface the right articles? Currently measures
hit rate @10 and MRR. Missing: NDCG@5, multi-hop cases, chunk-level fidelity.

**Gap to close**: Add NDCG@5. Add multi-hop query cases identified by the
hard-case discovery script. Add a fixture path that generates real chunk
embeddings so the eval exercises the actual production code path.

### Eval B — Entity extraction F1

**What**: Does `extract_entities` (Feature A) produce accurate entities and
relations? F1 ≥ 0.70 on entities is the gate before Feature A ships.

**Data source**: Run the extractor on 10 of your real articles after Feature A
is implemented, review its output, correct mistakes. The corrected output is
the gold set. Do not invent labels from memory.

### Eval C — Synthesis faithfulness

**What**: Does `synthesize_topic` produce claims grounded in its retrieved sources?

**Scorer**: LLM-as-judge using `llm_client.chat()` — not the RAGAS library, which
pulls LangChain and breaks Braintrust tracing. The judge lists each claim, says
YES/NO per claim against the source passages, then scores = supported / total.

### Eval D — Agent loop vs. one-shot recall

**What**: Does the iterative search loop in `synthesize_topic(depth="deep")` find
more relevant articles than a single `hybrid_search` call on multi-hop queries?
If `recall@10` delta ≤ 0 across cases, the loop is removed.

### Eval E — Multi-hop accuracy

**What**: Does `synthesize_topic(depth="deep")` answer questions requiring
information from two or more articles? Scored by key-point coverage via LLM-as-judge.

### Eval F — Online behavioral metrics (PostHog spec)

Documented below in Phase 5. Cannot run offline — requires real user behavior.

---

## Two ways evals run

### pytest regression gate (`pytest tests/evals/`)

- Runs on every manual eval invocation before a feature PR merges.
- Asserts thresholds from `baselines.json`. Fails if a metric regresses.
- Does not log to Braintrust Experiment (only passes through traces).
- Does not run in CI — requires OpenAI key and test DB.

### Braintrust experiment script (`python scripts/run_evals.py <eval-name>`)

- Logs `(input, output, expected, scores)` per case to Braintrust.
- Run manually when you want to compare two states (before/after a feature).
- Results visible in Braintrust UI as a named experiment with score history.

The separation matters: pytest catches regressions; Braintrust shows improvement.

---

## Architecture decisions

### Decision: Braintrust experiment logging location

Running `braintrust.Experiment` inside pytest is unreliable — pytest tears down
fixtures before `experiment.close()` is called, and experiments are meant to
run as a batch rather than interleaved with assertions.

Choice: Standalone `scripts/run_evals.py`. Scoring functions live in
`tests/evals/scoring.py` (shared). Pytest imports and calls the scorers for
assertions; the script imports the same scorers and logs to Braintrust.

```text
tests/evals/scoring.py           ← shared: ndcg_at_k, faithfulness_score, etc.
tests/evals/test_search_evals.py ← uses scorers, asserts thresholds
scripts/run_evals.py             ← uses scorers, logs to braintrust.Experiment
```

### Decision: Faithfulness scorer

`llm_client.chat(task=TASK_FAITHFULNESS_JUDGE)` — not the RAGAS library. Keeps
all LLM calls inside Braintrust tracing. Judge prompt uses chain-of-thought:
list each claim, YES/NO against sources, score = supported/total. Two few-shot
examples in the prompt for consistency.

### Decision: Chunk fidelity in eval fixture

For evals that need to exercise the chunk code path, generate real chunks at
fixture setup time using the production `generate_chunk_embeddings` logic called
inline (not via Celery). This is a new module-scoped fixture that seeds articles
and then generates chunks for them. Adds ~10s to the eval run but makes the
retrieval eval test what production actually does.

### Decision: Hard-case dataset

Cases are discovered by `scripts/find_hard_cases.py` against the real retrieval
stack, not hand-invented. The script takes candidate multi-hop queries (generated
by an LLM from your article titles), runs them through `hybrid_search`, and
outputs cases where at least one expected article is missing from top-10. Those
cases are the eval dataset. This ensures the cases represent actual failures,
not hypothetical ones.

---

## User actions required

1. **Braintrust API key**: already in `.env` — confirmed.

2. **Run `find_hard_cases.py` after Phase 0** to generate the eval dataset. Requires
   the test DB to be running and `OPENAI_API_KEY` set. Outputs a Python dict you
   paste into `tests/evals/search_eval_dataset.py`.

3. **Write entity gold labels after Feature A ships**: run `extract_entities` on
   10 of your most-read articles, review output, correct mistakes, save as gold.
   Estimated time: 30–60 min. A template is in Phase 4.

4. **Write 8 synthesis eval cases** after Feature D ships: topics from your actual
   library with expected key points. Template in Phase 3.

5. **Answer the open CI question** (see Open Questions below).

---

## Phases

### Phase 0 — Hard-case discovery (prerequisite for Phase 1 dataset)

**Goal**: Find multi-hop queries where current retrieval fails. These become the
eval dataset.

**Entry criteria**: Test DB running. `OPENAI_API_KEY` set.

Changes:

1. `scripts/find_hard_cases.py` (new file):

```python
"""
Discovers hard eval cases by running candidate queries against the current
retrieval stack and identifying failures.

Usage:
    poetry run python scripts/find_hard_cases.py

Output: prints Python dict ready to paste into search_eval_dataset.py
"""

# Step 1: Load article titles/descriptions from the test DB (or from
#         EVAL_ARTICLES in search_eval_dataset.py if running offline)
# Step 2: Ask LLM to generate 20 multi-hop candidate queries:
#   "Given these articles, write questions that require reading TWO of them
#    to answer fully. The answer should not be findable in either article alone."
# Step 3: For each candidate query, run hybrid_search() against the seeded DB
# Step 4: Score recall: which expected articles appear in top-10?
# Step 5: Output cases where recall < 1.0 — these are the hard cases
```

This script produces the multi-hop additions to `EVAL_QUERIES` in `search_eval_dataset.py`.

Exit criteria:
- [ ] Script runs to completion against test DB
- [ ] Outputs ≥ 8 query cases where current retrieval scores recall < 1.0
- [ ] Cases pasted into `search_eval_dataset.py`

Estimated scope: 1 new script, ~100 lines.

---

### Phase 1 — Retrieval eval: NDCG@k + chunk fidelity + Braintrust script

**Goal**: Extend the existing retrieval eval to measure NDCG@5, exercise the
chunk code path, add the hard cases from Phase 0, and wire `scripts/run_evals.py`
for Braintrust experiment logging.

**Entry criteria**: Phase 0 complete. Hard cases in `search_eval_dataset.py`.

Changes:

1. `tests/evals/scoring.py` (new file) — shared scoring functions:
   ```python
   def ndcg_at_k(ranked: list[str], relevant: set[str], k: int = 5) -> float: ...
   def recall_at_k(ranked: list[str], relevant: set[str], k: int = 10) -> float: ...
   def mrr(ranked: list[str], relevant: set[str]) -> float: ...
   # faithfulness_score and key_point_coverage added in Phase 2
   ```

2. `tests/evals/conftest.py` — add `eval_articles_with_chunks` fixture:
   ```python
   @pytest.fixture(scope="module")
   def eval_articles_with_chunks(eval_articles_with_embeddings, db_module):
       """Generate real content_chunks for eval articles using production logic."""
       from app.tasks.embedding import _chunk_and_embed_article
       for key, article in eval_articles_with_embeddings.items():
           _chunk_and_embed_article(article, db_module)
       db_module.commit()
       return eval_articles_with_embeddings
   ```

3. `tests/evals/test_search_evals.py` — add NDCG@5 to `TestHybridSearchQuality`,
   add a new `TestMultiHopRetrieval` class using the hard cases from Phase 0 and
   the `eval_articles_with_chunks` fixture.

4. `tests/evals/baselines.json` — add `"retrieval_ndcg_at_5": null` and
   `"multi_hop_recall_at_10": null`.

5. `scripts/run_evals.py` (new file) — standalone Braintrust experiment runner:
   ```python
   # Usage: poetry run python scripts/run_evals.py retrieval
   # Logs a named experiment to Braintrust with per-case scores.
   # Imports scoring functions from tests/evals/scoring.py.
   # Does not run pytest — just scores and logs.
   ```

6. `Makefile` — add:
   ```makefile
   eval:
       cd content-queue-backend && \
       PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec \
       poetry run pytest tests/evals/ -v -s

   eval-bt:  ## Log experiment to Braintrust (requires OPENAI_API_KEY + BRAINTRUST_API_KEY)
       cd content-queue-backend && \
       PYENV_VERSION=3.11.12 /usr/local/opt/pyenv/bin/pyenv exec \
       poetry run python scripts/run_evals.py $(EVAL)
   # Usage: make eval-bt EVAL=retrieval
   ```

Exit criteria:
- [ ] `make eval` passes with NDCG@5 and multi-hop recall in the report
- [ ] `make eval-bt EVAL=retrieval` logs a named experiment in Braintrust UI
- [ ] `baselines.json` updated with measured values
- [ ] `TestMultiHopRetrieval` fails on at least some cases (proving current system breaks)

Estimated scope: 3 modified files, 2 new files, ~200 lines total.

---

### Phase 2 — Faithfulness judge infrastructure

**Goal**: Build the LLM-as-judge scorer for synthesis evals.

**Entry criteria**: Phase 1 complete.

Changes:

1. `app/core/llm_client.py` — add `TASK_FAITHFULNESS_JUDGE = "faithfulness_judge"`.

2. `app/core/config.py` — add:
   ```python
   LLM_MODEL_FAITHFULNESS_JUDGE_OPENAI: str = "gpt-4o-mini"
   LLM_MODEL_FAITHFULNESS_JUDGE_BEDROCK: str = "amazon.nova-micro-v1:0"
   ```

3. `tests/evals/scoring.py` — add:
   ```python
   def faithfulness_score(answer: str, source_passages: list[str]) -> float:
       """
       Chain-of-thought judge: for each claim in `answer`, does a source
       passage support it? Score = supported_claims / total_claims.
       Uses llm_client.chat(task=TASK_FAITHFULNESS_JUDGE).
       """

   def key_point_coverage(answer: str, expected_key_points: list[str]) -> float:
       """
       Judge: fraction of expected_key_points present (even paraphrased) in answer.
       """
   ```

4. `tests/evals/test_scoring.py` (new file) — unit tests for the scorers using
   hardcoded strings (no LLM call needed for ndcg/recall/mrr; LLM-dependent
   scorers get a skip guard like the tagging evals).

Exit criteria:
- [ ] `faithfulness_score(perfect_answer, sources)` returns 1.0 on a toy case
- [ ] `faithfulness_score(hallucinated_answer, sources)` returns < 0.5
- [ ] Unit tests pass with `make eval`

Estimated scope: 3 modified files, 1 new file, ~100 lines.

---

### Phase 3 — Synthesis eval harness (Evals C + D + E)

**Goal**: Eval for `synthesize_topic` covering faithfulness, loop effectiveness,
and multi-hop accuracy.

**Entry criteria**: Phase 2 complete. Feature D (`synthesize_topic`) implemented.
8 synthesis eval cases written by you (template below).

Dataset template (you fill in 8 cases):

```python
SYNTHESIS_EVAL_CASES = [
    {
        "topic": "the relationship between dopamine and attention",
        "depth": "deep",
        "seed_article_keys": ["attention_article", "deep_work_review"],
        "expected_key_points": [
            "dopamine drives variable reward behavior",
            "notification systems degrade sustained concentration",
        ],
        "multi_hop": True,  # requires both seed articles to answer fully
    },
    # 7 more — use topics you've actually read about in your library
]
```

Changes:

1. `tests/evals/synthesis_eval_dataset.py` (new file) — 8 cases. Written by you.

2. `tests/evals/test_synthesis_evals.py` (new file):
   - `TestSynthesisFaithfulness`: quick + deep synthesis, scored by faithfulness judge.
   - `TestLoopEffectiveness`: for each case, compare recall@10 of the loop's result
     set vs one-shot `hybrid_search`. Log `delta = loop_recall - oneshot_recall`.
   - `TestMultiHopAccuracy`: key-point coverage on `multi_hop=True` cases.

3. `scripts/run_evals.py` — add `synthesis` eval name.

4. `tests/evals/baselines.json` — add:
   ```json
   "synthesis_faithfulness_quick": null,
   "synthesis_faithfulness_deep": null,
   "synthesis_coverage_multi_hop": null,
   "loop_recall_delta": null
   ```

Exit criteria:
- [ ] All three test classes run and print reports
- [ ] `make eval-bt EVAL=synthesis` logs experiment in Braintrust
- [ ] `loop_recall_delta` baseline measured — if consistently ≤ 0, loop is removed
- [ ] `baselines.json` updated

Estimated scope: 2 new files, 1 modified (~200 lines), 8 cases written by you.

---

### Phase 4 — Entity extraction eval harness (Eval B)

**Goal**: F1 gate for `analyze_article` entity output. Blocks Feature A from
production until entity F1 ≥ 0.70.

**Entry criteria**: Feature A implemented (tagging + entity merge complete via
`analyze_article` task). Gold labels written by you. Relation types match the
revised 6-type taxonomy: DEVELOPED | INTRODUCES | BUILDS_ON | USES | CONTRADICTS
| ENABLES. See `docs/design/systems/tagging-entity-architecture-review.md`.

Gold label process:

1. Run `extract_entities` on your 10 most-read articles.
2. Print the extractor's output.
3. Correct entity names, types, and relations by hand.
4. Save as the gold set. The gold should reflect what the articles actually contain,
   not what you think they should contain.

Gold label template:

```python
ENTITY_EVAL_CASES = [
    {
        "article_key": "attention_article",  # reuses seeded article
        "gold_entities": [
            {"name": "dopamine", "type": "CONCEPT"},
            {"name": "variable reward schedules", "type": "CONCEPT"},
            {"name": "Nir Eyal", "type": "PERSON"},
        ],
        "gold_relations": [
            {"source": "dopamine", "target": "variable reward schedules",
             "relation_type": "ENABLES"},
        ],
    },
    # 9 more
]
```

Changes:

1. `tests/evals/entity_eval_dataset.py` (new file) — 10 cases. Written by you.

2. `tests/evals/test_entity_evals.py` (new file):
   - `TestEntityF1`: entity precision, recall, F1 (case-insensitive name match).
   - `TestRelationF1`: relation-level F1 (source + target + type must all match).
     Gate: entity F1 ≥ 0.70, relation F1 ≥ 0.50.
   - Braintrust experiment: "entity-extraction-f1" via `run_evals.py`.

3. `tests/evals/baselines.json` — add `"entity_f1": null, "relation_f1": null`.

Exit criteria:
- [ ] Entity F1 measured and in `baselines.json`
- [ ] CI gate: test asserts entity F1 ≥ 0.70
- [ ] `make eval-bt EVAL=entity` logs per-article scores in Braintrust

Estimated scope: 2 new files, ~120 lines. Gold labels ~60 min.

---

### Phase 5 — PostHog event schema spec

**Goal**: Define exact event names and properties for all new features so
instrumentation is consistent when each feature ships.

**Entry criteria**: None.

Changes:

1. `docs/design/systems/eval-posthog-schema.md` (new file):

```markdown
# PostHog Event Schema — GraphRAG Features

## Feature C: entity-augmented search
posthog.capture(user_id, "search_completed", {
    "mode": "hybrid" | "entity_augmented",
    "result_count": int,
    "entity_boost_applied": bool,
})
posthog.capture(user_id, "search_result_clicked", {
    "rank": int,
    "entity_boost": bool,
    "query_id": str,
})

## Feature B: PPR connections
posthog.capture(user_id, "connection_shown", {"type": "semantic" | "conceptual"})
posthog.capture(user_id, "connection_clicked", {
    "type": "semantic" | "conceptual",
    "led_to_read": bool,
})

## Feature D: synthesize_topic
posthog.capture(user_id, "synthesis_completed", {
    "depth": "quick" | "deep",
    "source_count": int,
    "loop_iterations": int,
    "latency_ms": int,
})

## Feature F: daily brief
posthog.capture(user_id, "article_opened", {
    "source": "daily_brief" | "search" | "recommendation" | "direct",
})

## Feature H: tag agent
posthog.capture(user_id, "tag_kept_on_read", {"tag": str})
posthog.capture(user_id, "tag_deleted", {"tag": str, "article_age_days": int})

## Key derived metrics

- online MRR: avg rank of search_result_clicked.rank
- PPR vs cosine CTR: connection_clicked.led_to_read grouped by type
- Loop necessity: synthesis_completed.loop_iterations histogram
- Brief CTR: article_opened where source == "daily_brief"
- Tag retention: tag_kept_on_read / (tag_kept_on_read + tag_deleted)
```

Exit criteria:
- [ ] Doc written and reviewed
- [ ] No code changes in this phase

---

## Dependency order

```text
Phase 0 (hard-case discovery)
  └── Phase 1 (retrieval eval + Braintrust script)    ← can ship independently
        └── Phase 2 (faithfulness judge)              ← can ship independently
              └── Phase 3 (synthesis eval)            ← needs Feature D
              └── Phase 4 (entity eval)               ← needs Feature A + gold labels
Phase 5 (PostHog spec)                                ← no dependencies, any time
```

Phases 0–2 have no feature dependencies and can be built now. Phases 3 and 4
are blocked on the GraphRAG features they evaluate.

---

## Risk register

**Risk: chunk fixture adds unacceptable latency**
Generating real chunks at fixture setup requires N × LLM embed calls. At 7
eval articles × ~3 chunks each = ~21 embed calls ≈ 5–10 seconds.
*Mitigation*: Module-scoped fixture (runs once per eval session, not per test).
Mark with `@pytest.mark.slow` and skip in fast mode.

**Risk: multi-hop cases from `find_hard_cases.py` are trivially easy after features ship**
Cases proven hard by current retrieval may become easy once entity-augmented
search ships. The eval set should be updated after each sprint.
*Mitigation*: Re-run `find_hard_cases.py` before each feature PR to find cases
that still fail. The dataset is not static.

**Risk: faithfulness judge scores are noisy**
LLM judges have variance. Running the same answer twice may give different scores.
*Mitigation*: Temperature=0, chain-of-thought with explicit per-claim YES/NO,
two few-shot examples in the prompt. Log the judge's reasoning to Braintrust
so anomalous scores are auditable.

**Risk: gold labels for entity eval are incorrect**
Hand-labeling is error-prone. An incorrect gold set gives meaningless F1.
*Mitigation*: Label by reviewing the extractor's output (correct it), not by
inventing from memory. Have the extractor do a first pass; you do the correction.

---

## Open questions

1. **Should evals run in CI?** Recommendation: no — they require OpenAI key
   and are slow. Run manually before each feature PR merges with `make eval`.
   Update `baselines.json` in the same commit as the feature.

2. **How often to run Braintrust experiments?** Recommendation: once per sprint
   boundary (before and after a feature ships). The delta between two experiment
   runs is the evidence that the feature improved quality.
