from app.core.celery_app import celery_app
from app.models.content import ContentItem
from app.tasks.base import DatabaseTask
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@celery_app.task(base=DatabaseTask, bind=True)
def cleanup_old_deleted_items(self):
    """
    Permanently delete (hard delete) content items that have been soft-deleted
    for more than 7 days.

    This cleanup task:
    1. Finds items where deleted_at < now() - 7 days
    2. Hard deletes them from the database
    3. CASCADE foreign key will automatically delete associated highlights

    Runs daily via Celery beat schedule.
    """
    try:
        # Calculate cutoff date (7 days ago)
        cutoff_date = datetime.utcnow() - timedelta(days=7)

        # Find old deleted items
        old_deleted_items = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.deleted_at.isnot(None),
                ContentItem.deleted_at < cutoff_date,
            )
            .all()
        )

        if not old_deleted_items:
            logger.info("No old deleted items to clean up")
            return {"status": "completed", "deleted_count": 0}

        count = len(old_deleted_items)
        logger.info(f"Found {count} items deleted more than 7 days ago")

        # Hard delete them (CASCADE will handle highlights)
        for item in old_deleted_items:
            logger.info(
                f"Hard deleting: {item.id} - {item.title[:50]} (deleted {item.deleted_at})"
            )
            self.db.delete(item)

        self.db.commit()

        logger.info(
            f"Successfully hard deleted {count} old content items and their highlights"
        )

        return {
            "status": "completed",
            "deleted_count": count,
            "cutoff_date": cutoff_date.isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to cleanup old deleted items: {str(e)}")
        self.db.rollback()

        # Retry with exponential backoff
        if self.request.retries < 3:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

        return {"status": "failed", "error": str(e)}
