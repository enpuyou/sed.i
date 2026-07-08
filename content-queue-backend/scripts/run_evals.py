"""
Braintrust experiment runner for retrieval evals.

Runs retrieval scoring against the production DB and logs each case
as a scored Braintrust experiment row. Use this for trend tracking
across releases — not as a pytest gate.

Usage:
    EVAL=retrieval poetry run python scripts/run_evals.py

    # Optional overrides:
    DATABASE_URL=postgresql://... \\
    EVAL_USER_EMAIL=me@example.com \\
    EVAL=retrieval poetry run python scripts/run_evals.py

Requires BRAINTRUST_API_KEY in .env (or environment).
"""

from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path

# Add project root to path so app/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

PROD_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/content_queue"
)
EVAL_USER_EMAIL = os.environ.get("EVAL_USER_EMAIL", "enpu@example.com")
EVAL = os.environ.get("EVAL", "retrieval")


def _get_db_and_user():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from app.models.user import User

    engine = create_engine(PROD_DB_URL, poolclass=NullPool)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = db.query(User).filter(User.email == EVAL_USER_EMAIL).first()
    if user is None:
        raise SystemExit(f"User {EVAL_USER_EMAIL!r} not found in DB")
    return db, user


def _resolve_ids(expected_keys: list[str], article_ids: dict) -> list[str]:
    return [article_ids[k] for k in expected_keys if k in article_ids]


def run_retrieval_eval():
    import braintrust
    from app.core.hybrid_search import hybrid_search
    from tests.evals.retrieval_eval_dataset import ARTICLE_IDS, RETRIEVAL_EVAL_QUERIES
    from tests.evals.scoring import recall_at_k, mrr, ndcg_at_k

    api_key = os.environ.get("BRAINTRUST_API_KEY")
    if not api_key:
        raise SystemExit("BRAINTRUST_API_KEY not set")

    db, user = _get_db_and_user()

    experiment = braintrust.init(
        project="sedi",
        experiment_name="retrieval-baseline",
        api_key=api_key,
        open=False,
    )

    results_summary = []

    for case in RETRIEVAL_EVAL_QUERIES:
        key = case["key"]
        query = case["query"]
        expected_keys = case["expected_ids"]
        expected = _resolve_ids(expected_keys, ARTICLE_IDS)

        search_results = hybrid_search(
            query=query, user=user, db=db, limit=10, mode="full"
        )
        retrieved = [str(r["id"]) for r in search_results]

        r10 = recall_at_k(retrieved, expected, k=10)
        m = mrr(retrieved, expected)
        ndcg = ndcg_at_k(retrieved, expected, k=10)

        experiment.log(
            inputs={"query": query},
            output={
                "retrieved_ids": retrieved,
                "retrieved_titles": [r.get("title", "") for r in search_results],
            },
            expected={
                "relevant_ids": expected,
                "relevant_keys": expected_keys,
            },
            scores={
                "recall_at_10": r10,
                "mrr": m,
                "ndcg_at_10": ndcg,
            },
            metadata={
                "case_key": key,
                "multi_hop": case.get("multi_hop", False),
                "category": case.get("category", ""),
                "baseline_recall": case.get("current_recall"),
                "note": case.get("note", ""),
            },
        )

        results_summary.append(
            {
                "key": key,
                "multi_hop": case.get("multi_hop", False),
                "recall@10": round(r10, 3),
                "mrr": round(m, 3),
                "ndcg@10": round(ndcg, 3),
            }
        )

    experiment.close()
    db.close()

    print("\n=== Retrieval Eval Results ===")
    print(json.dumps(results_summary, indent=2))

    overall_recall = sum(r["recall@10"] for r in results_summary) / len(results_summary)
    overall_mrr = sum(r["mrr"] for r in results_summary) / len(results_summary)
    print(f"\nOverall: recall@10={overall_recall:.3f}  mrr={overall_mrr:.3f}")
    print(f"Logged {len(results_summary)} cases to Braintrust project 'sedi'")


EVALS = {
    "retrieval": run_retrieval_eval,
}

if __name__ == "__main__":
    if EVAL not in EVALS:
        raise SystemExit(f"Unknown EVAL={EVAL!r}. Available: {list(EVALS)}")
    EVALS[EVAL]()
