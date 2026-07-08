"""
Retrieval eval — full dataset (45 queries, 6 tiers).

Re-exports from the canonical dataset in tests/evals/retrieval_eval_dataset.py.
That file is the source of truth for article IDs and per-query scores.
All scores were measured 2026-07-06 via evals/retrieval/runner.py.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3] / "content-queue-backend"))
from tests.evals.retrieval_eval_dataset import ARTICLE_IDS, RETRIEVAL_EVAL_QUERIES as FULL_QUERIES

__all__ = ["ARTICLE_IDS", "FULL_QUERIES"]
