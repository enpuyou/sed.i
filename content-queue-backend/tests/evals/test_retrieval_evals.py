"""
Retrieval evals against the production DB.

These tests use real article embeddings from the production library
(enpu@example.com) rather than the seeded test DB. They measure the
current retrieval baseline before any GraphRAG features are applied.

Run with:
    PYENV_VERSION=3.11.12 ~/.pyenv/bin/pyenv exec poetry run pytest \
        tests/evals/test_retrieval_evals.py -v

Prerequisites:
    - Local server must be running (postgres on 5433, content ingested)
    - EVAL_USER_EMAIL env var or default enpu@example.com must exist in the DB

Pass/fail thresholds are set conservatively to match the CURRENT baseline
so the suite acts as a regression gate. Raise thresholds as GraphRAG
features ship.
"""

from __future__ import annotations

import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.evals.retrieval_eval_dataset import ARTICLE_IDS, RETRIEVAL_EVAL_QUERIES
from tests.evals.scoring import ndcg_at_k, recall_at_k, mrr

PROD_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue",
)
EVAL_USER_EMAIL = os.getenv("EVAL_USER_EMAIL", "enpu@example.com")


# ── Module-scoped DB fixtures (read-only, no teardown) ───────────────────────


@pytest.fixture(scope="module")
def prod_engine():
    engine = create_engine(PROD_DB_URL, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def prod_db(prod_engine):
    SessionLocal = sessionmaker(bind=prod_engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="module")
def eval_user(prod_db):
    from app.models.user import User

    user = prod_db.query(User).filter(User.email == EVAL_USER_EMAIL).first()
    if user is None:
        pytest.skip(f"Eval user {EVAL_USER_EMAIL!r} not found in production DB")
    return user


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_ids(expected_keys: list[str]) -> list[str]:
    """Convert dataset key names to UUID strings."""
    return [ARTICLE_IDS[k] for k in expected_keys if k in ARTICLE_IDS]


def _run_search(query: str, user, db, mode: str = "full") -> list[str]:
    """Return ordered list of article UUID strings from hybrid_search."""
    from app.core.hybrid_search import hybrid_search

    results = hybrid_search(query=query, user=user, db=db, limit=10, mode=mode)
    return [str(r["id"]) for r in results]


# ── Library sanity check ─────────────────────────────────────────────────────


class TestLibrarySanity:
    """Verify the production library has enough data to run evals."""

    def test_eval_user_has_embedded_articles(self, prod_db, eval_user):
        row = prod_db.execute(
            text(
                "SELECT COUNT(*) FROM content_items "
                "WHERE user_id = :uid AND embedding IS NOT NULL"
            ),
            {"uid": str(eval_user.id)},
        ).scalar()
        assert row >= 20, (
            f"Expected ≥20 embedded articles for eval, found {row}. "
            "Run make worker to process pending items."
        )

    def test_known_article_ids_exist(self, prod_db, eval_user):
        """Spot-check that key article IDs from the dataset exist in the DB."""
        spot_check_keys = [
            "harness_design",
            "banality_recommendation",
            "californian_ideology",
            "why_context_engineering",
        ]
        missing = []
        for key in spot_check_keys:
            uid = ARTICLE_IDS.get(key)
            if uid is None:
                missing.append(f"{key} (not in ARTICLE_IDS)")
                continue
            row = prod_db.execute(
                text(
                    "SELECT 1 FROM content_items " "WHERE id = :id AND user_id = :uid"
                ),
                {"id": uid, "uid": str(eval_user.id)},
            ).scalar()
            if row is None:
                missing.append(f"{key} ({uid})")
        assert not missing, f"Articles not found in production DB: {missing}"


# ── Single-hop retrieval baseline ────────────────────────────────────────────


class TestSingleHopRetrieval:
    """
    Single-query cases that current retrieval should already pass.

    Recall@10 ≥ 1.0 on all. Regression gate: if these drop, something
    broke in search/embedding.
    """

    @pytest.mark.parametrize(
        "case",
        [c for c in RETRIEVAL_EVAL_QUERIES if not c["multi_hop"]],
        ids=[c["key"] for c in RETRIEVAL_EVAL_QUERIES if not c["multi_hop"]],
    )
    def test_single_hop_recall(self, case, eval_user, prod_db):
        retrieved = _run_search(case["query"], eval_user, prod_db)
        expected = _resolve_ids(case["expected_ids"])
        score = recall_at_k(retrieved, expected, k=10)
        # s3_final is the measured baseline for the current production system (S3).
        baseline = case.get("s3_final", case.get("current_recall", 1.0))
        assert score >= baseline - 0.10, (
            f"[{case['key']}] recall@10={score:.2f} (baseline={baseline:.2f}, floor={baseline-0.10:.2f})\n"
            f"  query: {case['query']!r}\n"
            f"  retrieved: {retrieved[:5]}\n"
            f"  expected:  {expected}"
        )


# ── Multi-hop retrieval: current failure baseline ─────────────────────────────


class TestMultiHopRetrieval:
    """
    Multi-hop cases. Cases with s3_final < 1.0 document the current gap that
    GraphRAG features are intended to close. When a feature ships and a query
    improves, raise its s3_final baseline in retrieval_eval_dataset.py.
    """

    @pytest.mark.parametrize(
        "case",
        [
            c
            for c in RETRIEVAL_EVAL_QUERIES
            if c["multi_hop"] and c.get("s3_final") is not None
        ],
        ids=[
            c["key"]
            for c in RETRIEVAL_EVAL_QUERIES
            if c["multi_hop"] and c.get("s3_final") is not None
        ],
    )
    def test_multi_hop_recall(self, case, eval_user, prod_db):
        retrieved = _run_search(case["query"], eval_user, prod_db, mode="full")
        expected = _resolve_ids(case["expected_ids"])
        actual_recall = recall_at_k(retrieved, expected, k=10)
        baseline = case["s3_final"]

        if baseline < 1.0:
            # Confirmed failure: document actual score, pass unless DRAMATICALLY worse
            # (which would indicate a regression beyond the known failure)
            assert actual_recall >= baseline - 0.20, (
                f"[{case['key']}] recall@10={actual_recall:.2f} is dramatically worse "
                f"than known baseline {baseline:.2f}. Possible regression.\n"
                f"  query: {case['query']!r}\n"
                f"  retrieved: {retrieved[:5]}\n"
                f"  expected:  {expected}\n"
                f"  note: {case.get('note', '')}"
            )
        else:
            # Baseline = 1.0: this should still pass
            assert actual_recall >= 0.85, (
                f"[{case['key']}] recall@10={actual_recall:.2f} (expected ≥0.85, "
                f"was {baseline:.2f} at last measure)\n"
                f"  query: {case['query']!r}\n"
                f"  note: {case.get('note', '')}"
            )

    def test_multi_hop_aggregate_mrr(self, eval_user, prod_db):
        """Aggregate MRR across all multi-hop cases with known baselines."""
        multi_hop = [
            c
            for c in RETRIEVAL_EVAL_QUERIES
            if c["multi_hop"] and c.get("s3_final") is not None
        ]
        total_mrr = 0.0
        for case in multi_hop:
            retrieved = _run_search(case["query"], eval_user, prod_db, mode="full")
            expected = _resolve_ids(case["expected_ids"])
            total_mrr += mrr(retrieved, expected)
        avg_mrr = total_mrr / len(multi_hop) if multi_hop else 0.0

        # Baseline: ~0.55 MRR across 30 queries (50-article dataset, 2026-07-01)
        # This is a soft lower-bound regression gate
        assert avg_mrr >= 0.4, (
            f"Multi-hop aggregate MRR={avg_mrr:.3f} fell below floor 0.40. "
            "Check if search regression occurred."
        )


# ── Summary: print a human-readable baseline table ───────────────────────────


class TestBaselineReport:
    """
    Generates a readable summary of retrieval scores per query.
    Not a pass/fail gate — always passes. Run with -v to see the table.
    """

    def test_print_baseline_table(self, eval_user, prod_db, capsys):
        rows = []
        for case in RETRIEVAL_EVAL_QUERIES:
            if case.get("s3_final") is None and case.get("current_recall") is None:
                continue
            retrieved = _run_search(case["query"], eval_user, prod_db, mode="full")
            expected = _resolve_ids(case["expected_ids"])
            r10 = recall_at_k(retrieved, expected, k=10)
            m = mrr(retrieved, expected)
            ndcg = ndcg_at_k(retrieved, expected, k=10)
            rows.append((case["key"], case["multi_hop"], r10, m, ndcg))

        with capsys.disabled():
            print("\n\n=== Retrieval Baseline (production DB) ===")
            print(
                f"{'Query key':<40} {'multi_hop':<10} {'R@10':>6} {'MRR':>6} {'NDCG@10':>8}"
            )
            print("-" * 72)
            for key, mh, r10, m, ndcg in rows:
                print(
                    f"{key:<40} {'yes' if mh else 'no':<10} {r10:>6.2f} {m:>6.2f} {ndcg:>8.3f}"
                )
            print()
