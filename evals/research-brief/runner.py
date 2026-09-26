"""
Research-brief eval runner.

Three variants:
  A — synthesize_topic quick (baseline, single-pass, synchronous)
  B — synthesize_topic deep (full multi-agent research run, polled to completion)
  C — variant B with memory seeding (current_focus set before run)

Usage (from project root):
    cd content-queue-backend
    PYENV_VERSION=3.11.12 ~/.pyenv/bin/pyenv exec poetry run python \\
        ../evals/research-brief/runner.py --variants A,B --size pilot

Prerequisites:
  - Local postgres on port 5433 (or DATABASE_URL env override)
  - EVAL_USER_EMAIL env var set to a user in the DB
  - OPENAI_API_KEY in environment (used for LLM judge)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Make app importable
sys.path.insert(0, str(Path(__file__).parents[2] / "content-queue-backend"))

from openai import OpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from dataset.pilot import CASES as PILOT_CASES
from scorer import score_brief

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue",
)
EVAL_USER_EMAIL = os.getenv("EVAL_USER_EMAIL", "")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 300  # 5 min max per deep run


# ---------------------------------------------------------------------------
# DB / user setup
# ---------------------------------------------------------------------------

def _make_session():
    engine = create_engine(DB_URL, poolclass=NullPool)
    Session = sessionmaker(bind=engine)
    return Session()


def _get_user(db, email: str):
    from app.models.user import User
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise RuntimeError(
            f"User '{email}' not found. Set EVAL_USER_EMAIL to a valid DB user."
        )
    return user


# ---------------------------------------------------------------------------
# Variant A — quick synthesis
# ---------------------------------------------------------------------------

def _run_variant_a(question: str, user, db) -> dict:
    from app.mcp.tools.synthesis import synthesize_topic
    return synthesize_topic(topic=question, depth="quick", user=user, db=db)


# ---------------------------------------------------------------------------
# Variant B — deep research run (polled)
# ---------------------------------------------------------------------------

def _poll_run(run_id: str, db) -> dict:
    """Poll a ResearchRun until terminal. Returns the run row's result dict."""
    from app.models.research import ResearchRun

    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        db.expire_all()
        run = db.query(ResearchRun).filter(ResearchRun.id == run_id).first()
        if run is None:
            raise RuntimeError(f"Run {run_id} disappeared from DB")
        if run.status in ("done", "partial", "failed"):
            return {
                "status": run.status,
                "result": run.result,
                "error": run.error,
                "item_ids_retrieved": run.item_ids_retrieved or [],
            }
        time.sleep(POLL_INTERVAL_S)

    raise TimeoutError(f"Run {run_id} did not complete within {POLL_TIMEOUT_S}s")


def _run_variant_b(question: str, user, db) -> dict:
    from app.mcp.tools.synthesis import synthesize_topic

    enqueue_result = synthesize_topic(topic=question, depth="deep", user=user, db=db)
    if "error" in enqueue_result:
        return {"error": enqueue_result["error"], "result": None, "item_ids_retrieved": []}

    run_id = enqueue_result["run_id"]
    return _poll_run(run_id, db)


# ---------------------------------------------------------------------------
# Variant C — deep with memory seeding
# ---------------------------------------------------------------------------

def _run_variant_c(question: str, user, db) -> dict:
    from app.models.memory import UserProfile

    # Seed current_focus from the question (simulate a focused user)
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    original_focus = profile.current_focus if profile else None
    if profile:
        profile.current_focus = question[:120]
        db.commit()
    else:
        db.add(UserProfile(user_id=user.id, current_focus=question[:120]))
        db.commit()

    try:
        result = _run_variant_b(question, user, db)
    finally:
        # Restore original focus
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if profile:
            profile.current_focus = original_focus
            db.commit()

    return result


# ---------------------------------------------------------------------------
# Run all variants on one case
# ---------------------------------------------------------------------------

_VARIANT_FN = {
    "A": _run_variant_a,
    "B": _run_variant_b,
    "C": _run_variant_c,
}


def _extract_brief(raw_output: dict) -> dict | None:
    """
    Both quick and deep variants return different shapes.
    Quick: direct SynthesisResponse dict (has 'summary', 'perspectives').
    Deep: {'status': ..., 'result': <ResearchBrief dict or None>}.
    """
    if "result" in raw_output and raw_output.get("result") is not None:
        return raw_output["result"]
    if "summary" in raw_output:
        return raw_output
    return None


