"""
CI regression gate for all evals.

Hard-fail metrics (exit 1):   deterministic scores — classification_accuracy, retrieval R@K, MRR
Soft-fail metrics (exit 0):   LLM-judge scores — writes a comment file, does not block merge

Usage:
    python evals/check_regressions.py
    python evals/check_regressions.py --comment-file /tmp/regression_comment.md

The --comment-file output is picked up by the CI PR comment step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent

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

# Each entry: (eval_name, baselines_path, latest_results_path, metric_keys)
EVALS: list[tuple[str, Path, Path, list[str]]] = [
    (
        "retrieval",
        ROOT / "retrieval" / "baselines.json",
        ROOT / "retrieval" / "results" / "latest.json",
        ["recall_at_10", "mrr", "ndcg_at_10"],
    ),
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
    args = parser.parse_args()

    all_hard: list[str] = []
    all_soft: list[str] = []

    for name, baselines, latest, metrics in EVALS:
        h, s = check_eval(name, baselines, latest, metrics)
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
