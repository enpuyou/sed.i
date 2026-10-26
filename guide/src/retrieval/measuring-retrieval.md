---
layout: layouts/chapter.njk
title: Measuring retrieval
part: 2
order: 5
summary: How the project decides whether a change made search better, what the numbers mean, and what the evaluation cannot tell you.
verified: 2026-10-25
permalink: /retrieval/measuring-retrieval/
---

## In one minute

An evaluation ("eval") is a fixed set of questions with known right answers, plus a script that scores the search engine on them. sed.i has one for retrieval: 45 hand-written queries against the author's own 61-article library, where each query lists the articles that should come back. The script scores each result list with three numbers (recall at 10, MRR, NDCG) and compares four versions of search, A to D, adding one component at a time. A regression gate in continuous integration compares stored scores with stored baselines and fails the build when a named metric drops by more than a tolerance.

The evals changed what shipped twice: the entity-search lane was switched off after it scored below chunk-only search, and a paid reranking step was removed after its own eval said not to ship it. A third eval chose a prompt for the memory profile (0.8412 against 0.6663). The weakness is size and bias: 45 queries from one person's library cannot detect a difference of a point or two, and several parts of search (chunk size, thresholds, speed) have no eval at all.

## The problem

**How do you know a change to search made it better?**

The obvious approach is to try a few queries and look. It fails in three ways. You remember the queries you tried, not the ones that quietly got worse. A change that helps one kind of query often hurts another, and eyeballing three queries shows one side. And a number you can write down lets someone else (or you, in three months) check the claim.

An eval turns "feels better" into "recall at 10 went from 0.7459 to 0.7652 on these 45 queries". That is still a small sample and has to be read as one. This chapter explains how the numbers are made so that the claims in the other retrieval chapters can be checked.

## Mental model

An eval is an exam for the search engine. The questions are queries, the answer key is the list of articles a person decided should come back, and the score is how many of those the engine returned and how high it ranked them. A regression gate is the rule "this term's grade may not fall below last term's by more than a set amount".

| Term | Meaning here |
|---|---|
| Relevant set | The articles a person judged should be returned for a query (binary: relevant or not). Called ground truth or the answer key. |
| Top k | The first k results; k is 10 in these evals because the product shows a list. |
| Recall@k | Of the relevant articles, the fraction that appear in the top k. |
| MRR | Reciprocal rank of the first relevant result: 1 for rank 1, 1/2 for rank 2, and so on; averaged over queries it is the "mean". |
| NDCG@k | A score that rewards relevant results more the higher they rank. Normalised by the best possible ordering, so 1.0 is perfect. |
| Judged relevance | A person labelled which articles are relevant. The retrieval eval uses this. |
| LLM judge | A language model scores an output against a rubric. The memory and research evals use this because there is no fixed answer to compare with. |
| Baseline | A stored score from an accepted run. |
| Regression gate | A check that fails when a metric falls below its baseline by more than a tolerance. |

{% fig "From a query to a score" %}
```text
45 queries, each with a hand-labelled list of relevant articles
   |  run search variant A, B, C, D  (needs a database and an embedding call)
   v
a ranked list of article ids per query per variant
   |  recall@10, MRR, NDCG@10  (pure functions, no model)
   v
mean over 45 queries, per variant  ->  written to a results JSON (git-ignored)
   |
   |  CI: compare stored numbers with baselines.json
   v
pass, soft warning, or hard failure
```
{% endfig %}

## Walkthrough: three metrics on two result lists

The two lists are made up (illustrative), but the arithmetic is done by the repository's own functions (captured). Suppose a query has three relevant articles, `a`, `b`, `c`, and we look at the top 5 results. Search X returns `x a y b z`. Search Y returns `a x y z c`.

### Step 1: the functions

The metrics are pure functions in `content-queue-backend/tests/evals/scoring.py`; the retrieval eval imports them through a thin wrapper in `evals/retrieval/scorer.py`.