def run_case(
    case: dict,
    variants: list[str],
    user,
    db,
    judge_client: OpenAI,
    judge_model: str = "gpt-4o",
) -> dict:
    """Run all requested variants on a single case. Returns per-variant scores."""
    results = {}
    for variant in variants:
        fn = _VARIANT_FN[variant]
        t0 = time.monotonic()
        try:
            raw = fn(case["question"], user, db)
            elapsed = time.monotonic() - t0
            brief = _extract_brief(raw)
            if brief is None:
                results[variant] = {
                    "error": raw.get("error", "no_result"),
                    "elapsed_s": round(elapsed, 1),
                    "score": None,
                }
                continue

            retrieved_ids = set(raw.get("item_ids_retrieved", []))
            # Variant A uses SynthesisResponse (no gap field, no item_id citations)
            # — skip gap/grounding hard fails that don't apply to its schema
            skip_hf = ["no_gap_report", "zero_source_grounding"] if variant == "A" else None
            score_result = score_brief(
                case=case,
                brief=brief,
                retrieved_ids=retrieved_ids,
                client=judge_client,
                model=judge_model,
                skip_hard_fails=skip_hf,
            )
            results[variant] = {
                "elapsed_s": round(elapsed, 1),
                **score_result,
            }
        except Exception as exc:
            elapsed = time.monotonic() - t0
            results[variant] = {
                "error": str(exc),
                "elapsed_s": round(elapsed, 1),
                "score": None,
            }

    return results


# ---------------------------------------------------------------------------
# Aggregate + print
# ---------------------------------------------------------------------------

def _print_aggregate(all_results: dict, variants: list[str]) -> None:
    print("\n=== AGGREGATE ===")
    for v in variants:
        scores = [
            r[v]["weighted_score"]
            for r in all_results.values()
            if v in r and r[v].get("weighted_score") is not None
        ]
        passes = sum(
            1 for r in all_results.values()
            if v in r and r[v].get("pass")
        )
        n = len(scores)
        avg = sum(scores) / n if n else 0.0
        print(f"  Variant {v}: avg_score={avg:.3f}  pass={passes}/{n}")


def _print_per_case(all_results: dict, variants: list[str]) -> None:
    col_w = 12
    header = f"{'case_key':<28}" + "".join(f"{v:>{col_w}}" for v in variants)
    if len(variants) > 1:
        for i in range(1, len(variants)):
            header += f"  {variants[i]}-{variants[0]:>{col_w-2}}"
    print("\n=== PER-CASE ===")
    print(header)
    for key, case_results in all_results.items():
        row = f"{key:<28}"
        scores = {}
        for v in variants:
            s = case_results.get(v, {}).get("weighted_score")
            scores[v] = s
            row += f"{(f'{s:.3f}' if s is not None else 'ERR'):>{col_w}}"
        if len(variants) > 1:
            base = scores.get(variants[0])
            for i in range(1, len(variants)):
                cmp = scores.get(variants[i])
                if base is not None and cmp is not None:
                    delta = cmp - base
                    sign = "+" if delta >= 0 else ""
                    row += f"  {sign}{delta:.3f}".rjust(col_w)
                else:
                    row += "   N/A".rjust(col_w)
        print(row)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    variants: list[str] | None = None,
    dataset_size: str = "pilot",
    compare_to_baseline: bool = True,
    log_to_braintrust: bool = False,
) -> dict:
    if variants is None:
        variants = ["A", "B"]

    cases = PILOT_CASES  # pilot only for now; full.py would be FULL_CASES

    judge_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    db = _make_session()

    if not EVAL_USER_EMAIL:
        raise RuntimeError("Set EVAL_USER_EMAIL to the email of the eval user in the DB.")

    user = _get_user(db, EVAL_USER_EMAIL)

    all_results: dict[str, dict] = {}
    for case in cases:
        print(f"  [{case['key']}] ...", end=" ", flush=True)
        case_results = run_case(
            case=case,
            variants=variants,
            user=user,
            db=db,
            judge_client=judge_client,
        )
        all_results[case["key"]] = case_results
        summary = "  ".join(
            f"{v}={case_results[v].get('weighted_score', 'ERR'):.3f}"
            if case_results[v].get("weighted_score") is not None
            else f"{v}=ERR"
            for v in variants
        )
        print(summary)

    _print_aggregate(all_results, variants)
    _print_per_case(all_results, variants)

    # Write results
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    out = {
        "timestamp": ts,
        "variants": variants,
        "dataset_size": dataset_size,
        "cases": all_results,
    }
    latest_path = RESULTS_DIR / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nResults written to {latest_path}")

    # Recommendation
    avg_scores = {}
    for v in variants:
        scores = [
            r[v]["weighted_score"]
            for r in all_results.values()
            if v in r and r[v].get("weighted_score") is not None
        ]
        avg_scores[v] = sum(scores) / len(scores) if scores else 0.0

    best = max(avg_scores, key=lambda v: avg_scores[v])
    baseline_score = avg_scores.get(variants[0], 0.0)
    best_score = avg_scores[best]

    if best != variants[0] and best_score - baseline_score >= 0.02:
        recommendation = f"ship variant {best}"
    elif best_score >= 0.70:
        recommendation = "investigate"
    else:
        recommendation = "dont_ship"

    status = "pass" if best_score >= 0.70 else "fail"
    out["recommendation"] = recommendation
    out["status"] = status
    with open(latest_path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nRecommendation: {recommendation}  (status: {status})")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="A,B", help="Comma-separated variant letters")
    parser.add_argument("--size", default="pilot", choices=["pilot", "full"])
    parser.add_argument("--braintrust", action="store_true")
    args = parser.parse_args()

    variants = [v.strip().upper() for v in args.variants.split(",")]
    run(variants=variants, dataset_size=args.size, log_to_braintrust=args.braintrust)
