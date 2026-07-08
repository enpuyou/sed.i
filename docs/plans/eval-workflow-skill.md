# Plan: `/eval` skill — end-to-end eval workflow
Date: 2026-07-03
Status: Draft

## Goal

A skill that runs the full scientific loop for evaluating any system change —
prompt, retrieval method, feature, or architectural variant. Not just a test
runner. The loop goes from hypothesis through promotion.

## Non-goals

- Not a replacement for pytest unit tests.
- Not real-time production monitoring (that's Braintrust online eval).
- Not a general benchmark — evals are always grounded in the real corpus and
  real user queries.

---

## Research grounding

**Eval-Driven Development (EDD)** is the established practitioner name for this
workflow. DeepEval and Hamel Husain/Shreya Shankar's masterclass define it as:
curate ~100 test cases (goldens), define 3–5 metrics that correlate to real
performance, iterate until all pass. The closest thing to "hypothesis-first" in
practitioner guidance is Hamel's advice to write 5–10 success scenarios and
5–10 failure scenarios before touching any prompt.

**Pilot → scale** is widely practiced but not formally named. The consensus:
manually review 20–50 outputs first whenever making significant changes; build
eval infrastructure only after you understand what "good" looks like.

**Hard fail vs PR comment** is the CI split that matters: deterministic metrics
(retrieval R@K, classification accuracy) get hard fail on threshold breach.
LLM-as-judge scores get a PR comment because false positives on probabilistic
scores would break CI constantly.

**Braintrust Experiments** differ from local JSON in one key way: per-test-case
comparison across runs, with a filterable "regressed cases" view and a GitHub
Actions PR comment showing the score diff table. You designate one experiment as
the baseline; every subsequent run auto-diffs. Worth using for Phase 7 (full
scale, multi-variant) but not required for pilot.

**G-Eval** (Liu et al., 2023) is the most production-validated LLM-as-judge
pattern — 8M+ runs in March 2025 alone. Mechanism: expand the task description
into chain-of-thought evaluation steps, use those steps as the scoring rubric.
3-point Likert with explicit CoT criteria per level is simpler and well-calibrated.

**RAGAS synthetic test generation**: generates query/answer pairs from actual
documents via a knowledge graph, then human-review filters them. Right approach
for building extraction eval datasets rather than hand-authoring every case.

---

## What already exists in `tests/evals/`

| File | What it measures | Data source | Type |
|------|-----------------|-------------|------|
| `test_search_evals.py` | Routing accuracy, hit rate | 7 seeded synthetic articles | pytest, test DB |
| `test_retrieval_evals.py` | R@10, MRR, NDCG on 32 queries | Production DB | pytest, prod DB |
| `test_tagging_evals.py` | Tag specificity, coverage, forbidden rate | Real LLM calls, 10 articles | pytest, real LLM |
| `test_mcp_evals.py` | MCP tool response shape contracts | Seeded test DB | pytest, test DB |
| `scoring.py` | R@K, MRR, NDCG, faithfulness, key-point coverage | — | Pure functions |
| `baselines.json` | Three flat thresholds (accuracy, hit rate, MRR) | — | Single file for all evals |
| `scripts/run_evals.py` | Braintrust runner for retrieval | Production DB | Standalone script |

## What's missing from `tests/evals/` vs our proposed standard

| Gap | Current state | What's needed |
|-----|--------------|---------------|
| Multi-variant comparison | Pass/fail against one threshold — no A vs B vs C | Variant-aware runner that produces a comparison table |
| Pilot size gate | No concept — all 32 queries always run | Explicit pilot dataset (8–10 cases) that runs in <30s |
| Hypothesis recording | Not recorded anywhere | `hypothesis.md` per eval, written before running |
| G-Eval rubric scorer | `faithfulness_score` is binary; tagging uses heuristics | `rubric_score()` with CoT criteria and per-dimension scores |
| Per-eval baselines | One flat `baselines.json` for everything combined | Per-eval `baselines.json` with metric history and justification |
| Extraction eval | Quality tracked in markdown, not runnable | `evals/extraction/` wired to `article_analysis.py` |
| Decision gate | Tests pass/fail — no "ship this variant?" output | Recommendation phase: ship / don't ship / investigate |
| Braintrust per-variant | `run_evals.py` logs one experiment for the whole run | One Braintrust experiment per variant so the comparison UI works |
| CI split | No differentiation | Hard fail for deterministic metrics; PR comment for LLM-judge |

---

## The full eval loop

Every eval the skill runs follows this sequence. Phases cannot be skipped.

```
1. Hypothesis       What do I believe, and why?
2. Criteria         What does "better" mean, and how do I measure it?
3. Data             What ground truth do I need, and where does it come from?
4. Harness check    Does the eval itself work before I trust its output?
5. Pilot            Small data, fast feedback — is the setup correct?
6. Interpret pilot  Per-case breakdown — patterns before scaling
7. Scale            Full dataset, all variants
8. Interpret full   Which cases improved? Which regressed? Why?
9. Decision         Ship / don't ship / investigate further
10. Promote         Update baseline, document decision, close the loop
```

---

## Phase 1 · Hypothesis

Before any code runs, the skill asks:

> What are you evaluating, and what do you expect to find?

The user must complete:

```
Evaluating:    [system / feature / prompt / variant name]
Variants:      [A: current baseline] [B: ...] [C: ...] (1–4 variants max)
Hypothesis:    "I believe [variant B] will outperform [variant A] on [metric]
                because [reason]. I expect to see [specific behavior change]."
Falsifiable?   [what result would prove the hypothesis wrong?]
Success cases: 3–5 examples that should work if the hypothesis is true
Failure cases: 3–5 examples that should still work (guard against regression)
```

The skill writes this to `evals/<name>/hypothesis.md`. If the user can't write
a falsifiable hypothesis, the eval is premature — the skill says so and stops.

Grounded in Hamel Husain's practitioner guidance: write success and failure
scenarios before touching any code or prompt.

---

## Phase 2 · Criteria

The skill proposes metrics based on eval type, user confirms or adjusts.

**Retrieval evals:**

- Primary: Recall@10 (did we find the right articles?)
- Secondary: MRR (did the right article rank first?)
- Guard rail: no regression on queries currently at 1.0

**Extraction / classification evals (determinate outputs):**

- Precision, Recall, F1 against ground truth labels
- Guard rail: no increase in noise entity / forbidden tag rate

**Open-ended evals (descriptions, summaries, synthesis):**

- G-Eval rubric score (see Phase 2b)
- Guard rail: faithfulness score stays above floor

**CI failure mode per type:**

- Deterministic metrics → hard fail on threshold breach
- LLM-as-judge scores → PR comment (probabilistic, false positives too costly)

### Phase 2b · G-Eval rubric design (for open-ended tasks)

When ground truth doesn't exist, use G-Eval pattern:

1. Write the task description (e.g., "extract concept entities from an article")
2. Expand into 3–5 chain-of-thought evaluation steps (what would a good judge check?)
3. For each step, write explicit anchors: what does a 1 look like? a 3? a 5?
4. Assign weights summing to 1.0
5. Set pass threshold (e.g., weighted score ≥ 0.70)
6. **Validate the rubric** on 3 known-good and 3 known-bad examples before using it in the eval

Example rubric for extraction quality (stored in `evals/extraction/rubric.py`):

```python
EXTRACTION_RUBRIC = [
    {
        "dimension": "concept_precision",
        "weight": 0.40,
        "cot_steps": [
            "List each extracted entity.",
            "For each entity: is it a named idea central to the article's argument, "
            "or is it a generic label / incidental detail?",
            "Count signal entities vs noise entities.",
        ],
        "anchors": {
            1: "All or most entities are generic (AI, technology) or incidental",
            3: "Mix of signal and noise; some core concepts present",
            5: "All entities are named ideas that are central to the argument",
        }
    },
    {
        "dimension": "relation_quality",
        "weight": 0.40,
        "cot_steps": [
            "List each relation.",
            "For each: does the predicate specify HOW the entities connect "
            "(cause, contrast, mechanism, instantiation)?",
            "Is the relation grounded in the article text, or inferred?",
        ],
        "anchors": {
            1: "No relations, or predicates are vague co-occurrence statements",
            3: "Relations exist with some specificity but grounding is weak",
            5: "Predicates are specific and causal/structural, grounded in article text",
        }
    },
    {
        "dimension": "description_usefulness",
        "weight": 0.20,
        "cot_steps": [
            "For each entity description: does it explain WHY this entity "
            "matters to THIS article's argument, or does it just define the term?",
        ],
        "anchors": {
            1: "Descriptions are empty or restate the entity name",
            3: "Descriptions define the entity but not its role in this article",
            5: "Every description explains why this entity matters to the argument",
        }
    },
]
```

Sample N=3 times and average to reduce LLM judge variance.

---

## Phase 3 · Data

Three sources in order of preference:

**A. Existing labeled data** — check `tests/evals/` and `evals/` first.

**B. Real corpus + hand labels** — for retrieval: query → relevant article IDs,
labeled against the real library. For extraction: article → ideal entity output,
reviewed by user. Minimum: 10 labeled examples for pilot, 50+ for full eval.

**C. RAGAS-style synthetic generation + human review** — for new systems:
generate candidate cases from actual documents, human-filter for correctness.
Do not use unreviewed synthetic data as ground truth.

Data hygiene: labels created independently of the system being evaluated;
every label has a written rationale; dataset versioned in the repo.

**Size targets:**

- Pilot: 8–12 examples (under 30 seconds to run, catches harness bugs)
- Full eval: 30–100 examples (enough to trust a 5pp delta)

---

## Phase 4 · Harness check

Validate the eval before trusting its output:

1. **Baseline sanity**: run the baseline variant on the pilot dataset.
   Expected: scores match stored baseline ± 0.03.
   If deviation > 0.05: stop — harness is broken or baseline is stale.

2. **Scorer sanity**: run scorer on one known-good example (expect ≥ 0.85)
   and one known-bad example (expect ≤ 0.40). If either fails: scorer has a bug.

3. **Data sanity**: spot-check 3 random examples. Are labels reasonable?

Skill will not proceed to Phase 5 if harness check fails.

---

## Phase 5 · Pilot

Run all variants on the pilot dataset (8–12 examples):

```
Variant A (baseline):          recall@10=0.75   mrr=0.80   [10 queries]
Variant B (threshold 0.40):    recall@10=0.80   mrr=0.85
Variant C (score passthrough): recall@10=0.85   mrr=0.90
```

Per-case breakdown (required):

```
query                A      B      C      B-A     C-A
context_engineering  1.00   1.00   1.00   —       —
enshittification     0.00   0.50   0.50   +0.50   +0.50
anthropic_products   0.67   0.33   0.67   -0.34   —
```

Pilot job: confirm harness is measuring what we think; surface implementation
bugs (a variant scoring 0.0 everywhere means a bug, not a finding); identify
which query categories are moving.

If pilot results contradict the hypothesis across all cases → stop and
investigate before scaling. Most common cause: implementation bug.

---

## Phase 6 · Interpret pilot

Required before scaling. Skill produces:

- Which categories improved / regressed?
- Is improvement concentrated in a specific type (vocabulary-distant queries)?
- Does regression have a clear cause (hub entity fan-out)?
- Hypothesis check: supported, contradicted, or too small to tell?

Go / no-go decision for Phase 7.

---

## Phase 7 · Scale

Run all variants on the full dataset. For LLM-call-heavy variants, estimate cost
before running:

```
Estimated: 50 examples × 3 variants × $0.002/call = $0.30. Proceed? [y/n]
```

Log each variant as a separate Braintrust experiment so the comparison UI works.
Results also written locally to `evals/<name>/results/<variant>_<timestamp>.json`.

---

## Phase 8 · Interpret full results

**Aggregate table:**

```
variant                  recall@10  mrr    ndcg@10  regressions  improvements
A (baseline)             0.721      0.773  0.781    —            —
B (threshold 0.40)       0.748      0.897  0.812    4            5
C (score passthrough)    0.764      0.859  0.831    2            5
```

**Residual failure taxonomy** — for every query still below 1.0, classify:
vocabulary gap / entity gap / corpus gap / algorithm gap.

**Statistical note:** with 32 queries, a 3pp delta = ~1 query flipping. Treat
deltas < 5pp on < 50 queries as directional, not conclusive. Always look at the
per-query breakdown.

---

## Phase 9 · Decision

Skill outputs a recommendation — one of three:

**Ship variant X** when:

- Primary metric improved ≥ 2pp
- No guard rail regressions (queries at 1.0 stayed at 1.0)
- Remaining regressions have documented root causes
- Hypothesis confirmed

**Don't ship** when:

- No primary metric improvement, or
- Guard rail regressions with no clear fix, or
- Improvement on one metric, regression on another with no accepted tradeoff

**Investigate further** when:

- Delta < 2pp on < 30 queries (directional but inconclusive)
- One variant shows unexpected behavior worth understanding first
- A root cause was identified suggesting a better variant to try

User makes the final call. Skill provides evidence and recommendation.

---

## Phase 10 · Promote

If shipping:

1. Update `evals/<name>/baselines.json` with new scores + date + justification
2. Update `ARCHITECTURE.md` if system config changed
3. Update relevant doc in `docs/design/systems/`
4. Commit: `perf(search): lower entity threshold to 0.40, +4.3pp recall@10`

If not shipping: document finding in `evals/<name>/results/` with what was
learned. Negative results are still results.

---

## Artifact layout

Root location: `evals/` at the project root (shared across backend and any
future frontend evals).

```
evals/
  <name>/
    hypothesis.md          ← Phase 1 output (committed)
    dataset/
      pilot.py             ← 8–12 labeled examples (committed)
      full.py              ← 30–100 labeled examples (committed)
    rubric.py              ← G-Eval rubric, if applicable (committed)
    scorer.py              ← scoring functions for this eval (committed)
    runner.py              ← entry point: run all variants, write results (committed)
    baselines.json         ← {metric: score, _updated: date, _reason: str} (committed)
    results/               ← gitignored
      <variant>_<ts>.json
      latest.json
  check_regressions.py     ← CI script: reads all baselines vs latest results
```

Shared scoring functions stay in `tests/evals/scoring.py` and are imported by
every `scorer.py`. The `rubric_score()` G-Eval function needs to be added there.

---

## Standard runner contract

```python
# evals/<name>/runner.py
def run(
    variants: list[str] | None = None,   # None = all
    dataset_size: str = "pilot",          # "pilot" | "full"
    compare_to_baseline: bool = True,
    log_to_braintrust: bool = False,      # True in Phase 7 only
) -> dict:
    """
    Returns {
        "variants": {name: {"scores": {...}, "per_case": [...]}},
        "regressions": [...],
        "recommendation": "ship" | "dont_ship" | "investigate",
        "status": "pass" | "fail"
    }
    """
```

Make targets:

```makefile
eval-<name>-pilot:
    poetry run python evals/<name>/runner.py --size pilot

eval-<name>:
    poetry run python evals/<name>/runner.py --size full --braintrust

eval-all:
    $(MAKE) eval-retrieval eval-extraction
```

---

## CI regression gate

`evals/check_regressions.py` — hard fail for deterministic metrics,
PR comment annotation for LLM-judge scores:

```python
HARD_FAIL_METRICS = {"recall_at_10", "mrr", "ndcg_at_10", "classification_accuracy"}
SOFT_FAIL_METRICS = {"rubric_score", "faithfulness_score"}  # → PR comment only

TOLERANCES = {
    "recall_at_10": 0.02,
    "mrr": 0.02,
    "ndcg_at_10": 0.02,
    "classification_accuracy": 0.00,
    "rubric_score": 0.05,
    "faithfulness_score": 0.05,
}
```

---

## Suggested first implementation order

1. Add `rubric_score()` (G-Eval pattern) to `tests/evals/scoring.py`
2. Build `evals/extraction/` — hypothesis, dataset from the 10 articles in
   `entity-extraction-eval.md`, rubric, runner
3. Run pilot on those 10 articles, set baselines, confirm harness works
4. Write the `/eval` skill file at `.claude/skills/eval/skill.md`
5. Add `make eval-extraction` and `make eval-retrieval` targets
6. Migrate `scripts/run_evals.py` → `evals/retrieval/runner.py`
7. Add `evals/check_regressions.py` for CI
