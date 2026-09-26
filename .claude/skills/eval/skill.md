---
name: eval
description: "End-to-end evaluation workflow: hypothesis → criteria → data → pilot → scale → decision → promote."
user-invokable: true
---

# /eval — End-to-End Evaluation Workflow

Runs the full scientific loop for evaluating any system change: prompt, retrieval
method, feature, or architectural variant. Not a test runner — a structured
experiment from hypothesis to shipping decision.

**Invoke**: `/eval new <name>` to start a new eval, `/eval run <name>` to run one.

---

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

---

## Eval inventory (what already exists)

Before starting a new eval, check what's already built:

```bash
ls evals/ 2>/dev/null && echo "---" && ls content-queue-backend/tests/evals/
```

Existing evals at a glance:

| Eval | Location | Scale | Runnable | What it measures |
|------|----------|-------|----------|-----------------|
| Search routing | `tests/evals/test_search_evals.py` | 7 synthetic articles, 17 queries | ✅ pytest | Classification accuracy, hit rate |
| Retrieval quality | `tests/evals/test_retrieval_evals.py` | 61 articles, 32 real queries | ✅ pytest | R@10, MRR, NDCG — regression gate |
| Tagging quality | `tests/evals/test_tagging_evals.py` | 10 articles | ✅ pytest | Specificity, coverage, forbidden-tag rate |
| MCP contracts | `tests/evals/test_mcp_evals.py` | Test DB | ✅ pytest | Response shape contracts |
| PDF extraction | `experiments/pdf-extraction-eval/` | Corpus | ✅ full | Backend comparison (3 variants) |
| Entity extraction | `docs/design/systems/entity-extraction-eval.md` | 10 articles | ❌ prose only | Quality rubric — **needs formalization** |

---

## The 10-phase loop

Every eval follows this sequence. Do not skip phases.

```
1. Hypothesis    What do I believe, and why?
2. Criteria      What does "better" mean, and how do I measure it?
3. Data          What ground truth do I need?
4. Harness check Does the eval itself work before I trust its output?
5. Pilot         8–12 cases, all variants, <60 seconds
6. Interpret     Patterns before scaling. Go / no-go.
7. Scale         Full dataset, all variants, Braintrust logging
8. Interpret     Aggregate + per-case breakdown + failure taxonomy
9. Decision      Ship / don't ship / investigate
10. Promote      Update baselines, docs, commit
```

---

## Phase 1 · Hypothesis

Ask the user to fill this template before any code runs:

```
Evaluating:    [system name — e.g., "entity extraction prompt", "retrieval threshold"]
Variants:      A: [baseline/current]  B: [change 1]  C: [change 2]  (max 4)
Hypothesis:    "I believe [variant] will outperform [baseline] on [metric]
                because [mechanism]. I expect [specific behavior change]."
Falsified by:  [what result would prove the hypothesis wrong?]
Success cases: 3 specific inputs where the hypothesis predicts improvement
Failure cases: 3 specific inputs that should NOT regress
```

Write to `evals/<name>/hypothesis.md`.

**Stop if** the user cannot write a falsifiable hypothesis. The eval is premature.

Why: without a hypothesis, results get rationalized post-hoc. The hypothesis also
defines what the pilot needs to show before you scale.

---

## Phase 2 · Criteria

Propose metrics based on eval type. User confirms or adjusts.

### Retrieval evals
- Primary: **Recall@10** — did we find the right articles?
- Secondary: **MRR** — did the right article rank first?
- Guard rail: no regression on queries currently at R@10 = 1.0

### Extraction / classification (determinate outputs)
- **Precision, Recall, F1** against labeled ground truth
- Guard rail: no increase in noise entity or forbidden-tag rate

### Open-ended outputs (descriptions, summaries, synthesis)
- **G-Eval rubric score** (see Phase 2b below)
- Guard rail: faithfulness score stays above stored floor

### CI failure mode per type
- Deterministic metrics (R@K, accuracy, F1) → **hard fail** on threshold breach
- LLM-as-judge scores → **PR comment** only (probabilistic, false positives too costly)

