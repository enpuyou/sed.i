"""
CI regression gate for all evals.

Hard-fail metrics (exit 1):   deterministic scores — classification_accuracy, retrieval recall@10 / MRR
Soft-fail metrics (exit 0):   LLM-judge scores and retrieval NDCG — writes a comment file, does not block merge

WHAT THE RETRIEVAL GATE PROTECTS. CI cannot re-run the retrieval eval (it needs the
author's private library and paid embeddings). The gate therefore compares two
COMMITTED files: evals/retrieval/accepted.json (snapshot of the last accepted eval
run) against evals/retrieval/baselines.json (the bar), for the production variant
(GATED_VARIANT). With --base-baselines it also fails if a PR lowers baselines.json
relative to the base branch. It protects the committed numbers, which are reviewable
in a PR; it does NOT measure the live system.

Accepting a new baseline: see evals/retrieval/runner.py docstring.

Usage:
    python evals/check_regressions.py
    python evals/check_regressions.py --comment-file /tmp/regression_comment.md
    python evals/check_regressions.py --base-baselines /tmp/base_baselines.json
    python evals/check_regressions.py --accept     # snapshot results/latest.json -> accepted.json

The --comment-file output is picked up by the CI PR comment step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# Hard-fail: a drop here means something is measurably broken.
HARD_FAIL = {"classification_accuracy", "search_hit_rate_at_10", "search_mrr", "recall_at_10", "mrr"}
# Soft-fail: LLM-judge scores are probabilistic — flag but don't block.
SOFT_FAIL = {"rubric_score_avg", "pass_rate"}

# Maximum drop allowed before flagging (absolute).
TOLERANCES: dict[str, float] = {
    "classification_accuracy": 0.00,
    "search_hit_rate_at_10": 0.02,
    "search_mrr": 0.02,
    # Retrieval harness metric names: 2 percentage points (45 queries cannot resolve less).
    "recall_at_10": 0.02,
    "mrr": 0.02,
    "ndcg_at_10": 0.02,
    "rubric_score_avg": 0.05,
    "rubric_score_min": 0.05,
    "pass_rate": 0.05,
}

# Retrieval gate: committed snapshot vs committed baselines (see module docstring).
RETRIEVAL_BASELINES = ROOT / "retrieval" / "baselines.json"
RETRIEVAL_ACCEPTED = ROOT / "retrieval" / "accepted.json"
RETRIEVAL_LATEST = ROOT / "retrieval" / "results" / "latest.json"
# Production variant (hybrid_search mode=full with the entity lane off,
# ENTITY_SEARCH_ENABLED=False). Only this variant is gated.
GATED_VARIANT = "variant_B_chunks"
RETRIEVAL_METRICS = ["recall_at_10", "mrr", "ndcg_at_10"]
# runner.py variant letter -> baselines.json key
VARIANT_KEYS = {
    "A": "variant_A_item_only",
    "B": "variant_B_chunks",
    "C": "variant_C_entity_rank_rrf",
    "D": "variant_D_entity_passthrough",
}

# Each entry: (eval_name, baselines_path, latest_results_path, metric_keys)
EVALS: list[tuple[str, Path, Path, list[str]]] = [
    (
        "research-brief",
        ROOT / "research-brief" / "baselines.json",
        ROOT / "research-brief" / "results" / "c_full_pilot.json",
        ["rubric_score_avg", "rubric_score_min", "pass_rate"],
    ),
]


def _normalize_research_brief(raw: dict) -> dict:
    """Extract flat metrics from the c_full_pilot.json structure."""
    results = raw.get("c_results", {})
    scores = [v["weighted_score"] for v in results.values() if "weighted_score" in v]
    passes = [v for v in results.values() if v.get("pass")]
    if not scores:
        return {}
    return {
        "rubric_score_avg": round(sum(scores) / len(scores), 4),
        "rubric_score_min": round(min(scores), 4),
        "pass_rate": round(len(passes) / len(scores), 4),
        "n_cases": len(scores),
    }


# Eval-specific result normalizers — applied before metric comparison.
NORMALIZERS = {
    "research-brief": _normalize_research_brief,
}

# Metrics in the pytest-based baselines.json (content-queue-backend/tests/evals/)
PYTEST_BASELINES = Path(__file__).parent.parent / "content-queue-backend" / "tests" / "evals" / "baselines.json"


def check_eval(
    name: str,
    baselines_path: Path,
    latest_path: Path,
    metrics: list[str],
) -> tuple[list[str], list[str]]:
    """Returns (hard_failures, soft_warnings)."""
    hard: list[str] = []
    soft: list[str] = []

    if not baselines_path.exists():
        soft.append(f"{name}: no baselines.json — skipping regression check")
        return hard, soft

    if not latest_path.exists():
        soft.append(f"{name}: no latest.json — eval has not been run yet")
        return hard, soft

    baseline = json.loads(baselines_path.read_text())
    raw = json.loads(latest_path.read_text())
    normalizer = NORMALIZERS.get(name)
    latest = normalizer(raw) if normalizer else raw

    for metric in metrics:
        if metric.startswith("_"):
            continue
        baseline_val = baseline.get(metric)
        latest_val = latest.get(metric)

        if baseline_val is None:
            continue
        if latest_val is None:
            soft.append(f"{name}.{metric}: not found in latest results (baseline={baseline_val:.4f})")
            continue

        drop = baseline_val - latest_val
        tolerance = TOLERANCES.get(metric, 0.02)

        if drop > tolerance:
            msg = f"{name}.{metric}: {latest_val:.4f} < baseline {baseline_val:.4f} (drop={drop:+.4f}, tolerance={tolerance})"
            if metric in HARD_FAIL:
                hard.append(msg)
            else:
                soft.append(msg)

    return hard, soft


def _load_variant_metrics(path: Path, label: str, errors: list[str]) -> dict:
    """Load {metric: float} for GATED_VARIANT; append a message to errors if unusable."""
    if not path.exists():
        errors.append(f"retrieval: {label} file missing: {path}")
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"retrieval: {label} is not valid JSON ({path.name}): {e}")
        return {}
    variant = data.get(GATED_VARIANT) if isinstance(data, dict) else None
    if not isinstance(variant, dict):
        errors.append(f"retrieval: {label} has no '{GATED_VARIANT}' object ({path.name})")
        return {}
    out = {}
    for metric in RETRIEVAL_METRICS:
        val = variant.get(metric)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            errors.append(f"retrieval.{GATED_VARIANT}.{metric}: missing or non-numeric in {label} (got {val!r})")
        else:
            out[metric] = float(val)
    return out


def check_retrieval(
    baselines_path: Path = RETRIEVAL_BASELINES,
    accepted_path: Path = RETRIEVAL_ACCEPTED,
    base_baselines_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Committed accepted snapshot vs committed baselines. Returns (hard_failures, soft_warnings).

    Missing files, malformed shape, or missing metrics are hard failures, never skips.
    """
    hard: list[str] = []
    soft: list[str] = []
    baseline = _load_variant_metrics(baselines_path, "baselines", hard)
    accepted = _load_variant_metrics(accepted_path, "accepted snapshot", hard)
    base: dict = {}
    if base_baselines_path is not None:
        base = _load_variant_metrics(base_baselines_path, "base-branch baselines", hard)

    for metric in RETRIEVAL_METRICS:
        name = f"retrieval.{GATED_VARIANT}.{metric}"
        if metric in baseline and metric in accepted:
            drop = round(baseline[metric] - accepted[metric], 6)
            tol = TOLERANCES[metric]
            print(f"  compared {name}: accepted={accepted[metric]:.4f} baseline={baseline[metric]:.4f} tolerance={tol}")
            if drop > tol:
                msg = f"{name}: accepted {accepted[metric]:.4f} < baseline {baseline[metric]:.4f} (drop={drop:+.4f}, tolerance={tol})"
                (hard if metric in HARD_FAIL else soft).append(msg)
        if metric in baseline and metric in base and baseline[metric] < base[metric]:
            msg = f"{name}: baselines.json lowered from {base[metric]:.4f} (base branch) to {baseline[metric]:.4f}"
            (hard if metric in HARD_FAIL else soft).append(msg)
    return hard, soft


