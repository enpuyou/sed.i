#!/usr/bin/env python3
"""
HNSW vs sequential-scan performance benchmark.

Inserts N random 1536-dim unit vectors into a temporary pgvector table,
then times a nearest-neighbour query using both sequential scan (exact)
and HNSW (approximate). Prints wall-clock timings and a comparison table.

Usage:
    python scripts/benchmark_hnsw.py [--dsn <postgres-dsn>]

The script creates and drops a temporary table (hnsw_bench_tmp) within the
same transaction — it does NOT touch production tables. Run against a local
or dev database.

Defaults to DATABASE_URL from .env if present, otherwise falls back to the
CONTENT_QUEUE_DB env var, then the hard-coded fallback DSN.

Requirements: psycopg2-binary, numpy (both already in Poetry deps)
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

DIMS = 1536
N_VALUES = [1_000, 5_000, 10_000, 50_000]
QUERY_REPS = 10  # how many KNN queries to time at each N
K = 10  # neighbours per query
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_EF_SEARCH = 40


def _dsn() -> str:
    for key in ("DATABASE_URL", "CONTENT_QUEUE_DB"):
        val = os.getenv(key)
        if val:
            return val
    # Try loading from .env
    env_file = os.path.join(os.path.dirname(__file__), "..", "content-queue-backend", ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://localhost/content_queue"


def _unit_vectors(n: int, dims: int) -> "np.ndarray":
    import numpy as np

    rng = np.random.default_rng(42)
    vecs = rng.standard_normal((n, dims)).astype("float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return (vecs / norms).tolist()


def _time_query(cur, table: str, query_vec: str, k: int, use_index: bool) -> float:
    if not use_index:
        cur.execute("SET LOCAL enable_indexscan = off;")
        cur.execute("SET LOCAL enable_bitmapscan = off;")
    else:
        cur.execute("SET LOCAL enable_indexscan = on;")
        cur.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH};")
    t0 = time.perf_counter()
    cur.execute(
        f"SELECT id FROM {table} ORDER BY embedding <=> %s::vector LIMIT %s",
        (query_vec, k),
    )
    cur.fetchall()
    return time.perf_counter() - t0


def _fmt(secs: float) -> str:
    return f"{secs * 1000:.2f} ms"


def run(dsn: str) -> None:
    import psycopg2
    import numpy as np

    print(f"HNSW benchmark — {DIMS}D unit vectors, K={K}, {QUERY_REPS} queries each")
    print(f"DSN: {dsn[:40]}...")
    print()

    header = f"{'N':>8}  {'seq (p50)':>12}  {'hnsw (p50)':>12}  {'speedup':>10}"
    print(header)
    print("-" * len(header))

    for n in N_VALUES:
        vecs = _unit_vectors(n, DIMS)

        # Use a fresh connection per N to avoid contaminated plan caches
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        cur = conn.cursor()

        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TEMP TABLE hnsw_bench_tmp (
                    id SERIAL PRIMARY KEY,
                    embedding vector({DIMS})
                ) ON COMMIT DROP;
                """
            )

            # Bulk insert using COPY
            import io

            buf = io.StringIO()
            for vec in vecs:
                buf.write("[" + ",".join(f"{v:.6f}" for v in vec) + "]\n")
            buf.seek(0)
            cur.copy_expert(
                "COPY hnsw_bench_tmp (embedding) FROM STDIN",
                buf,
            )

            # Build HNSW index
            cur.execute(
                f"""
                CREATE INDEX ON hnsw_bench_tmp
                USING hnsw (embedding vector_cosine_ops)
                WITH (m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION});
                """
            )
            conn.commit()

            # Generate query vectors (different from training set)
            rng = np.random.default_rng(99)
            q_vecs = rng.standard_normal((QUERY_REPS, DIMS)).astype("float32")
            q_vecs = (q_vecs / np.linalg.norm(q_vecs, axis=1, keepdims=True)).tolist()

            seq_times = []
            hnsw_times = []
            for q in q_vecs:
                q_str = "[" + ",".join(f"{v:.6f}" for v in q) + "]"
                seq_times.append(_time_query(cur, "hnsw_bench_tmp", q_str, K, use_index=False))
                hnsw_times.append(_time_query(cur, "hnsw_bench_tmp", q_str, K, use_index=True))

            seq_p50 = statistics.median(seq_times)
            hnsw_p50 = statistics.median(hnsw_times)
            speedup = seq_p50 / hnsw_p50 if hnsw_p50 > 0 else float("inf")

            print(
                f"{n:>8,}  {_fmt(seq_p50):>12}  {_fmt(hnsw_p50):>12}  {speedup:>9.1f}x"
            )

        finally:
            conn.rollback()  # ON COMMIT DROP handles temp table
            cur.close()
            conn.close()

    print()
    print("Notes:")
    print(f"  HNSW params: m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION}, ef_search={HNSW_EF_SEARCH}")
    print(f"  Current prod entity count: ~300 — benefit is near-zero at this scale.")
    print("  Benefit materialises above ~5K entities (typically >1000 active users).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", help="PostgreSQL DSN (overrides DATABASE_URL env var)")
    args = parser.parse_args()

    try:
        import numpy  # noqa: F401
        import psycopg2  # noqa: F401
    except ImportError as e:
        print(f"Missing dependency: {e}", file=sys.stderr)
        print("Run: poetry run python scripts/benchmark_hnsw.py", file=sys.stderr)
        sys.exit(1)

    run(args.dsn or _dsn())


if __name__ == "__main__":
    main()