### Phase 2b · G-Eval rubric design (open-ended tasks only)

G-Eval is the most production-validated LLM-as-judge pattern (Liu et al., 2023,
8M+ production runs). Mechanism: expand the task into chain-of-thought evaluation
steps, use those as a scoring rubric.

Steps:
1. Write 3–5 orthogonal quality dimensions (e.g., concept_precision, relation_quality, description_usefulness)
2. For each dimension: write explicit **chain-of-thought steps** a judge would follow
3. For each dimension: write **anchors** at 1 (bad), 3 (acceptable), 5 (ideal)
4. Assign **weights** summing to 1.0
5. Set **pass threshold** (e.g., weighted score ≥ 0.70)
6. **Validate rubric**: run it on 3 known-good examples (expect ≥ 0.85) and 3
   known-bad examples (expect ≤ 0.40). If either fails, fix the rubric first.

**Key rule for conceptual pairs:** if two extracted entities form a conceptual pair
or spectrum (e.g., `centaur` / `reverse-centaur`, `fast thinking` / `slow thinking`),
their descriptions must explicitly articulate what distinguishes them. Generic
descriptions of related concepts will cause spurious deduplication.

Write rubric to `evals/<name>/rubric.py`.

---

## Phase 3 · Data

Three sources in order of preference:

**A. Existing labeled data** — check `tests/evals/` and `evals/` first. If a
dataset covers the system being evaluated, use it. Document which subset applies.

**B. Real corpus + hand labels** — for retrieval: query → relevant article UUIDs,
labeled against the actual library. For extraction: article → ideal output,
reviewed by you. Every label needs a written rationale. Labels must be created
independently of the system being evaluated.

**C. Synthetic + human review** — generate candidate cases, human-filter.
Do NOT use unreviewed synthetic data as ground truth.

**Size targets:**
- Pilot: **8–12 examples** — fast, catches harness bugs, under 60 seconds
- Full eval: **30–100 examples** — enough to trust a 5pp delta

Write pilot dataset to `evals/<name>/dataset/pilot.py`.
Write full dataset to `evals/<name>/dataset/full.py`.

---

## Phase 4 · Harness check

Run these checks before Phase 5. Stop if any fail.

```python
# 1. Baseline sanity: run baseline variant on pilot dataset
#    Expected: scores match stored baseline ± 0.03
#    If deviation > 0.05: harness is broken or baseline is stale — investigate

# 2. Scorer sanity:
#    - Run on one manually-verified good example → expect score ≥ 0.85
#    - Run on one manually-verified bad example  → expect score ≤ 0.40
#    If either fails: scorer has a bug

# 3. Data sanity: spot-check 3 random examples
#    Are the labels reasonable? Are expected outputs actually correct?
```

**Common harness bugs caught here:**
- Wrong `mode=` parameter (entity lane only active in `mode="full"`)
- Scorer computing the wrong metric (e.g., MRR vs R@10 confusion)
- Dataset keys not matching production article UUIDs
- Baseline stored from a different corpus state

---

## Phase 5 · Pilot

Run all variants on the pilot dataset (8–12 cases):

```bash
poetry run python evals/<name>/runner.py --size pilot
```

Required output format — aggregate:

```
Variant A (baseline):    recall@10=0.75  mrr=0.80  [N cases]
Variant B (<change>):    recall@10=0.80  mrr=0.85
Variant C (<change>):    recall@10=0.85  mrr=0.90
```

Required output format — per-case:

```
case_key              A      B      C      B−A    C−A
context_engineering   1.00   1.00   1.00   —      —
enshittification      0.00   0.50   0.50   +0.50  +0.50
anthropic_products    0.67   0.33   0.67   −0.34  —
```

**Stop if** a variant scores 0.0 across all cases — that's an implementation bug,
not a finding. Fix it before continuing.

---

## Phase 6 · Interpret pilot

Required before scaling. Produce:

1. **Pattern identification** — which query/case categories moved? Is improvement
   concentrated in a specific type (e.g., vocabulary-distant queries, long articles)?
   Does regression have a clear cause (e.g., hub entity fan-out, score displacement)?

2. **Hypothesis check** — does the pilot directionally support the hypothesis?
   If contradicted across all cases: is the hypothesis wrong, or is the pilot too
   small? Most common cause of surprise: implementation bug, not a real finding.

3. **Go / no-go:**
   - **Go**: results directionally match hypothesis, no implementation bugs
   - **No-go**: results random, all variants tie, or harness appears broken
   - **Investigate**: one variant has unexpected behavior worth understanding first

Do not scale until this interpretation is complete and a go decision is made.

---

## Phase 7 · Scale

Run all variants on the full dataset.

Before running, estimate cost for LLM-heavy variants:

```
Estimated: N examples × M variants × $X/call = $Y. Proceed? [y/n]
```

Run command:

```bash
poetry run python evals/<name>/runner.py --size full --braintrust
```

Log each variant as a **separate Braintrust experiment** — this enables the
per-test-case comparison UI and the GitHub Actions PR comment diff table.
Designate variant A (baseline) as the Braintrust baseline experiment.

Results also written locally to `evals/<name>/results/<variant>_<timestamp>.json`.

---

## Phase 8 · Interpret full results

**Aggregate table:**

```
variant                   recall@10  mrr    ndcg@10  regressions  improvements
A (baseline)              0.721      0.773  0.781    —            —
B (threshold 0.40)        0.748      0.897  0.812    4            5
C (score passthrough)     0.764      0.859  0.831    2            5
```

**Per-case breakdown:** always include — aggregate numbers alone hide which
cases flipped and why. With 32 queries, a 3pp delta = ~1 query.

**Residual failure taxonomy:** for every case still failing, classify why:

| Type | What it means | What fixes it |
|------|--------------|---------------|
| Vocabulary gap | Query words don't appear in article | Concept entity bridging |
| Entity gap | No entity spans these articles | Extraction improvement |
| Corpus gap | Article not in corpus / not indexed | Ingest / backfill |
| Algorithm gap | Fixable by further tuning | More variants |
| Data gap | Ground truth label may be wrong | Re-examine the label |

**Statistical note:** treat deltas < 5pp on < 50 cases as directional evidence,
not proof. Always look at per-case breakdown.

---

## Phase 8b · Report format requirements

The full result is written to `evals/<name>/results/report.md`. The report
**must** include all of the following sections, in order, with a TOC at the top:

**TOC** — numbered anchor links to every section. Always first.

