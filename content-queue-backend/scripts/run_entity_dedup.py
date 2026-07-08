#!/usr/bin/env python3
"""
Run entity deduplication for one user or all users.

Finds near-duplicate entity nodes (cosine sim >= threshold), verifies each
candidate pair with gpt-4o-mini, then merges confirmed duplicates.

Usage:
    # Dry run — see what would be merged without changing anything
    poetry run python scripts/run_entity_dedup.py --dry-run

    # Run for all users
    poetry run python scripts/run_entity_dedup.py

    # Run for a specific user
    poetry run python scripts/run_entity_dedup.py --user-id <uuid>

    # Lower threshold to catch more variants (default 0.82)
    poetry run python scripts/run_entity_dedup.py --threshold 0.78

Cost: ~$0.001 per run at 1,500 entities. See app/tasks/entity_dedup.py for details.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deduplicate entity nodes in the knowledge graph"
    )
    parser.add_argument(
        "--user-id", help="Limit to a specific user UUID (default: all users)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.82,
        help="Cosine similarity floor for candidate pairs (default 0.82)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be merged without writing anything",
    )
    args = parser.parse_args()

    from app.core.database import SessionLocal
    from app.models.user import User
    from app.tasks.entity_dedup import deduplicate_entities

    db = SessionLocal()
    try:
        if args.user_id:
            user_ids = [args.user_id]
        else:
            rows = db.query(User.id).all()
            user_ids = [str(r.id) for r in rows]

        if not user_ids:
            print("No users found.")
            return 0

        scope = f"user {args.user_id}" if args.user_id else f"all {len(user_ids)} users"
        mode = "[DRY RUN] " if args.dry_run else ""
        print(f"{mode}Entity dedup for {scope} (threshold={args.threshold})")
        print()

        t0 = time.time()
        total_candidates = total_merged = total_skipped = 0

        for uid in user_ids:
            result = deduplicate_entities(
                user_id=uid,
                db=db,
                sim_threshold=args.threshold,
                dry_run=args.dry_run,
            )
            total_candidates += result["candidates"]
            total_merged += result["merged"]
            total_skipped += result["skipped"]

            if result["candidates"] > 0:
                print(
                    f"  user {uid}: {result['candidates']} candidates, "
                    f"{result['merged']} merged, {result['skipped']} skipped"
                )

        elapsed = time.time() - t0
        print()
        print(f"Done in {elapsed:.1f}s")
        print(f"  Candidate pairs found: {total_candidates}")
        print(f"  Merged:                {total_merged}")
        print(f"  Skipped (not same):    {total_skipped}")
        if args.dry_run:
            print()
            print("Dry run — no changes written. Remove --dry-run to apply.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
