"""
Celery tasks: generate_embedding, generate_highlight_embeddings_batch,
process_all_missing_embeddings.

Calls OpenAI text-embedding-3-small to produce a 1536-dim vector and stores
it on the ContentItem or Highlight. No-op if OPENAI_API_KEY is unset.

Dispatch: generate_embedding.delay(content_item_id)
          generate_highlight_embeddings_batch.delay(user_id)
"""

from app.core.celery_app import celery_app
from app.core.llm_client import llm_client
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.tasks.base import DatabaseTask, html_to_plain
from uuid import UUID
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


def generate_embedding_for_item(content_item_id: str, db: Session) -> dict:
    """
    Generate and store a semantic embedding for a ContentItem.

    Plain function (no Celery, no downstream task triggers) — suitable for
    calling from Prefect flows or other orchestrators that manage sequencing
    themselves. The Celery task `generate_embedding` wraps this.
    """
    item = db.query(ContentItem).filter(ContentItem.id == UUID(content_item_id)).first()
    if not item:
        logger.error(f"Content item {content_item_id} not found")
        return {"status": "not_found"}

    if not item.full_text and not item.description:
        logger.warning(f"No text to embed for {content_item_id}")
        return {"status": "no_text"}

    text_parts = []
    if item.title:
        text_parts.append(item.title)
    if item.description:
        text_parts.append(item.description)
    if item.full_text:
        text_parts.append(html_to_plain(item.full_text))

    combined_text = "\n\n".join(text_parts)

    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(combined_text)
        if len(tokens) > 8000:
            tokens = tokens[:8000]
            text_to_embed = encoding.decode(tokens)
        else:
            text_to_embed = combined_text
    except Exception:
        max_chars = 8000 * 4
        text_to_embed = (
            combined_text[:max_chars]
            if len(combined_text) > max_chars
            else combined_text
        )

    result = llm_client.embed(text_to_embed)
    embedding = result.embeddings[0]
    item.embedding = embedding
    db.commit()

    logger.info(f"Embedding generated for {item.original_url} (dim: {len(embedding)})")
    return {
        "content_item_id": content_item_id,
        "embedding_dimension": len(embedding),
        "status": "completed",
    }


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def generate_embedding(self, content_item_id: str):
    """
    Generate embedding for content item using OpenAI.

    - Uses text-embedding-3-small model (1536 dimensions)
    - Combines title + description + full_text
    - Stores embedding in pgvector column
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

        # Check if we have text to embed
        if not item.full_text and not item.description:
            logger.warning(f"No text to embed for {content_item_id}")
            return {"content_item_id": content_item_id, "status": "no_text"}

        logger.info(f"Generating embedding for {item.original_url}")

        # Prepare text for embedding — strip HTML tags first.
        # full_text is always HTML (trafilatura, extension, PDF all store HTML).
        # Stripping tags gives cleaner semantic signal for the embedding model.
        text_parts = []
        if item.title:
            text_parts.append(item.title)
        if item.description:
            text_parts.append(item.description)
        if item.full_text:
            text_parts.append(html_to_plain(item.full_text))

        combined_text = "\n\n".join(text_parts)

        # Use tiktoken to count tokens accurately
        # text-embedding-3-small uses cl100k_base encoding
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
            tokens = encoding.encode(combined_text)

            # Limit to 8000 tokens (leaving buffer for safety, max is 8191)
            if len(tokens) > 8000:
                logger.info(
                    f"Truncating text from {len(tokens)} to 8000 tokens for {item.original_url}"
                )
                tokens = tokens[:8000]
                text_to_embed = encoding.decode(tokens)
            else:
                text_to_embed = combined_text
        except Exception as e:
            logger.warning(
                f"Token counting failed, falling back to character limit: {e}"
            )
            # Fallback: simple character limit (conservative estimate: ~4 chars per token)
            max_chars = 8000 * 4  # ~32k characters
            if len(combined_text) > max_chars:
                text_to_embed = combined_text[:max_chars]
            else:
                text_to_embed = combined_text

        # Generate embedding
        result = llm_client.embed(text_to_embed)
        embedding = result.embeddings[0]

        # Store in database
        item.embedding = embedding
        self.db.commit()

        logger.info(
            f"Successfully generated embedding for {item.original_url} (dimension: {len(embedding)})"
        )

        # Trigger combined analysis (tags + entities) and chunk embeddings
        from app.tasks.article_analysis import analyze_article_task
        from app.tasks.chunk_embeddings import generate_chunk_embeddings_task

        analyze_article_task.delay(content_item_id)
        generate_chunk_embeddings_task.delay(content_item_id)

        return {
            "content_item_id": content_item_id,
            "embedding_dimension": len(embedding),
            "status": "completed",
        }

    except Exception as e:
        logger.error(f"Failed to generate embedding for {content_item_id}: {str(e)}")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

        # Only update status if retries exhausted
        item = (
            self.db.query(ContentItem)
            .filter(ContentItem.id == UUID(content_item_id))
            .first()
        )
        if item:
            item.processing_status = "failed"
            item.processing_error = f"Embedding error: {str(e)}"
            self.db.commit()

        return {"content_item_id": content_item_id, "status": "failed", "error": str(e)}


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def generate_highlight_embeddings_batch(self, user_id: str):
    """
    Batch generate embeddings for all highlights without embeddings for a user.

    - Uses text-embedding-3-small model (1536 dimensions)
    - Embeds highlight text + surrounding context
    - Periodic task: run every 5 minutes or triggered manually
    - NOT real-time to avoid wasting API calls on temporary highlights
    """
    try:
        user_uuid = UUID(user_id)

        # Find all highlights for this user without embeddings
        highlights_to_embed = (
            self.db.query(Highlight)
            .filter(
                Highlight.user_id == user_uuid,
                Highlight.embedding.is_(None),
            )
            .all()
        )

        if not highlights_to_embed:
            logger.info(f"No highlights to embed for user {user_id}")
            return {"user_id": user_id, "count": 0, "status": "completed"}

        logger.info(
            f"Batch embedding {len(highlights_to_embed)} highlights for user {user_id}"
        )

        # Prepare texts for embedding (batch API call)
        embed_tasks = []
        for highlight in highlights_to_embed:
            if not highlight.text:
                continue

            # Use highlight text as is (already short)
            # Could enhance with surrounding context from full_text if needed
            embed_tasks.append((highlight.id, highlight.text))

        if not embed_tasks:
            logger.info(f"No valid highlight text to embed for user {user_id}")
            return {"user_id": user_id, "count": 0, "status": "completed"}

        # Batch embed all texts at once
        texts_to_embed = [text for _, text in embed_tasks]
        result = llm_client.embed(texts_to_embed)

        # Update highlights with embeddings
        embeddings_map = {i: emb for i, emb in enumerate(result.embeddings)}
        embedded_count = 0

        for idx, (highlight_id, _) in enumerate(embed_tasks):
            if idx in embeddings_map:
                highlight = (
                    self.db.query(Highlight)
                    .filter(Highlight.id == highlight_id)
                    .first()
                )
                if highlight:
                    highlight.embedding = embeddings_map[idx]
                    embedded_count += 1

        self.db.commit()

        logger.info(
            f"Successfully embedded {embedded_count} highlights for user {user_id}"
        )

        return {
            "user_id": user_id,
            "count": embedded_count,
            "status": "completed",
        }

    except Exception as e:
        logger.error(f"Failed to batch embed highlights for user {user_id}: {str(e)}")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def process_all_missing_embeddings(self):
    """
    Scanner task: Finds all users with unembedded highlights and triggers batch processing for them.

    - Runs periodically (e.g. every 5 mins)
    - Dispatches per-user tasks to distribute load
    """
    try:
        # Find distinct user_ids that have highlights with missing embeddings
        # We use distinct() to avoid duplicate user_ids
        users_with_missing = (
            self.db.query(Highlight.user_id)
            .filter(Highlight.embedding.is_(None))
            .distinct()
            .all()
        )

        if not users_with_missing:
            logger.info("No missing highlight embeddings found for any user.")
            return {"count": 0, "status": "completed"}

        logger.info(
            f"Found {len(users_with_missing)} users with missing highlight embeddings. Dispatching tasks."
        )

        dispatched_count = 0
        for (user_id,) in users_with_missing:
            # Trigger the batch task for this specific user
            generate_highlight_embeddings_batch.delay(str(user_id))
            dispatched_count += 1

        return {"dispatched_count": dispatched_count, "status": "completed"}

    except Exception as e:
        logger.error(f"Failed to scan for missing embeddings: {str(e)}")
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

        return {"status": "failed", "error": str(e)}
