"""
Tagging quality evals.

Measures whether generate_tags_with_llm() produces:
  1. Specific, non-generic tags (no "AI", "Technology", "Food")
  2. Domain-appropriate tags (food article → food tags, not tech tags)
  3. Correct tag count (4-6 tags per article)

Run with: pytest tests/evals/test_tagging_evals.py -v -s

Requires OPENAI_API_KEY. Skipped automatically when key is absent.
"""

import os

import pytest
from dotenv import load_dotenv
from .tagging_eval_dataset import TAGGING_EXAMPLES

load_dotenv()


pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") and not os.getenv("AWS_ACCESS_KEY_ID"),
    reason="No LLM credentials set — skipping tagging evals",
)


class TestTaggingQuality:
    """
    Evaluates tagging against the dataset using heuristic scoring.

    Heuristic scorer (no LLM call needed for basic evals):
      - Specificity: no single-word tags, no forbidden tags present
      - Count: between 4 and 6 tags
      - Domain match: at least one expected tag present (or a semantically close one)
    """

    def test_tagging_report(self):
        from app.tasks.tagging import generate_tags_with_llm

        results = []
        for example in TAGGING_EXAMPLES:
            predicted = generate_tags_with_llm(
                title=example["title"],
                description=example["description"],
                full_text=example["full_text"],
            )

            # Score this example
            score = _score_tags(
                predicted=predicted,
                expected=example["expected_tags"],
                forbidden=example["forbidden_tags"],
            )
            results.append(
                {
                    "key": example["key"],
                    "predicted": predicted,
                    "expected": example["expected_tags"],
                    "score": score,
                }
            )

        # Aggregate
        avg_score = sum(r["score"]["total"] for r in results) / len(results)
        specificity_pass = sum(1 for r in results if r["score"]["specificity"] >= 0.8)
        count_pass = sum(1 for r in results if r["score"]["count_ok"])
        forbidden_violations = sum(1 for r in results if r["score"]["forbidden_hit"])

        report = [
            "",
            "=" * 65,
            "EVAL: Tagging Quality",
            "=" * 65,
            f"Examples:           {len(results)}",
            f"Avg score:          {avg_score:.2f} / 1.00",
            f"Specificity ≥ 0.8:  {specificity_pass}/{len(results)}",
            f"Count in 4-6 range: {count_pass}/{len(results)}",
            f"Forbidden tag hits: {forbidden_violations}",
            "",
            "Per-example breakdown:",
        ]
        for r in results:
            s = r["score"]
            report.append(
                f"  [{r['key']:20s}] score={s['total']:.2f}  "
                f"specificity={s['specificity']:.2f}  "
                f"count_ok={s['count_ok']}  "
                f"forbidden={'YES' if s['forbidden_hit'] else 'no'}"
            )
            report.append(f"    predicted: {r['predicted']}")
        report.append("=" * 65)
        print("\n".join(report))

        # Thresholds
        assert avg_score >= 0.70, f"Average tagging score {avg_score:.2f} below 0.70"
        assert (
            forbidden_violations == 0
        ), f"{forbidden_violations} examples have forbidden (generic) tags"
        assert (
            count_pass >= len(results) * 0.80
        ), f"Only {count_pass}/{len(results)} examples have 4-6 tags"


def _score_tags(
    predicted: list[str],
    expected: list[str],
    forbidden: list[str],
) -> dict:
    """
    Heuristic scorer for a single tagging result.

    Returns a dict with component scores and a 0-1 total.
    """
    predicted_lower = [t.lower() for t in predicted]
    expected_lower = [t.lower() for t in expected]
    forbidden_lower = [t.lower() for t in forbidden]

    # Specificity: penalise single-word tags
    multi_word = sum(1 for t in predicted if len(t.split()) >= 2)
    specificity = multi_word / len(predicted) if predicted else 0

    # Count: 4-6 is ideal
    count_ok = 4 <= len(predicted) <= 6

    # Forbidden hits: any predicted tag that exactly matches a forbidden tag
    forbidden_hit = any(t in forbidden_lower for t in predicted_lower)

    # Coverage: how many expected tags appear in predicted (partial word match)
    coverage_hits = 0
    for exp in expected_lower:
        exp_words = set(exp.split())
        for pred in predicted_lower:
            pred_words = set(pred.split())
            # A hit if ≥ 1 content word overlaps (ignore stop words)
            overlap = exp_words & pred_words - {
                "the",
                "a",
                "an",
                "and",
                "of",
                "in",
                "to",
                "for",
            }
            if overlap:
                coverage_hits += 1
                break
    coverage = coverage_hits / len(expected) if expected else 0

    # Forbidden penalty: each forbidden tag found costs 0.3
    forbidden_penalty = 0.3 if forbidden_hit else 0

    total = (
        0.40 * specificity
        + 0.30 * coverage
        + 0.20 * (1.0 if count_ok else 0.0)
        - forbidden_penalty
    )
    total = max(0.0, min(1.0, total))

    return {
        "specificity": specificity,
        "coverage": coverage,
        "count_ok": count_ok,
        "forbidden_hit": forbidden_hit,
        "total": total,
    }