{% fig "tests/evals/scoring.py: recall and MRR" %}
<!--src content-queue-backend/tests/evals/scoring.py#24-31-->
```python
def recall_at_k(
    retrieved_ids: list[str], relevant_ids: list[str], k: int = 10
) -> float:
    """Fraction of relevant items found in the top-k retrieved."""
    if not relevant_ids:
        return 1.0
    retrieved_top_k = set(retrieved_ids[:k])
    return len(set(relevant_ids) & retrieved_top_k) / len(relevant_ids)
```
{% endfig %}

{% fig "tests/evals/scoring.py: NDCG" %}
<!--src content-queue-backend/tests/evals/scoring.py#63-71-->
```python
relevant_set = set(relevant_ids)

def dcg(ordered_ids: list[str]) -> float:
    return sum(
        (1.0 if id_ in relevant_set else 0.0) / math.log2(rank + 1)
        for rank, id_ in enumerate(ordered_ids[:k], start=1)
    )

ideal = dcg(list(relevant_ids)[:k])
```
{% endfig %}

### Step 2: the arithmetic

- **Recall@5** counts relevant articles found in the top 5, divided by the number of relevant articles. X finds `a` and `b`: 2/3. Y finds `a` and `c`: 2/3. They tie.
- **MRR** takes the rank of the first relevant result. X's first hit is `a` at rank 2: 1/2. Y's is `a` at rank 1: 1/1. Y wins.
- **NDCG@5** gives a hit at rank r a gain of 1/log2(r + 1): rank 1 is worth 1, rank 2 is 0.631, rank 3 is 0.5, rank 4 is 0.431, rank 5 is 0.387. Sum the gains of the relevant hits to get DCG (discounted cumulative gain). Divide by the DCG of the ideal list, where all three relevant articles are at ranks 1 to 3: 1 + 0.631 + 0.5 = 2.131. X has hits at ranks 2 and 4: 0.631 + 0.431 = 1.062, and 1.062 / 2.131 = 0.498. Y has hits at ranks 1 and 5: 1 + 0.387 = 1.387, and 1.387 / 2.131 = 0.651.

{% fig "Output of the repository's functions on the two lists (captured)" %}
```text
list X: ['x', 'a', 'y', 'b', 'z']  relevant: ['a', 'b', 'c']
  recall@5 = 0.6667
  mrr      = 0.5000
  dcg = 1.0616  ideal = 2.1309  ndcg@5 = 0.4982  (function: 0.4982)
list Y: ['a', 'x', 'y', 'z', 'c']  relevant: ['a', 'b', 'c']
  recall@5 = 0.6667
  mrr      = 1.0000
  dcg = 1.3869  ideal = 2.1309  ndcg@5 = 0.6508  (function: 0.6508)
empty relevant set: recall 1.0  ndcg 0.0
mean over the two lists: mrr 0.75
```
{% endfig %}

### Step 3: what each metric hides

Recall ignores order inside the top k, so X and Y tie. MRR looks only at the first hit, so it does not see that Y's second hit is last. NDCG sees order for every hit and so separates the lists by a middle amount (0.498 against 0.651). No single number is the truth; the project reports all three because they disagree exactly when ranking quality changes without recall changing.

The report's primary metric is recall@10, since the product shows a list of ten rather than a single answer. The last line of the output shows a quirk: with an empty relevant set, recall returns 1.0 and NDCG returns 0.0. The evals never pass an empty set (every query has at least one expected article), so it does not matter today.

### Step 4: from one query to a score

The retrieval eval does this for each of 45 queries and each of four variants and takes the mean. The queries are in tiers from "exact title words" (9 queries) to "cross-domain synthesis" (12 queries); 19 of the 45 have 4 expected articles, 10 have 3, 9 have 2, 5 have 1 and 2 have 5. One number for each variant:

| Variant | What it adds | R@10 | MRR | NDCG@10 |
|---|---|---|---|---|
| A | article-level embeddings + keyword | 0.7459 | 0.8276 | 0.6922 |
| B | passage (chunk) embeddings | 0.7652 | 0.8310 | 0.7038 |
| C | entity lane, rank-based | 0.7470 | 0.8206 | 0.6886 |
| D | entity lane, score added | 0.7581 | 0.7934 | 0.6796 |

