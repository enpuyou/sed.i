#!/usr/bin/env python3
"""
Backfill entity extraction for articles that have never been analyzed.

Dispatches analyze_article_task for every fully-ingested article (has embedding
+ full_text) with no entity mentions. Processes in batches of 50, polling until
all articles are covered.

Usage:
    # All users
    poetry run python scripts/backfill_entity_extraction.py

    # Single user
    poetry run python scripts/backfill_entity_extraction.py --user-id <uuid>

    # Dry run — show count without dispatching
    poetry run python scripts/backfill_entity_extraction.py --dry-run

Prerequisites:
    - Celery worker running: make worker
    - DATABASE_URL and OPENAI_API_KEY set in .env
    - Migrations applied: alembic upgrade head
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from app.core.database import SessionLocal


def count_pending(db, user_id: str | None) -> int:
    uid_filter = "AND ci.user_id = CAST(:uid AS uuid)" if user_id else ""
    params: dict = {}
    if user_id:
        params["uid"] = user_id

    row = db.execute(
        text(
            f"""
            SELECT COUNT(*) AS n
            FROM content_items ci
            WHERE ci.embedding IS NOT NULL
              AND ci.full_text IS NOT NULL
              AND ci.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM entity_mentions em
                  WHERE em.content_item_id = ci.id
              )
              {uid_filter}
        """
        ),
        params,
    ).fetchone()
    return row.n if row else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill entity extraction for existing articles"
    )
    parser.add_argument("--user-id", help="Limit to a specific user UUID")
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Articles per batch (default 50)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count pending articles without dispatching",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        pending = count_pending(db, args.user_id)
        scope = f"user {args.user_id}" if args.user_id else "all users"

        if pending == 0:
            print(f"No articles pending entity extraction for {scope}.")
            return 0

        print(f"Found {pending} articles pending entity extraction for {scope}.")

        if args.dry_run:
            print("Dry run — no tasks dispatched.")
            return 0

        print("Make sure the Celery worker is running: make worker")
        print()

        from app.tasks.article_analysis import backfill_entity_extraction

        total_dispatched = 0
        batches_needed = (pending + args.batch_size - 1) // args.batch_size

        for batch_num in range(1, batches_needed + 1):
            result = backfill_entity_extraction(
                user_id=args.user_id,
                batch_size=args.batch_size,
            )
            dispatched = result.get("dispatched", 0)
            total_dispatched += dispatched

            print(
                f"Batch {batch_num}/{batches_needed}: dispatched {dispatched} tasks  (total so far: {total_dispatched})"
            )

            if dispatched == 0:
                break

            # Brief pause between batches to avoid hammering the broker
            if batch_num < batches_needed:
                time.sleep(1)

        print(f"\nDone. {total_dispatched} analyze_article tasks queued.")
        print(
            "Entity embeddings will be triggered automatically after each article completes."
        )
        print("Monitor progress in Celery worker logs or Flower.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
