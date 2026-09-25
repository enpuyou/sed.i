"""
Scorer for memory consolidation eval.

Calls the G-Eval rubric judge for each dimension, returns weighted total.
Uses the shared LLM client so the judge model matches the production model.
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Judge calls (5 per case-variant, gpt-4o) routinely blow through OpenAI's
# 30K TPM org limit on a full eval run. The LLM client's own built-in retry
# (2 attempts) fires within ~1s of the same rate-limit window and both fail
# identically — see results from the 2026-09-25 run, where 21/150 dimension
# scores silently landed as the score_profile() error fallback (score=1, the
# rubric floor) instead of a real judgment, corrupting the aggregate without
# any visible signal in the summary table. Sleeping past the window before
# retrying at this layer (separate from the LLM client's own retry) fixes it.
_RATE_LIMIT_RETRY_SLEEP_S = 20
_RATE_LIMIT_MAX_ATTEMPTS = 3


class DimensionScore(BaseModel):
    score: int  # 1–5
    reason: str


class CaseScore(BaseModel):
    case_key: str
    variant: str
    dimension_scores: dict[str, int]
    dimension_reasons: dict[str, str]
    weighted_total: float
    hard_fail: bool
    passed: bool


def score_profile(
    *,
    case_key: str,
    variant: str,
    activity: str,
    current_focus: str,
    reading_velocity: str,
    memory_text: str,
    llm_client,  # app.core.llm_client.LLMClient
) -> CaseScore:
    """Score a single profile output on all 5 rubric dimensions."""
    import importlib.util, pathlib
    _rubric_path = pathlib.Path(__file__).parent / "rubric.py"
    _spec = importlib.util.spec_from_file_location("rubric", _rubric_path)
    _rubric = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_rubric)
    WEIGHTS = _rubric.WEIGHTS
    build_judge_prompt = _rubric.build_judge_prompt
    weighted_score = _rubric.weighted_score
    is_hard_fail = _rubric.is_hard_fail

    dimension_scores: dict[str, int] = {}
    dimension_reasons: dict[str, str] = {}

    for dimension in WEIGHTS:
        system_prompt, user_prompt = build_judge_prompt(
            activity=activity,
            current_focus=current_focus,
            reading_velocity=reading_velocity,
            memory_text=memory_text,
            dimension=dimension,
        )
        result: DimensionScore | None = None
        last_error: Exception | None = None
        for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                result = llm_client.structured_chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=DimensionScore,
                    task="synthesis",  # reuse synthesis model slot; no separate judge config needed
                    max_tokens=256,
                )
                break
            except Exception as e:
                last_error = e
                is_rate_limit = "rate_limit" in str(e).lower() or "429" in str(e)
                if is_rate_limit and attempt < _RATE_LIMIT_MAX_ATTEMPTS - 1:
                    logger.warning(
                        f"Rate limited on {case_key}/{variant}/{dimension} "
                        f"(attempt {attempt + 1}/{_RATE_LIMIT_MAX_ATTEMPTS}), "
                        f"sleeping {_RATE_LIMIT_RETRY_SLEEP_S}s before retry"
                    )
                    time.sleep(_RATE_LIMIT_RETRY_SLEEP_S)
                    continue
                break

        if result is not None:
            dimension_scores[dimension] = result.score
            dimension_reasons[dimension] = result.reason
        else:
            logger.warning(f"Judge failed on {case_key}/{variant}/{dimension}: {last_error}")
            dimension_scores[dimension] = 1
            dimension_reasons[dimension] = f"ERROR: {last_error}"

    total = weighted_score(dimension_scores)
    fail = is_hard_fail(dimension_scores)

    PASS_THRESHOLD = _rubric.PASS_THRESHOLD

    return CaseScore(
        case_key=case_key,
        variant=variant,
        dimension_scores=dimension_scores,
        dimension_reasons=dimension_reasons,
        weighted_total=total,
        hard_fail=fail,
        passed=(not fail) and (total >= PASS_THRESHOLD),
    )
