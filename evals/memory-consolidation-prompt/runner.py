"""
Runner for memory consolidation prompt eval.

Usage:
    poetry run python -m evals.memory_consolidation_prompt.runner --size pilot
    poetry run python -m evals.memory_consolidation_prompt.runner --size pilot --variants A,C

Variants:
    A — baseline old prompt (single prompt, structured output with knowledge_gaps)
    B — new prompt with explicit four-dimension instructions (current production)
    C — briefing framing (emergent prioritization, no checklist)

Output:
    evals/memory-consolidation-prompt/results/latest.json
    Console: aggregate table + per-case deltas
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

_VARIANT_A_PROMPT = """\
You are a personal reading assistant. Based on the reading activity below, build a profile of this user.

Reading activity:
{activity}

Extract:
- current_focus: what topics they are currently focused on
- reading_velocity: fast, deep, or browsing
- knowledge_gaps: list of topics they seem interested in but haven't read deeply about
- episodic_events: notable reading events or shifts in the past period

Be concise and accurate. Only include information evident from the activity.
"""

_VARIANT_B_PROMPT = """\
You are building a first-time memory profile for a personal reading assistant.
This is a BOOTSTRAP — derive the profile from scratch based on all available reading activity.

Reading activity:
{activity}

Produce a profile that captures WHO this reader is and WHAT they are working toward —
not just what topics appear, but the patterns, depth, and trajectory.

Rules for current_focus:
- Be specific about sub-domain, not just the parent field.
  BAD:  "artificial intelligence"
  GOOD: "AI engineering and agent systems — context engineering, LLM evals, forward-deployed roles"
- If multiple distinct threads exist, name the primary one and note the secondary.

Rules for reading_velocity:
- Infer from behavioral signals (read_position %, highlight count), not topics.
- fast = consistently high read% but few highlights (skimming for coverage)
- deep = high read% AND multiple highlights per article (sustained engagement)
- browsing = saved many articles but low read% overall (collecting, not consuming)
- Note: a user can be "deep" on technical content but "fast" on news — pick the dominant pattern.

Rules for memory_text (free-form prose, 3-6 sentences):
Write as if briefing a new assistant who needs to know this person quickly.
Cover all four of:
1. Trajectory — what are they building toward or preparing for?
2. Depth asymmetry — which topics do they engage with deeply vs. skim?
3. Behavioral pattern — heavy saver vs. active reader, highlight-heavy vs. passive?
4. Unread backlog signal — topics they save repeatedly but never open deeply (anxiety-saving or unresolved interest)

Be specific and grounded in the evidence. Do not generalize beyond what the activity shows.
Avoid filler like "the user is interested in". State what the data shows.
"""

_VARIANT_C_PROMPT = """\
You are building a first-time memory profile for a personal reading assistant.

Reading activity:
{activity}

Write a briefing note from one assistant to another. Imagine the next assistant has
never met this user but needs to give them a genuinely useful response right now.

Include only what would actually help: something that would make a response feel
unexpectedly personal, or that would prevent a naive mistake. Omit what's obvious
from the topics alone.

Output:
- current_focus: the most specific thing you can say about what they're working on
- reading_velocity: fast, deep, or browsing
- memory_text: 3-6 sentences. No structure required. Write the thing a smart colleague
  would tell you before your first meeting with this person.
