"""Read-only report: how many active content items are not stored in normalized
URL form, and how many would collide when migration a7d3e9c15b42 rewrites them.

Run before deploying the migration (it only SELECTs):

    DATABASE_URL=<url> python scripts/check_url_normalization.py

Collisions are two active rows of one user that normalize to the same URL; the
migration keeps one (already-normalized, else newest) and soft-deletes the rest.
"""

import os
import sys
from collections import defaultdict

import sqlalchemy as sa

from app.services.content import normalize_url


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL", file=sys.stderr)
        return 1
    url = url.replace("postgres://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, user_id, original_url FROM content_items "
                "WHERE deleted_at IS NULL"
            )
        ).fetchall()

    groups = defaultdict(list)
    for row in rows:
        groups[(row.user_id, normalize_url(row.original_url))].append(row)
    unnormalized = sum(
        1 for r in rows if normalize_url(r.original_url) != r.original_url
    )
    collisions = [g for g in groups.values() if len(g) > 1]

    print(f"active rows:                  {len(rows)}")
    print(f"not in normalized form:       {unnormalized}")
    print(f"collision groups:             {len(collisions)}")
    print(f"rows to soft-delete:          {sum(len(g) - 1 for g in collisions)}")
    for group in collisions[:10]:
        print("  collision:", [r.original_url[:70] for r in group])
    return 0


if __name__ == "__main__":
    sys.exit(main())
