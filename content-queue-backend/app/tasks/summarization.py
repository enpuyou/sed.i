"""
Celery task: generate_summary.

Calls OpenAI to produce a short summary of a ContentItem's full_text.
Stores result in ContentItem.summary. No-op if OPENAI_API_KEY is unset
or full_text is empty.

Dispatch: generate_summary.delay(item_id)
"""

from celery import Task
from sqlalchemy.orm import Session
from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.content import ContentItem
from app.core.config import settings
from app.tasks.base import html_to_plain
from uuid import UUID
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)


class DatabaseTask(Task):
    """Base task with database session"""

    _db: Session = None

    def after_return(self, *args, **kwargs):
        if self._db is not None:
            self._db.close()

    @property
    def db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def generate_summary(self, content_item_id: str):
    """
    Generate summary for content item using OpenAI.

    - Uses gpt-4o-mini (or gpt-3.5-turbo if unavailable)
    - Generates a concise TLDR
    - Stores in 'summary' column
    """
    try:
        # Get content item
        item = (
            self.db.query(ContentItem)
            .filter(ContentItem.id == UUID(content_item_id))
            .first()
        )
        if not item:
            logger.error(f"Content item {content_item_id} not found")
            return

        # Check if we have text to summarize
        if not item.full_text:
            logger.warning(f"No text to summarize for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_text"}

        logger.info(f"Generating summary for {item.original_url}")

        # Strip HTML tags — full_text is always HTML regardless of source.
        # Truncate to 10k words (gpt-4o-mini has 128k context but this is plenty).
        text_content = html_to_plain(item.full_text)
        words = text_content.split()
        if len(words) > 10000:
            text_content = " ".join(words[:10000])

        prompt = (
            "You are a helpful reading assistant. "
            "Summarize the following article in 3-5 concise bullet points. "
            "Start each bullet point with a **bold key concept** followed by the explanation. "
            "Format exactly as a markdown list with no introductory or concluding text."
        )

        # Generate summary using OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text_content},
            ],
            max_tokens=500,
        )

        summary = response.choices[0].message.content

        # Store in database
        item.summary = summary
        self.db.commit()

        logger.info(f"Successfully generated summary for {item.original_url}")

        return {
            "content_item_id": content_item_id,
            "status": "completed",
            "summary_len": len(summary),
        }

    except Exception as e:
        logger.error(f"Failed to generate summary for {content_item_id}: {str(e)}")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}
