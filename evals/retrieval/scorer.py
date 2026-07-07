"""
Retrieval eval — scoring functions.

Thin wrappers around the shared functions in
content-queue-backend/tests/evals/scoring.py.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2] / "content-queue-backend"))

from tests.evals.scoring import recall_at_k, mrr, ndcg_at_k

__all__ = ["recall_at_k", "mrr", "ndcg_at_k"]
