"""
G-Eval scorer for Library Research Brief.

Scores a ResearchBrief output against a pilot.py case using LLM-as-judge.
Each dimension is scored independently with chain-of-thought steps from rubric.py.
Final score = weighted sum; pass if >= PASS_THRESHOLD.

Hard fail conditions (override score):
  fabricated_citation   — key_sources contains item_ids not in retrieved set
  no_gap_report         — case has unanswerable_sub_qs but gaps list is empty
  zero_source_grounding — no article titles cited despite relevant articles existing
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[2] / "content-queue-backend"))

from rubric import (
    RUBRIC_DIMENSIONS,
    WEIGHTS,
    PASS_THRESHOLD,
    HARD_FAIL_CONDITIONS,
    SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def _judge_dimension(
    *,
    dim_key: str,
    dim_spec: dict,
    case: dict,
    brief: dict,
    client,
    model: str = "gpt-4o-mini",
) -> dict:
    """Score one dimension. Returns {score: int, reasoning: str}."""
    cot = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(dim_spec["cot_steps"]))
    anchors = "\n".join(f"  {k}: {v}" for k, v in dim_spec["anchors"].items())

    # Derive topic-level gap signals from index-based case fields
    expected = case.get("expected_sub_qs", [])
    cant_answer = [expected[i] for i in case.get("unanswerable_sub_qs", []) if i < len(expected)]
    can_answer = [expected[i] for i in case.get("answerable_from_library", []) if i < len(expected)]

    # Build intent section — only include fields the dimension actually uses
    if dim_key in ("question_fidelity", "useful_expansion"):
        case_meta = f"""\
Question: {case["question"]}
Category: {case["category"]}
Core intent: {case.get("core_intent", "")}
Legitimate expansions: {json.dumps(case.get("legitimate_expansions", []), indent=2)}
Off-limits expansions: {json.dumps(case.get("off_limits_expansions", []), indent=2)}
Ideal coverage: {case["ideal_coverage"]}"""
    elif dim_key == "gap_accuracy":
        case_meta = f"""\
Question: {case["question"]}
Category: {case["category"]}
Core intent: {case.get("core_intent", "")}
Ideal coverage: {case["ideal_coverage"]}
library_cant_answer (topics the library provably CANNOT address — true gaps):
{json.dumps(cant_answer, indent=2)}
library_can_answer (topics the library provably CAN address — false gap if reported as missing):
{json.dumps(can_answer, indent=2)}"""
    else:
        case_meta = f"""\
Question: {case["question"]}
Category: {case["category"]}
Core intent: {case.get("core_intent", "")}
Expected sub-questions: {json.dumps(expected, indent=2)}
Key article titles that must appear: {json.dumps(case["key_article_titles"], indent=2)}
Must not fabricate: {json.dumps(case.get("must_not_fabricate", []), indent=2)}
Ideal coverage: {case["ideal_coverage"]}"""

    prompt = f"""\
Dimension: {dim_key}
Description: {dim_spec["description"]}

Chain-of-thought steps to follow before scoring:
{cot}

Score anchors (1=bad, 3=acceptable, 5=ideal):
{anchors}

--- CASE METADATA ---
{case_meta}

--- BRIEF OUTPUT ---
{json.dumps(brief, indent=2)}

Follow the chain-of-thought steps above. Then output JSON with exactly these keys:
{{"reasoning": "<2-4 sentence explanation>", "score": <integer 1-5>}}
Output only the JSON object, no markdown fences."""

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=512,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
        return {"score": int(parsed["score"]), "reasoning": parsed.get("reasoning", "")}
    except Exception:
        # Best-effort: scan for a digit 1-5
        for ch in raw:
            if ch in "12345":
                return {"score": int(ch), "reasoning": raw}
        return {"score": 3, "reasoning": f"parse_error: {raw[:200]}"}


# ---------------------------------------------------------------------------
# Hard-fail detection
# ---------------------------------------------------------------------------

def _check_hard_fails(case: dict, brief: dict, retrieved_ids: set[str]) -> list[str]:
    """Return list of triggered hard-fail condition names."""
    triggered = []

    # fabricated_citation: any key_source item_id not in the retrieved set
    if retrieved_ids:
        all_cited = []
        for sqf in brief.get("sub_question_findings", []):
            for ks in sqf.get("key_sources", []):
                all_cited.append(ks.get("item_id", ""))
        fabricated = [cid for cid in all_cited if cid and cid not in retrieved_ids]
        if fabricated:
            triggered.append("fabricated_citation")

    # no_gap_report: case has unanswerable sub-qs but brief has no gaps
    unanswerable = case.get("unanswerable_sub_qs", [])
    if unanswerable and not brief.get("gaps"):
        triggered.append("no_gap_report")

    # zero_source_grounding: no article titles cited anywhere (excluding empty-library case)
    all_titles = []
    for sqf in brief.get("sub_question_findings", []):
        for ks in sqf.get("key_sources", []):
            if ks.get("title"):
                all_titles.append(ks["title"])
    if not all_titles and retrieved_ids:
        triggered.append("zero_source_grounding")

    return triggered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_brief(
    *,
    case: dict,
    brief: dict,
    retrieved_ids: set[str] | None = None,
    client,
    model: str = "gpt-4o-mini",
    skip_hard_fails: list[str] | None = None,
) -> dict:
    """
    Score a ResearchBrief against a pilot case.

    Returns:
    {
        "weighted_score": float,        # 0.0–1.0
        "pass": bool,
        "hard_fails": list[str],
        "dimensions": {
            "<dim>": {"score": int, "weighted": float, "reasoning": str}
        }
    }
    """
    if retrieved_ids is None:
        retrieved_ids = set()

    hard_fails = _check_hard_fails(case, brief, retrieved_ids)
    if skip_hard_fails:
        hard_fails = [h for h in hard_fails if h not in skip_hard_fails]

    dim_results: dict[str, Any] = {}
    for dim_key, dim_spec in RUBRIC_DIMENSIONS.items():
        result = _judge_dimension(
            dim_key=dim_key,
            dim_spec=dim_spec,
            case=case,
            brief=brief,
            client=client,
            model=model,
        )
        raw_score = result["score"]
        # Normalise from 1-5 scale to 0.0-1.0
        normalised = (raw_score - 1) / 4.0
        dim_results[dim_key] = {
            "score": raw_score,
            "weighted": normalised * WEIGHTS[dim_key],
            "reasoning": result["reasoning"],
        }

    weighted_score = sum(d["weighted"] for d in dim_results.values())
    passed = weighted_score >= PASS_THRESHOLD and not hard_fails

    return {
        "weighted_score": round(weighted_score, 4),
        "pass": passed,
        "hard_fails": hard_fails,
        "dimensions": dim_results,
    }
