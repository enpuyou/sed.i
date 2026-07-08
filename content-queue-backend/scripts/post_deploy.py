#!/usr/bin/env python3
"""
Post-deploy script — runs after alembic upgrade, before uvicorn starts.

Applies indexes that must be built outside a transaction (CONCURRENTLY).
Each statement is idempotent via IF NOT EXISTS.
"""

import os
import sys

import psycopg2


def get_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("post_deploy: DATABASE_URL not set, skipping", file=sys.stderr)
        sys.exit(0)
    # psycopg2 needs postgresql://, not postgres://
    return url.replace("postgres://", "postgresql://", 1)


CONCURRENT_INDEXES = [
    (
        "entities_embedding_hnsw",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS entities_embedding_hnsw
        ON entities
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """,
    ),
]


def main() -> None:
    dsn = get_dsn()
    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # CONCURRENTLY requires autocommit (no transaction)
    cur = conn.cursor()

    for name, sql in CONCURRENT_INDEXES:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s",
            (name,),
        )
        if cur.fetchone():
            print(f"post_deploy: index {name} already exists, skipping")
            continue
        print(f"post_deploy: building {name} ...", flush=True)
        cur.execute(sql)
        print(f"post_deploy: {name} done")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