**Metric definitions** — for every metric used, state:
- What it measures
- Scale (e.g. 0.0–1.0)
- What a specific value means in concrete terms (e.g. "R@10=0.75 on a query with
  4 expected articles means 3 of 4 were found in the top-10")
- What a 1pp delta means given dataset size (e.g. "1pp on 45 queries ≈ 0.45
  queries changing — treat deltas < 2pp as directional only")

**Variants** — mechanical description of what each variant adds or changes.
Not just a label — explain the implementation difference.

**Extraction prompt history** — if entity extraction or any LLM-generated
intermediate is involved, document which prompt version was used, what changed
from the previous version, and what the extraction eval showed. Reader should
understand the extraction state without reading another doc.

**Query taxonomy** — if tiers, categories, or labels appear in the dataset,
explain them. State explicitly whether they are human-assigned or inferred from
experiment. Define every value.

**Full per-query table** — must include the actual query text, not just the key.
Keys are opaque to anyone reading the report without the source file open.

**Entity bridge wins and regressions** — separate sections for each. Per-case
root cause, not just a count. State which entity, what sim score, which articles
it pointed to vs which articles were expected.

**Phase 9 decision** — explicit ship / don't ship / investigate with the
criteria checklist showing which passed and which failed.

---

## Phase 9 · Decision

Produce one of three recommendations:

**Ship variant X** when ALL of:
- Primary metric improved ≥ 2pp
- No guard rail regressions (cases that were 1.0 stayed 1.0)
- Remaining regressions have documented root causes
- Hypothesis confirmed

**Don't ship** when ANY of:
- No primary metric improvement
- Guard rail regressions with no clear fix
- Improvement on one metric, regression on another, no accepted tradeoff

**Investigate further** when:
- Delta < 2pp on < 30 cases (directional but inconclusive)
- One variant shows unexpected behavior worth understanding
- A root cause was identified suggesting a better variant to try

User makes the final call. You provide evidence and recommendation.

---

## Phase 10 · Promote

If shipping:

1. Update `evals/<name>/baselines.json`:
   ```json
   {
     "recall_at_10": 0.764,
     "mrr": 0.859,
     "_updated": "2026-07-06",
     "_reason": "score passthrough fix, +4.3pp R@10 vs rank-based RRF"
   }
   ```

2. Update `ARCHITECTURE.md` if system configuration changed (thresholds, modes, prompts)

3. Update the relevant system doc in `docs/design/systems/`

4. Suggest commit message:
   ```
   perf(search): score passthrough in entity RRF, +4.3pp recall@10
   ```

If not shipping: document the finding in `evals/<name>/results/` with what was
learned. Negative results are still results — note them in the system doc.

---

## Artifact layout

```
evals/                          ← project root (shared across backend/frontend)
  <name>/
    hypothesis.md               ← Phase 1 (committed)
    dataset/
      pilot.py                  ← 8–12 labeled cases (committed)
      full.py                   ← 30–100 labeled cases (committed)
    rubric.py                   ← G-Eval rubric, if applicable (committed)
    scorer.py                   ← scoring functions for this eval (committed)
    runner.py                   ← entry point: all variants, results, Braintrust (committed)
    baselines.json              ← {metric: score, _updated, _reason} (committed)
    results/                    ← gitignored
      <variant>_<timestamp>.json
  check_regressions.py          ← CI gate: baselines vs latest results
```

Shared scoring functions: `content-queue-backend/tests/evals/scoring.py`
(R@K, MRR, NDCG, faithfulness, rubric_score). Every `scorer.py` imports from there.

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

## Standard runner contract

Every `runner.py` implements:

```python
def run(
    variants: list[str] | None = None,   # None = all variants
    dataset_size: str = "pilot",          # "pilot" | "full"
    compare_to_baseline: bool = True,
    log_to_braintrust: bool = False,      # True only for --full runs
) -> dict:
    """
    Returns {
        "variants": {name: {"scores": {...}, "per_case": [...]}},
        "regressions": [...],
        "recommendation": "ship" | "dont_ship" | "investigate",
        "status": "pass" | "fail"
    }
    Writes results/latest.json.
    Logs one Braintrust experiment per variant if log_to_braintrust=True.
    """
```

---

## CI regression gate

`evals/check_regressions.py` reads each eval's `baselines.json` and
`results/latest.json`. Hard fail for deterministic metrics; soft fail
(PR comment) for LLM-judge scores:

```python
HARD_FAIL = {"recall_at_10", "mrr", "ndcg_at_10", "classification_accuracy", "f1"}
SOFT_FAIL = {"rubric_score", "faithfulness_score"}  # → exit 0, write comment

TOLERANCES = {
    "recall_at_10": 0.02,
    "mrr": 0.02,
    "ndcg_at_10": 0.02,
    "classification_accuracy": 0.00,
    "f1": 0.03,
    "rubric_score": 0.05,
    "faithfulness_score": 0.05,
}
```

---

## What this skill does NOT do

- Does not make the ship/don't-ship decision — that's yours
- Does not generate or approve ground truth labels — human review required
- Does not run in CI automatically — CI only runs `check_regressions.py` on stored baselines
- Does not replace unit tests — use pytest for correctness, `/eval` for quality

---

## Cross-references

- After shipping a variant, run `/pre-commit-dev` before committing
- `/finalize` before creating a PR
- System eval results go in `docs/design/systems/` (e.g., `entity-retrieval-eval.md`)
- Braintrust experiment naming convention: `<eval-name>-<variant>-<YYYY-MM-DD>`