def accept_latest(
    latest_path: Path = RETRIEVAL_LATEST, accepted_path: Path = RETRIEVAL_ACCEPTED
) -> None:
    """Write accepted.json from the runner's results/latest.json (variants[X].scores)."""
    raw = json.loads(latest_path.read_text())
    out: dict = {"_accepted": raw.get("_run_at"), "_dataset_size": raw.get("_dataset_size")}
    for letter, key in VARIANT_KEYS.items():
        scores = raw.get("variants", {}).get(letter, {}).get("scores")
        if scores:
            out[key] = {m: scores[m] for m in RETRIEVAL_METRICS}
    if GATED_VARIANT not in out:
        raise SystemExit(f"latest.json has no scores for {GATED_VARIANT}; run the runner with variant B")
    accepted_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {accepted_path}")


def check_pytest_baselines() -> tuple[list[str], list[str]]:
    """Check the shared pytest-based baselines (classification, search hit rate, MRR)."""
    hard: list[str] = []
    soft: list[str] = []

    if not PYTEST_BASELINES.exists():
        return hard, soft

    # The pytest evals write results inline — these are checked via pytest exit code, not here.
    # We just surface the stored thresholds so CI output is informative.
    return hard, soft


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comment-file", help="Write markdown summary to this path")
    parser.add_argument("--base-baselines", help="baselines.json from the PR base branch; fail if lowered")
    parser.add_argument("--accept", action="store_true", help="snapshot results/latest.json into accepted.json and exit")
    args = parser.parse_args()

    if args.accept:
        accept_latest()
        return

    all_hard: list[str] = []
    all_soft: list[str] = []

    for name, baselines, latest, metrics in EVALS:
        h, s = check_eval(name, baselines, latest, metrics)
        all_hard.extend(h)
        all_soft.extend(s)

    h, s = check_retrieval(
        base_baselines_path=Path(args.base_baselines) if args.base_baselines else None
    )
    all_hard.extend(h)
    all_soft.extend(s)

    h, s = check_pytest_baselines()
    all_hard.extend(h)
    all_soft.extend(s)

    # Console output
    if all_hard:
        print("HARD FAILURES (will block merge):")
        for msg in all_hard:
            print(f"  FAIL: {msg}")

    if all_soft:
        print("SOFT WARNINGS (informational only):")
        for msg in all_soft:
            print(f"  WARN: {msg}")

    if not all_hard and not all_soft:
        print("All eval baselines OK.")

    # Markdown comment for CI
    if args.comment_file:
        lines = ["## Eval Regression Gate\n"]
        if all_hard:
            lines.append("### Hard failures\n")
            for msg in all_hard:
                lines.append(f"- `{msg}`")
            lines.append("")
        if all_soft:
            lines.append("### Soft warnings (informational)\n")
            for msg in all_soft:
                lines.append(f"- `{msg}`")
            lines.append("")
        if not all_hard and not all_soft:
            lines.append("All baselines within tolerance.")

        Path(args.comment_file).write_text("\n".join(lines))

    sys.exit(1 if all_hard else 0)


if __name__ == "__main__":
    main()