These are measured: `evals/retrieval/results/report.md` and `evals/retrieval/baselines.json` (both committed), from a run of 2026-07-06. Not re-run for this guide because the runner embeds each query with a paid API.

## How small is 45 queries?

The report says deltas under 2 percentage points should be treated as directional. We can be more specific, using only the per-query results file from that run (measured by this guide, no model calls; the file is git-ignored, so it exists only on the author's machine). For each query take the difference in recall@10 between two variants, then resample the 45 differences with replacement 10,000 times and look at the middle 95% of the resulting means (a bootstrap).

| Comparison | Mean difference | Queries better / worse / unchanged | 95% interval |
|---|---|---|---|
| B minus A (add chunks) | +0.019 | 8 / 5 / 32 | -0.031 to +0.071 |
| D minus B (add entity lane) | -0.007 | 5 / 6 / 34 | -0.050 to +0.034 |
| D minus A | +0.012 | 9 / 8 / 28 | -0.052 to +0.078 |

Every interval includes zero. The evidence supports "chunks were probably not harmful and may help", and "the entity lane is not clearly different", and it supports nothing finer. Most queries (28 to 34 of 45) do not change at all between variants, so the average is decided by a handful of queries. The report says the same thing in words ("a 1pp delta on 45 queries is roughly 0.45 queries changing").

## Judged relevance, LLM judges and bias

### Who decides what is relevant

For retrieval, a person did. Each query lists article keys that should come back, written when the dataset was built. That is a judgement about the author's own library, not a ground truth that exists independently. The report does not say whether the labels were written before or after seeing results.

For outputs with no single right answer (a memory profile, a research brief) a language model scores against a rubric. The memory-consolidation eval uses five dimensions, each scored 1 to 5 and weighted (specificity 0.25, trajectory 0.30, depth asymmetry 0.20, behavioral pattern 0.15, faithfulness 0.10), with a pass mark of 0.70 and a hard fail if faithfulness is below 2. An LLM judge is cheap and scales, but it is noisy: the same prompt scored differently between two runs, and in one run it silently scored rate-limited calls as the minimum (below).

### Why an eval from one person's library is small and biased

| Property | Effect |
|---|---|
| One reader, one library of 61 articles | Results describe this library's topics (heavy on AI agents, tech criticism). A different library may behave differently. |
| Queries written by the builder | They know which articles exist, so queries may resemble the articles more than real queries would. Several tiers were designed to exercise the entity lane. |
| Category names encode outcomes | Categories such as `entity_wins` and `entity_regression` are named after what the lane was known to do, so the per-category table shows the labels, not independent evidence. |
| The same 45 queries guided the changes | The hub-cap and threshold experiments in the report used these queries. Tuning on the test set overstates how well a choice generalises. |
| Binary relevance | An article is relevant or not; a partly relevant article counts as non-relevant or fully relevant. |
| No real-user queries | The queries are not logged ones from users. |

None of this makes the eval useless. It makes it a regression and hypothesis-checking tool for one library, not a measure of how sed.i would do for a stranger.

## How it is built here

| File | Role |
|---|---|
| `evals/retrieval/runner.py` | runs variants A to D over the dataset against the local database; needs the embedding API for query vectors |
| `evals/retrieval/scorer.py` | re-exports the metric functions |
| `evals/retrieval/dataset/full.py` | the 45 queries (re-exports `tests/evals/retrieval_eval_dataset.py`) |
| `evals/retrieval/baselines.json` | stored scores per variant |
| `evals/retrieval/results/report.md` | the full write-up of the 2026-07-06 run |
| `evals/check_regressions.py` | compares stored results with baselines |
| `.github/workflows/evals-ci.yml` | the CI job |
| `content-queue-backend/tests/evals/` | pytest evals: search routing and hit rate, tagging, MCP contracts, and local-only retrieval tests |
| `evals/memory-consolidation-prompt/` | the prompt eval (rubric, dataset, runner, report) |
| `evals/research-brief/` | the research-brief eval |
| `evals/hnsw-index/` | an index speed benchmark |

### Evals under pytest

`content-queue-backend/tests/evals/` has four suites.

- **Search** (`test_search_evals.py`): seeds 7 articles and 17 queries into a test database, then checks that the query router sends each query to the right path (at least 80%), keyword hit rate is at least 70%, and hybrid hit rate is at least 75% with MRR at least 0.60. It prints latency but does not assert on it. Needs an OpenAI key for the semantic queries.
- **Tagging** (`test_tagging_evals.py`): 8 examples; checks tag specificity and count and that generic tags such as "AI" are absent; average score at least 0.70. Needs a key and is skipped without one.
- **MCP contracts** (`test_mcp_evals.py`): 8 tool examples; each tool returns the expected shape, handles an empty library, and cannot read another user's data. Needs no key.
- **Retrieval** (`test_retrieval_evals.py`): runs the 45 queries against the author's local database and asserts that recall has not dropped by more than 0.10 (single-hop) or 0.20 (multi-hop) from each query's stored score. It is not in the CI workflow, since CI has no copy of the library. Six cases that depend on the entity lane are skipped while the lane is off.

### The CI gate

`evals/check_regressions.py` holds two sets of metric names and a tolerance for each:

{% fig "evals/check_regressions.py: which metrics block a merge" %}
<!--src evals/check_regressions.py#22-35-->
```python
# Hard-fail: a drop here means something is measurably broken.
HARD_FAIL = {"classification_accuracy", "search_hit_rate_at_10", "search_mrr"}
# Soft-fail: LLM-judge scores are probabilistic — flag but don't block.
SOFT_FAIL = {"rubric_score_avg", "pass_rate"}

# Maximum drop allowed before flagging (absolute).
TOLERANCES: dict[str, float] = {
    "classification_accuracy": 0.00,
    "search_hit_rate_at_10": 0.02,
    "search_mrr": 0.02,
    "rubric_score_avg": 0.05,
    "rubric_score_min": 0.05,
    "pass_rate": 0.05,
}
```
{% endfig %}

The design is sound: deterministic metrics (classification accuracy, hit rate) block the merge; LLM-judge metrics only post a comment, because a noisy judge should not fail a build. In the workflow, the job runs on pull requests that touch search, tagging, research or evals code, runs the three pytest suites, runs this script, and posts a comment with the results.

The implementation does not do what the design says for retrieval. Running the script's functions on the files on the author's machine, and on two invented ones (captured):

{% fig "check_regressions on the shipped files, and on two invented ones (captured)" %}
```text
HARD_FAIL: ['classification_accuracy', 'search_hit_rate_at_10', 'search_mrr']
SOFT_FAIL: ['pass_rate', 'rubric_score_avg']
retrieval metrics compared: ['recall_at_10', 'mrr', 'ndcg_at_10']
shipped files -> (hard=[], soft=[])
flat baseline, recall -0.04 ->
  hard=[]
  soft=['retrieval.recall_at_10: 0.7252 < baseline 0.7652 (drop=+0.0400)']
hard-listed metric, search_mrr -0.05 ->
  hard=['x.search_mrr: 0.6000 < baseline 0.6500 (drop=+0.0500)']
  soft=[]
```
{% endfig %}

Four findings, all from reading and running the code:

1. In CI nothing is compared. The run files `evals/*/results/*.json` are git-ignored (only `baselines.json` and `report.md` are committed), so on a fresh checkout the script prints a soft warning, "no latest.json", for both retrieval and research-brief (captured below).
2. Even with the file present, as on the author's machine, retrieval compares nothing. The baselines file is keyed by variant (`variant_A_item_only` and so on), with no top-level `recall_at_10`, so the lookup finds nothing and the script prints "All eval baselines OK".
3. The retrieval metrics (`recall_at_10`, `mrr`, `ndcg_at_10`) are not in `HARD_FAIL`. If the baselines file were flat, a recall drop would be a soft warning, as the second line of output above shows. The docstring says retrieval R@K and MRR hard-fail.
4. The function meant to check the pytest thresholds (`check_pytest_baselines`) returns empty lists. The thresholds in `content-queue-backend/tests/evals/baselines.json` (hit rate 0.75, MRR 0.65) are not read by the script; the real hard failures are the `assert` lines in the pytest suites themselves.

{% fig "check_regressions on a fresh checkout: no results file (captured)" %}
```text
retrieval:      hard=[] soft=['retrieval: no latest.json — eval has not been run yet']
research-brief: hard=[] soft=['research-brief: no latest.json — eval has not been run yet']
```
{% endfig %}

So today the gate that blocks merges is the pytest assertions (router accuracy, hit rate, MRR, MCP contracts). The 45-query retrieval eval is a manual tool that CI cannot run (it needs the author's library and a paid embedding call), and its result is not checked automatically.

## The reranker decision

A reranker is a second pass that re-orders the top candidates with a more accurate (and slower) model. The project built one with Cohere Rerank v3.5 for the automatic search mode, evaluated it, and shipped it without gating. It was later removed. The numbers, from `docs/changelog/2026-09-25-reranker-removal-entity-flag-eval-sync.md` and item 5 of `docs/plans/sota-gap-action-plan.md` (the eval directory itself was deleted, so these figures are quoted, not re-checked):

| Variant | R@10 | MRR | NDCG@10 |
|---|---|---|---|
| B: chunks, no rerank | 0.7478 | 0.7873 | 0.6721 |
| E: B + Cohere rerank | 0.7793 | 0.7975 | 0.6949 |

That is +3.15 points of recall, above the eval's own bar of 2 points. The eval nevertheless recommended "investigate, not ship as-is", for two reasons: a case named in advance as must-not-regress (`teen_culture_identity`) fell from 1.00 to 0.75, and the gains and losses followed a pattern. Reranking helped concrete and entity-bridge queries and hurt abstract concept-bridge queries, so the aggregate gain is two opposing effects netting out. It shipped anyway, applied to every automatic search, with no gating by query type and no spend control, as the only paid third-party call in the search path.

It was removed on 2026-09-25. Two stated reasons: the eval had not recommended shipping, and the product direction moved to more users under a hard cost limit. The proposed way back is to rerank only non-abstract queries using the existing query classifier. Latency was never measured (`docs/plans/sota-layer-plan.md` leaves the budget analysis unchecked). The reranker's own B baseline (0.7478) differs from the 45-query run's B (0.7652), so these are two separate runs; the +3.15 is within one run.

Two lessons stand out. The guard-rail case did its job: a case written down before the experiment caught a regression the average hid. And the process failed at the next step: a "do not ship" outcome was shipped.

## A second eval-driven choice: the memory-consolidation prompt

The nightly memory job writes a short profile of what the user reads. Three prompts were compared on 10 synthetic activity logs, scored by an LLM judge on the five-dimension rubric above. A is the original prompt, B lists four things the profile must cover (trajectory, depth asymmetry, behavioral pattern, unread backlog), C asks for a "briefing note from one assistant to another".

| Run | A | B | C |
|---|---|---|---|
| First (2026-07-10) | 0.621 | 0.860 | 0.710 |
| Clean re-run (2026-09-25) | 0.6663 | 0.8412 | 0.7250 |

B was chosen both times. The clean re-run: B has 0.8412 against A's 0.6663 (+0.175), with pass rate 90% against 50% and no hard fails. Both are measured, from `evals/memory-consolidation-prompt/results/report.md` and `baselines.json`.

The re-run is the instructive part. The eval kept its own copy of the prompt, and it had drifted away from the shipped one, so the eval was scoring a prompt that was not in production. And a rate-limit bug in the harness recorded rate-limited judge calls as the lowest score: 21 of 150 dimension scores were corrupted, which made B look like it had fallen from 0.860 to 0.589. After the fix (retry with backoff) the numbers came back. A's score also moved between runs, which is judge noise on 10 cases. The report says deltas below about 0.10 are only directional at this size.

The targeted fix did not work on its target: the `eclectic_browser` case (a reader with no focus) still fails under B at 0.475, because the model still invents a coherent focus.

## What is not measured

| Not measured | Consequence |
|---|---|
| Chunk size, overlap, and the title prefix | The values (about 350 tokens, 40 words, a template) come from common practice; no eval compares 200 with 350 with 600. See [Chunking and embedding](/retrieval/chunking-and-embedding/). |
| Threshold values | The entity gate (0.40), the expansion gate (0.45) and the connections threshold (0.3) were not tuned against labelled data. The report did test the entity gate at 0.50 for five queries. |
| Highlight connections | No eval at all. See [Entity graph and connections](/retrieval/entity-graph-and-connections/). |
| Latency | The search pytest prints latency on a 7-article database and does not gate it. The index benchmark in `evals/hnsw-index/` measures index speed on synthetic vectors, not search as users experience it. The reranker's delay was never measured. |
| Real-user queries and relevance feedback | No logged queries or click data feed any eval. |
| Other users' libraries | One library only. |
| Result freshness | Evals use a fixed library; newly saved articles are not a test case. |
| Cost per query | Not part of any score, though it decided the reranker's fate. |

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Hand-labelled expected articles | Click logs; LLM-labelled relevance | Cheap, deterministic, and matches the product (a reading list). Cost: small and biased. |
| Recall@10 as the primary metric | MRR; NDCG | The UI shows ten results, so "was it in the list" matters most. MRR and NDCG are reported alongside. |
| Cumulative variants A to D | One change at a time from the same base | Shows what each component adds. Cost: C and D differ only in fusion, so D is not a clean test of "the entity graph". |
| Hard gate on deterministic metrics, soft on LLM judge | Gate everything; gate nothing | A noisy judge would fail builds at random. The idea is right; the retrieval metrics are not wired into it. |
| Tolerance of 0.02 on hit rate and MRR | Zero tolerance | With 17 queries in the CI set, one query is about 6 points, so a tolerance of 0.02 means any lost query fails. Reasonable for a regression check, and too tight to avoid false alarms if the labels or the data change. |
| Retrieval eval kept out of CI | Run in CI with a copy of the library | The library is private and the run calls a paid embedding API. Cost: nothing checks it automatically. |
| Keep the stored `report.md` | Only keep numbers | The write-up recorded the reasons for decisions, including the entity-lane shutdown. |

No ADR in `docs/decisions/` covers the eval harness.

## How it fails

| Failure | What happened | What was done |
|---|---|---|
| A harness bug hid in the aggregate | Rate-limited judge calls scored as the minimum; 21 of 150 scores were wrong | Retry with backoff; re-run clean |
| The eval drifted from production | The eval's copy of the memory prompt lacked a line that production had | Copied the production prompt verbatim |
| Stale baselines | The retrieval pytest baseline for six cases was measured with the entity lane on; the lane was later disabled | Those six cases are skipped while the lane is off |
| A guard rail was ignored | The reranker eval said "do not ship"; it shipped | Removed later |
| A gate that compares nothing | Retrieval baselines have no top-level metric keys | Not fixed (see below) |
| Noise read as signal | A 1-point difference on 45 queries is about half a query | The report states the 2-point rule; this chapter adds intervals |

## What is weak or missing

Ranked by how likely each is to matter.

1. **The retrieval regression gate does not gate.** CI never has a results file, the baselines file has no metric keys the script reads, the retrieval metrics are not in the hard-fail set, and the pytest-baseline check is empty. The fix is small: flatten the baselines to the shipped variant (currently B), add `recall_at_10` and `mrr` to the hard set with a tolerance sized to the sample, and read the pytest thresholds or drop the dead function.
2. **The sample is too small for the decisions it informs.** All differences between variants have intervals that include zero. Growing the set to a few hundred queries, mixing in real queries, and adding a second library would change what can be concluded.
3. **No evaluation of the connections feature or of chunk parameters.** Both are user-visible and rest on convention.
4. **Evals depend on private data and a paid API.** Nobody else can reproduce the retrieval numbers, and the runner needs the author's database. A synthetic corpus with fixed embeddings would allow a free CI run of the same metrics.
5. **Tuning on the test set.** The same 45 queries justify design changes and score them. A held-out split would keep one part unseen.
6. **Stale documents.** `evals/retrieval/hypothesis.md` still calls variant D "current production" although the lane is off, and the dataset docstring says 50 articles while the report says 61. Cheap to fix and easy to mislead.
7. **Metric edge case.** Recall returns 1.0 for an empty relevant set and NDCG returns 0.0.

{% say %}
An eval is a fixed set of queries with a hand-written list of the articles that should come back, and a script that scores search on them. I use recall at 10, which is how many of the right articles are in the first ten, MRR, which is how high the first right one ranks, and NDCG, which rewards ranking all of them near the top. I compared four versions: article embeddings, plus chunks, plus an entity lane in two forms. Chunks scored best, so the entity lane is off by default, and a paid reranker was removed after its own eval said not to ship it. A separate LLM-judged eval picked the memory prompt, 0.84 to 0.67. Be careful with the numbers: it is 45 queries from one library, differences of a point or two are inside the noise, and the CI gate for retrieval does not actually compare anything yet. Chunk size, thresholds, connections and latency have no eval.
{% endsay %}

## Check yourself

{% qa "Two search versions have the same recall@10 but different MRR. What does that tell you?" %}
They return the same relevant articles within the top ten, but one ranks the first relevant article higher. For a list that users scan from the top, the higher MRR is better. NDCG would also separate them if the later hits differ in rank.
{% endqa %}

{% qa "Variant D scored 0.7581 and variant B 0.7652 on recall@10. Is B better?" %}
Not demonstrably. The difference is 0.7 points, which is about one third of a query on 45 queries; five queries got better, six worse and 34 did not change, and the bootstrap interval runs from -5.0 to +3.4 points. What can be said is that D did not clearly beat B and that MRR and NDCG were also lower, so there was no case for the extra cost.
{% endqa %}

{% qa "Why does the gate treat LLM-judge scores as soft warnings and classification accuracy as a hard failure?" %}
Classification accuracy is deterministic: the same code on the same data gives the same number, so a drop means something changed. A judge model gives somewhat different scores on repeat runs (the memory eval's score for the same prompts moved by 0.02 to 0.05 between two runs), so a drop may be noise. Failing a build on noise teaches people to ignore the gate.
{% endqa %}

{% qa "The memory eval's first re-run showed B falling from 0.860 to 0.589. What was wrong, and how was it caught?" %}
Nothing in the prompt. The scorer recorded OpenAI rate-limit errors as the lowest rubric score, so 21 of 150 scores were corrupted. A fall that large with no prompt change was implausible, which led to the scorer's error handling (the report does not say how it was first noticed). The fix was a retry with backoff, and the clean run gave 0.8412.
{% endqa %}

{% qa "The reranker raised recall@10 by 3.15 points and was still removed. Give two reasons from the record." %}
A case named beforehand as must-not-regress fell from 1.00 to 0.75, and the gain was two opposing effects (helps concrete queries, hurts abstract ones) that cancelled in the average. It also added a paid external call to every automatic search with no query gating or budget check, and the product moved to a cost limit. The eval itself had said "investigate, not ship as-is".
{% endqa %}

{% qa "You want to know whether 250 tokens per chunk would beat 350. What do you need that does not exist yet?" %}
A way to rebuild the chunk table at a different size, run the same queries against both, and compare recall@10 and MRR with an interval. The query set also has to be large enough that a difference of a few points is detectable, which the 45-query set is not, so you would first add queries.
{% endqa %}
