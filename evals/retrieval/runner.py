"""
Retrieval eval runner — four variants, pilot or full dataset.

Variants (labelled per eval skill convention):
  Variant A — item-level embeddings only (keyword search + item cosine, no chunks)
  Variant B — + chunk embeddings (keyword + chunk-or-item semantic, RRF fused)
  Variant C — + entity lane, rank-based RRF (no score passthrough)
  Variant D — + entity lane with IDF-dampened score passthrough (current production)

Usage:
    # From project root
    cd content-queue-backend
    PYENV_VERSION=3.11.12 pyenv exec poetry run python ../evals/retrieval/runner.py
    PYENV_VERSION=3.11.12 pyenv exec poetry run python ../evals/retrieval/runner.py --size pilot
    PYENV_VERSION=3.11.12 pyenv exec poetry run python ../evals/retrieval/runner.py --size full

Prerequisites:
    - Local postgres on port 5433
    - EVAL_USER_EMAIL env var (default: enpu@example.com) exists in DB
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Run from content-queue-backend/ so app imports work
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "content-queue-backend"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from evals.retrieval.scorer import recall_at_k, mrr, ndcg_at_k

PROD_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/content_queue",
)
EVAL_USER_EMAIL = os.getenv("EVAL_USER_EMAIL", "enpu@example.com")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Variant implementations ───────────────────────────────────────────────────

def _variant_a_item_only(query: str, user, db, limit: int = 10) -> list[str]:
    """
    Variant A: keyword search + item-level cosine similarity only.
    Does not use content_chunks at all — pure item embedding baseline.
    """
    from app.core.config import settings
    from app.core.embedding_cache import get_or_create_query_embedding, call_embed
    from app.core.hybrid_search import keyword_search

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        r.ping()
        query_embedding = get_or_create_query_embedding(query, redis_client=r)
    except Exception:
        query_embedding = call_embed(query)

    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

    sem_rows = db.execute(
        text("""
            SELECT ci.id::text
            FROM content_items ci
            WHERE ci.user_id = :uid
              AND ci.deleted_at IS NULL
              AND ci.embedding IS NOT NULL
              AND ci.title IS NOT NULL AND ci.title != ''
            ORDER BY ci.embedding <=> CAST(:q AS vector)
            LIMIT :lim
        """),
        {"uid": str(user.id), "q": embedding_str, "lim": limit * 3},
    ).fetchall()
    sem_ids = [str(r[0]) for r in sem_rows]

    kw_results = keyword_search(query=query, user=user, db=db, limit=limit * 3)
    kw_ids = [r["id"] for r in kw_results]

    scores: dict[str, float] = defaultdict(float)
    for rank, id_ in enumerate(kw_ids, 1):
        scores[id_] += 1.0 / (60 + rank)
    for rank, id_ in enumerate(sem_ids, 1):
        scores[id_] += 1.0 / (60 + rank)

    return sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]


def _variant_b_chunks(query: str, user, db, limit: int = 10) -> list[str]:
    """
    Variant B: keyword + chunk-aware semantic (chunks where available, item
    fallback for articles without chunks), RRF fused. No entity lane.
    """
    from app.core.hybrid_search import keyword_search, _semantic_search

    fetch_limit = limit * 3
    kw_results = keyword_search(query=query, user=user, db=db, limit=fetch_limit)
    sem_results = _semantic_search(query, user, db, fetch_limit)

    scores: dict[str, float] = defaultdict(float)
    for rank, r in enumerate(kw_results, 1):
        scores[r["id"]] += 1.0 / (60 + rank)
    for rank, r in enumerate(sem_results, 1):
        scores[r["id"]] += 1.0 / (60 + rank)

    return sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]


def _variant_c_entity_rank_rrf(query: str, user, db, limit: int = 10) -> list[str]:
    """
    Variant C: Variant B + entity lane using rank-based RRF only.
    Entity results are treated as a third ranked list; raw entity scores discarded.
    """
    from app.core.hybrid_search import keyword_search, _semantic_search, _entity_search

    fetch_limit = limit * 3
    kw_results = keyword_search(query=query, user=user, db=db, limit=fetch_limit)
    sem_results = _semantic_search(query, user, db, fetch_limit)
    entity_results = _entity_search(query, user, db, fetch_limit)

    scores: dict[str, float] = defaultdict(float)
    for rank, r in enumerate(kw_results, 1):
        scores[r["id"]] += 1.0 / (60 + rank)
    for rank, r in enumerate(sem_results, 1):
        scores[r["id"]] += 1.0 / (60 + rank)
    for rank, r in enumerate(entity_results, 1):
        scores[r["id"]] += 1.0 / (60 + rank)

    return sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]


def _variant_d_entity_passthrough(query: str, user, db, limit: int = 10) -> list[str]:
    """
    Variant D: current production system.
    Variant B + entity lane with IDF-dampened score × 0.025 added to RRF sum.
    Equivalent to hybrid_search(mode="full").
    """
    from app.core.hybrid_search import hybrid_search
    results = hybrid_search(query=query, user=user, db=db, limit=limit, mode="full")
    return [str(r["id"]) for r in results]


VARIANTS = {
    "A": ("item only", _variant_a_item_only),
    "B": ("+ chunks", _variant_b_chunks),
    "C": ("+ entity rank-RRF", _variant_c_entity_rank_rrf),
    "D": ("+ entity passthrough", _variant_d_entity_passthrough),
}


# ── Runner ────────────────────────────────────────────────────────────────────

def run(
    variants: list[str] | None = None,
    dataset_size: str = "pilot",
    compare_to_baseline: bool = True,
    log_to_braintrust: bool = False,
) -> dict:
    """
    Standard runner contract per eval skill spec.

    Returns {
        "variants": {name: {"scores": {...}, "per_case": [...]}},
        "regressions": [...],
        "recommendation": "ship" | "dont_ship" | "investigate",
        "status": "pass" | "fail"
    }
    Writes results/latest.json.
    """
    if variants is None:
        variants = list(VARIANTS.keys())

    if dataset_size == "pilot":
        from evals.retrieval.dataset.pilot import PILOT_QUERIES as queries
    else:
        from evals.retrieval.dataset.full import FULL_QUERIES as queries

    engine = create_engine(PROD_DB_URL, poolclass=NullPool)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = SessionLocal()

    from app.models.user import User
    user = db.query(User).filter(User.email == EVAL_USER_EMAIL).first()
    if user is None:
        print(f"ERROR: User {EVAL_USER_EMAIL!r} not found in DB.")
        sys.exit(1)

    print(f"\nRetrieval eval — {dataset_size} ({len(queries)} queries), variants {variants}")
    print(f"User: {EVAL_USER_EMAIL}\n")

    variant_results: dict[str, dict] = {v: {"scores": {}, "per_case": []} for v in variants}
    per_query_rows: list[dict] = []

    for case in queries:
        key = case["key"]
        query_text = case["query"]
        expected = case["expected_ids"]
        if isinstance(expected[0], str) and len(expected[0]) > 36:
            # already UUIDs
            expected_ids = expected
        else:
            from tests.evals.retrieval_eval_dataset import ARTICLE_IDS
            expected_ids = [ARTICLE_IDS.get(k, k) for k in expected]

        row: dict = {"key": key, "tier": case.get("tier"), "query": query_text}

        for v in variants:
            label, fn = VARIANTS[v]
            try:
                retrieved = fn(query_text, user, db, limit=10)
                r10 = recall_at_k(retrieved, expected_ids, k=10)
                m = mrr(retrieved, expected_ids)
                n = ndcg_at_k(retrieved, expected_ids, k=10)
            except Exception as e:
                print(f"  [{key}] ERROR variant {v}: {e}")
                r10, m, n = 0.0, 0.0, 0.0

            row[f"variant_{v}_r10"] = round(r10, 4)
            variant_results[v]["per_case"].append({
                "key": key, "recall_at_10": r10, "mrr": m, "ndcg_at_10": n,
            })

        scores_str = "  ".join(
            f"{v}={row.get(f'variant_{v}_r10', 0):.2f}" for v in variants
        )
        print(f"  T{case.get('tier','?')} {key:<42} {scores_str}")
        per_query_rows.append(row)

    db.close()
    engine.dispose()

    # Aggregate per variant
    for v in variants:
        cases = variant_results[v]["per_case"]
        if cases:
            variant_results[v]["scores"] = {
                "recall_at_10": round(sum(c["recall_at_10"] for c in cases) / len(cases), 4),
                "mrr": round(sum(c["mrr"] for c in cases) / len(cases), 4),
                "ndcg_at_10": round(sum(c["ndcg_at_10"] for c in cases) / len(cases), 4),
                "n": len(cases),
            }

    # Regressions vs stored baselines
    regressions = []
    if compare_to_baseline:
        baselines_path = Path(__file__).parent / "baselines.json"
        if baselines_path.exists():
            with open(baselines_path) as f:
                baselines = json.load(f)
            key_map = {"A": "variant_A_item_only", "B": "variant_B_chunks",
                       "C": "variant_C_entity_rank_rrf", "D": "variant_D_entity_passthrough"}
            for v in variants:
                bkey = key_map.get(v)
                if bkey and bkey in baselines:
                    for metric in ("recall_at_10", "mrr", "ndcg_at_10"):
                        actual = variant_results[v]["scores"].get(metric, 0)
                        baseline_val = baselines[bkey].get(metric, 0)
                        tolerance = 0.02
                        if actual < baseline_val - tolerance:
                            regressions.append({
                                "variant": v, "metric": metric,
                                "actual": actual, "baseline": baseline_val,
                                "delta": round(actual - baseline_val, 4),
                            })

    # Decision
    if regressions:
        recommendation = "investigate"
        status = "fail"
    else:
        recommendation = "ship"
        status = "pass"

    output = {
        "_run_at": datetime.utcnow().isoformat() + "Z",
        "_dataset_size": dataset_size,
        "_variants": variants,
        "variants": variant_results,
        "regressions": regressions,
        "recommendation": recommendation,
        "status": status,
        "per_query": per_query_rows,
    }

    latest_path = RESULTS_DIR / "latest.json"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    ts_path = RESULTS_DIR / f"{dataset_size}_{ts}.json"
    for p in (latest_path, ts_path):
        with open(p, "w") as f:
            json.dump(output, f, indent=2)

    return output


def _print_summary(output: dict) -> None:
    print("\n" + "=" * 70)
    print("AGGREGATE")
    print("=" * 70)
    print(f"{'Variant':<28} {'R@10':>7} {'MRR':>7} {'NDCG@10':>9} {'N':>5}")
    print("-" * 56)
    for v, label_fn in VARIANTS.items():
        label = label_fn[0]
        s = output["variants"].get(v, {}).get("scores", {})
        if s:
            print(f"Variant {v} ({label:<18}) {s['recall_at_10']:>7.4f} {s['mrr']:>7.4f} {s['ndcg_at_10']:>9.4f} {s['n']:>5}")

    regressions = output.get("regressions", [])
    if regressions:
        print(f"\nREGRESSIONS ({len(regressions)}):")
        for r in regressions:
            print(f"  Variant {r['variant']} {r['metric']}: {r['actual']:.4f} vs baseline {r['baseline']:.4f} ({r['delta']:+.4f})")
    else:
        print("\nNo regressions vs stored baselines.")

    print(f"\nRecommendation: {output['recommendation'].upper()}")
    print(f"Status:         {output['status'].upper()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=["pilot", "full"], default="pilot")
    parser.add_argument("--variants", nargs="+", choices=list(VARIANTS.keys()), default=None)
    parser.add_argument("--braintrust", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    output = run(
        variants=args.variants,
        dataset_size=args.size,
        log_to_braintrust=args.braintrust,
    )
    _print_summary(output)
    print(f"\nElapsed: {time.time() - t0:.1f}s")
    print(f"Results: evals/retrieval/results/latest.json")

    sys.exit(0 if output["status"] == "pass" else 1)
