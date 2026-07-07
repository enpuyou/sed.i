"""
Shared scoring functions for retrieval and synthesis evals.

All functions are pure (no I/O). LLM-as-judge functions require an llm_client.
"""

from __future__ import annotations

import math
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)

TASK_FAITHFULNESS_JUDGE = "faithfulness_judge"


# ── Retrieval metrics ─────────────────────────────────────────────────────────


def recall_at_k(
    retrieved_ids: list[str], relevant_ids: list[str], k: int = 10
) -> float:
    """Fraction of relevant items found in the top-k retrieved."""
    if not relevant_ids:
        return 1.0
    retrieved_top_k = set(retrieved_ids[:k])
    return len(set(relevant_ids) & retrieved_top_k) / len(relevant_ids)


def precision_at_k(
    retrieved_ids: list[str], relevant_ids: list[str], k: int = 10
) -> float:
    """Fraction of top-k retrieved items that are relevant."""
    if k == 0:
        return 0.0
    retrieved_top_k = retrieved_ids[:k]
    return sum(1 for id_ in retrieved_top_k if id_ in set(relevant_ids)) / k


def mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """Mean Reciprocal Rank — rank of the first relevant result."""
    relevant_set = set(relevant_ids)
    for rank, id_ in enumerate(retrieved_ids, start=1):
        if id_ in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int = 10) -> float:
    """
    Normalized Discounted Cumulative Gain at k.

    Binary relevance: 1 if in relevant_ids, 0 otherwise.
    Returns 0.0 if relevant_ids is empty.
    """
    if not relevant_ids:
        return 0.0

    relevant_set = set(relevant_ids)

    def dcg(ordered_ids: list[str]) -> float:
        return sum(
            (1.0 if id_ in relevant_set else 0.0) / math.log2(rank + 1)
            for rank, id_ in enumerate(ordered_ids[:k], start=1)
        )

    ideal = dcg(list(relevant_ids)[:k])
    if ideal == 0.0:
        return 0.0
    return dcg(retrieved_ids) / ideal


# ── LLM-as-judge scoring ──────────────────────────────────────────────────────

_FAITHFULNESS_SYSTEM = """\
You are an evaluation judge. You will be given a query, a set of source passages,
and a generated answer. Score the answer on faithfulness: does every factual claim
in the answer appear in the source passages?

Respond with JSON only:
{"score": <float 0.0–1.0>, "reason": "<one sentence>"}

0.0 = the answer contains major unsupported claims
1.0 = every claim is directly supported by the passages
"""

_FAITHFULNESS_USER = """\
Query: {query}

Source passages:
{passages}

Answer:
{answer}
"""


def faithfulness_score(
    query: str,
    passages: list[str],
    answer: str,
    llm_client: "LLMClient",
) -> dict:
    """
    LLM-as-judge faithfulness scorer.

    Returns {"score": float, "reason": str}.
    Falls back to {"score": 0.0, "reason": "<error>"} on failure.
    """
    import json

    passages_text = "\n\n---\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(passages))
    user_msg = _FAITHFULNESS_USER.format(
        query=query, passages=passages_text, answer=answer
    )

    try:
        result = llm_client.chat(
            messages=[
                {"role": "system", "content": _FAITHFULNESS_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            task=TASK_FAITHFULNESS_JUDGE,
            max_tokens=256,
            temperature=0.0,
        )
        data = json.loads(result.content)
        return {
            "score": float(data.get("score", 0.0)),
            "reason": str(data.get("reason", "")),
        }
    except Exception as e:
        logger.warning(f"faithfulness_score failed: {e}")
        return {"score": 0.0, "reason": f"judge error: {e}"}


def key_point_coverage(
    expected_points: list[str],
    answer: str,
    llm_client: "LLMClient",
) -> dict:
    """
    LLM-as-judge: what fraction of expected key points appear in the answer?

    Returns {"score": float, "covered": list[bool], "reason": str}.
    """
    import json

    points_list = "\n".join(f"{i+1}. {p}" for i, p in enumerate(expected_points))
    prompt = (
        f"For each key point below, does the answer cover it? "
        f'Respond with JSON only: {{"covered": [true/false per point], "reason": "<one sentence>"}}\n\n'
        f"Key points:\n{points_list}\n\n"
        f"Answer:\n{answer}"
    )

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            task=TASK_FAITHFULNESS_JUDGE,
            max_tokens=512,
            temperature=0.0,
        )
        data = json.loads(result.content)
        covered = [bool(v) for v in data.get("covered", [])]
        score = sum(covered) / len(covered) if covered else 0.0
        return {"score": score, "covered": covered, "reason": data.get("reason", "")}
    except Exception as e:
        logger.warning(f"key_point_coverage failed: {e}")
        return {
            "score": 0.0,
            "covered": [False] * len(expected_points),
            "reason": f"judge error: {e}",
        }