"""


class ConsolidationOutput(BaseModel):
    current_focus: str
    reading_velocity: Literal["fast", "deep", "browsing"]
    memory_text: str

ConsolidationOutput.model_rebuild()


VARIANTS = {
    "A": _VARIANT_A_PROMPT,
    "B": _VARIANT_B_PROMPT,
    "C": _VARIANT_C_PROMPT,
}


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_dataset(size: str) -> list[dict]:
    import importlib.util as _ilu
    if size == "pilot":
        _path = Path(__file__).parent / "dataset" / "pilot.py"
        _spec = _ilu.spec_from_file_location("pilot", _path)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.CASES
    msg = f"Unknown dataset size: {size}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run(
    variants: list[str] | None = None,
    dataset_size: str = "pilot",
    compare_to_baseline: bool = True,
    log_to_braintrust: bool = False,
) -> dict:
    """
    Run all variants on the dataset. Returns result dict; writes results/latest.json.
    """
    import os
    import sys

    # Add backend to path so we can import app.core.llm_client
    backend_path = Path(__file__).parent.parent.parent / "content-queue-backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    import importlib.util as _ilu

    def _load(name, path):
        spec = _ilu.spec_from_file_location(name, path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    _eval_dir = Path(__file__).parent
    _rubric = _load("rubric", _eval_dir / "rubric.py")
    _scorer_mod = _load("scorer", _eval_dir / "scorer.py")
    score_profile = _scorer_mod.score_profile
    PASS_THRESHOLD = _rubric.PASS_THRESHOLD
    WEIGHTS = _rubric.WEIGHTS

    from app.core.llm_client import llm_client, TASK_MEMORY_CONSOLIDATION

    active_variants = variants or list(VARIANTS.keys())
    cases = load_dataset(dataset_size)

    results: dict[str, dict] = {v: {"scores": {}, "per_case": []} for v in active_variants}

    for case in cases:
        key = case["key"]
        activity = case["activity_str"]
        print(f"\n[{key}]")

        for variant_name in active_variants:
            prompt_template = VARIANTS[variant_name]
            prompt = prompt_template.format(activity=activity)

            try:
                output: ConsolidationOutput = llm_client.structured_chat(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=ConsolidationOutput,
                    task=TASK_MEMORY_CONSOLIDATION,
                    max_tokens=1024,
                )
            except Exception as e:
                print(f"  [{variant_name}] LLM call failed: {e}")
                results[variant_name]["per_case"].append({
                    "case_key": key,
                    "error": str(e),
                    "passed": False,
                })
                continue

            case_score = score_profile(
                case_key=key,
                variant=variant_name,
                activity=activity,
                current_focus=output.current_focus,
                reading_velocity=output.reading_velocity,
                memory_text=output.memory_text,
                llm_client=llm_client,
            )

            results[variant_name]["per_case"].append({
                "case_key": key,
                "current_focus": output.current_focus,
                "reading_velocity": output.reading_velocity,
                "memory_text": output.memory_text,
                "dimension_scores": case_score.dimension_scores,
                "dimension_reasons": case_score.dimension_reasons,
                "weighted_total": case_score.weighted_total,
                "hard_fail": case_score.hard_fail,
                "passed": case_score.passed,
            })

            status = "PASS" if case_score.passed else ("HARD_FAIL" if case_score.hard_fail else "FAIL")
            print(f"  [{variant_name}] {status} {case_score.weighted_total:.3f}  focus={output.current_focus[:60]}")

    # Aggregate per variant
    for variant_name, vdata in results.items():
        per_case = vdata["per_case"]
        completed = [c for c in per_case if "weighted_total" in c]
        if not completed:
            vdata["scores"] = {"mean_weighted": 0.0, "pass_rate": 0.0, "hard_fail_rate": 0.0}
            continue

        mean_w = sum(c["weighted_total"] for c in completed) / len(completed)
        pass_rate = sum(1 for c in completed if c["passed"]) / len(completed)
        hard_fail_rate = sum(1 for c in completed if c.get("hard_fail")) / len(completed)

        # Per-dimension means
        dim_means = {}
        for dim in WEIGHTS:
            vals = [c["dimension_scores"].get(dim, 1) for c in completed if "dimension_scores" in c]
            dim_means[dim] = round(sum(vals) / len(vals), 3) if vals else 0.0

        vdata["scores"] = {
            "mean_weighted": round(mean_w, 4),
            "pass_rate": round(pass_rate, 4),
            "hard_fail_rate": round(hard_fail_rate, 4),
            "dimension_means": dim_means,
            "n": len(completed),
        }

    # Print summary table
    print("\n" + "=" * 70)
    print(f"{'Variant':<12} {'weighted':<10} {'pass_rate':<12} {'hard_fail':<12} N")
    print("-" * 70)
    for v in active_variants:
        s = results[v]["scores"]
        if s:
            print(
                f"{v:<12} {s.get('mean_weighted', 0):<10.4f} "
                f"{s.get('pass_rate', 0):<12.3f} "
                f"{s.get('hard_fail_rate', 0):<12.3f} "
                f"{s.get('n', 0)}"
            )

    # Per-case delta table (B−A, C−A)
    if compare_to_baseline and "A" in active_variants and len(active_variants) > 1:
        a_by_key = {c["case_key"]: c.get("weighted_total", 0.0) for c in results["A"]["per_case"]}
        print("\nPer-case deltas vs A:")
        print(f"{'case':<35}", end="")
        for v in active_variants:
            print(f" {v:<8}", end="")
        for v in active_variants:
            if v != "A":
                print(f" {v}−A{'':<5}", end="")
        print()
        print("-" * 70)
        for case in cases:
            key = case["key"]
            print(f"{key:<35}", end="")
            scores: dict[str, float] = {}
            for v in active_variants:
                by_key = {c["case_key"]: c.get("weighted_total") for c in results[v]["per_case"]}
                sc = by_key.get(key)
                scores[v] = sc or 0.0
                print(f" {(sc or 0.0):<8.3f}", end="")
            for v in active_variants:
                if v != "A":
                    delta = scores.get(v, 0.0) - scores.get("A", 0.0)
                    sign = "+" if delta >= 0 else ""
                    print(f" {sign}{delta:<8.3f}", end="")
            print()

    # Recommendation
    baseline_score = results.get("A", {}).get("scores", {}).get("mean_weighted", 0.0)
    best_variant = max(active_variants, key=lambda v: results[v]["scores"].get("mean_weighted", 0.0))
    best_score = results[best_variant]["scores"].get("mean_weighted", 0.0)
    improvement = best_score - baseline_score

    if improvement >= 0.05 and best_variant != "A":
        recommendation = "ship"
        reason = f"Variant {best_variant} improves weighted score by {improvement:.3f} over baseline"
    elif improvement < 0.02:
        recommendation = "dont_ship"
        reason = f"No variant improves baseline by ≥ 0.02 (best delta: {improvement:.3f})"
    else:
        recommendation = "investigate"
        reason = f"Variant {best_variant} shows delta {improvement:.3f} — marginal, check per-case"

    print(f"\nRecommendation: {recommendation.upper()} — {reason}")

    output_data = {
        "_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "_dataset_size": dataset_size,
        "_variants": active_variants,
        "variants": results,
        "recommendation": recommendation,
        "status": "pass" if recommendation == "ship" else "fail",
    }

    latest_path = RESULTS_DIR / "latest.json"
    latest_path.write_text(json.dumps(output_data, indent=2))
    print(f"\nResults written to {latest_path}")

    return output_data


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run memory consolidation prompt eval")
    parser.add_argument("--size", choices=["pilot", "full"], default="pilot")
    parser.add_argument("--variants", help="Comma-separated variant names (default: all)", default=None)
    parser.add_argument("--braintrust", action="store_true", help="Log to Braintrust (full runs only)")
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",")] if args.variants else None

    result = run(
        variants=variants,
        dataset_size=args.size,
        log_to_braintrust=args.braintrust,
    )

    sys.exit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
