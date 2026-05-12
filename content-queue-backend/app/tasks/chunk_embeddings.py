"""
Celery tasks: generate_chunk_embeddings, process_all_missing_chunks.

Splits article full_text into structure-aware chunks (~256-400 tokens each),
prepends a contextual prefix per chunk (Anthropic contextual retrieval pattern),
and embeds each in a batch OpenAI call. Chunks are stored in content_chunks.

At query time, semantic search uses MAX(cosine_similarity) across chunks to
score an article — finds articles that contain a relevant passage anywhere,
not just in the first 8k tokens.

Dispatch: generate_chunk_embeddings.delay(content_item_id)
"""

import logging
import re
from uuid import UUID

from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.chunk import ContentChunk
from app.models.content import ContentItem
from app.tasks.base import DatabaseTask, html_to_plain

logger = logging.getLogger(__name__)

# Target chunk size in tokens (approximate via word count: 1 token ≈ 0.75 words)
_TARGET_TOKENS = 350
_TARGET_WORDS = int(_TARGET_TOKENS / 0.75)  # ~467 words
_OVERLAP_WORDS = 40
_MIN_CHUNK_WORDS = 20  # discard tiny leftover chunks


def split_article_into_chunks(html: str) -> list[str]:
    """
    Split article HTML into plain-text chunks at structural boundaries.

    Strategy (structure-aware recursive):
    1. Split at HTML header tags (h1-h4) — each header starts a new section.
    2. Within each section, split at paragraph boundaries if section is too long.
    3. Strip HTML from each chunk before returning.

    Returns a list of plain-text chunk strings. Empty input returns [].
    """
    if not html or not html.strip():
        return []

    plain = html_to_plain(html)
    if not plain.strip():
        return []

    # Step 1: split at header boundaries using the original HTML
    # Insert a sentinel before each header so we can split cleanly.
    # Use \g<0> to re-insert the full matched tag after the sentinel.
    sectioned = re.sub(
        r"(<h[1-4][^>]*>)", r"\n\n__HEADER__\n\n\g<1>", html, flags=re.IGNORECASE
    )
    raw_sections = re.split(r"\n\n__HEADER__\n\n", sectioned)
    sections = [html_to_plain(s) for s in raw_sections if html_to_plain(s).strip()]

    if not sections:
        return []

    # Step 2: split any section that's too long at paragraph/sentence boundaries
    chunks: list[str] = []
    for section in sections:
        words = section.split()
        if not words:
            continue
        if len(words) <= _TARGET_WORDS:
            if len(words) >= _MIN_CHUNK_WORDS:
                chunks.append(section)
            elif chunks:
                # Merge very short sections into the previous chunk
                chunks[-1] = chunks[-1] + " " + section
            else:
                # First section is short — keep it as-is (don't discard)
                chunks.append(section)
        else:
            # Split at sentence boundaries within the section
            sentences = re.split(r"(?<=[.!?])\s+", section)
            current: list[str] = []
            current_words = 0
            for sentence in sentences:
                s_words = len(sentence.split())
                if current_words + s_words > _TARGET_WORDS and current:
                    chunk_text = " ".join(current)
                    if len(chunk_text.split()) >= _MIN_CHUNK_WORDS:
                        chunks.append(chunk_text)
                    # Overlap: carry last _OVERLAP_WORDS into the next chunk
                    overlap_words = " ".join(current).split()[-_OVERLAP_WORDS:]
                    current = overlap_words + sentence.split()
                    current_words = len(current)
                else:
                    current.extend(sentence.split())
                    current_words += s_words
            if current:
                remainder = " ".join(current)
                if len(remainder.split()) >= _MIN_CHUNK_WORDS:
                    chunks.append(remainder)
                elif chunks:
                    chunks[-1] = chunks[-1] + " " + remainder
                else:
                    chunks.append(remainder)

    return [c.strip() for c in chunks if c.strip()]


def contextual_prefix(
    chunk_text: str,
    article_title: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Prepend article context to a chunk before embedding (Anthropic contextual retrieval).

    Without context, isolated chunks lose meaning — "revenue grew 3%" is useless
    without knowing which company. The prefix anchors the chunk to its source article.
    The chunk text appears exactly once in the output.
    """
    prefix = (
        f'From the article "{article_title}" '
        f"(section {chunk_index + 1} of {total_chunks}): "
    )
    return prefix + chunk_text


def generate_chunk_embeddings(content_item_id: str, db: Session | None = None) -> dict:
    """
    Generate chunk embeddings for a content item.

    Can be called directly (with db= for tests) or as a Celery task via .delay().
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        item = (
            db.query(ContentItem)
            .filter(ContentItem.id == UUID(content_item_id))
            .first()
        )

        if not item:
            logger.error(f"ContentItem {content_item_id} not found")
            return {"status": "not_found"}

        if not item.full_text:
            logger.info(f"No full_text for {content_item_id}, skipping chunks")
            return {"status": "no_text"}

        chunks = split_article_into_chunks(item.full_text)
        if not chunks:
            logger.info(f"No chunks produced for {content_item_id}")
            return {"status": "no_chunks"}

        title = item.title or ""
        total = len(chunks)
        texts_to_embed = [
            contextual_prefix(chunk, title, i, total) for i, chunk in enumerate(chunks)
        ]

        # Batch embed all chunks in one API call
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts_to_embed,
            encoding_format="float",
        )
        embeddings = [e.embedding for e in response.data]

        # Delete existing chunks first (idempotent)
        db.query(ContentChunk).filter(ContentChunk.content_item_id == item.id).delete()

        # Insert new chunks
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            db.add(
                ContentChunk(
                    content_item_id=item.id,
                    user_id=item.user_id,
                    chunk_index=i,
                    text=chunk_text,
                    embedding=embedding,
                )
            )

        db.commit()
        logger.info(f"Generated {len(chunks)} chunks for {item.original_url}")
        return {"status": "completed", "chunk_count": len(chunks)}

    except Exception as e:
        logger.error(f"Failed to generate chunks for {content_item_id}: {e}")
        db.rollback()
        return {"status": "failed", "error": str(e)}
    finally:
        if own_db:
            db.close()


@celery_app.task(
    base=DatabaseTask, bind=True, max_retries=3, name="generate_chunk_embeddings"
)
def generate_chunk_embeddings_task(self, content_item_id: str):
    """Celery-wrapped version of generate_chunk_embeddings."""
    try:
        return generate_chunk_embeddings(content_item_id, db=self.db)
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
        return {"status": "failed", "error": str(e)}


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def process_all_missing_chunks(self):
    """Scanner task: finds items with embeddings but no chunks and backfills them."""
    try:
        from sqlalchemy import text

        rows = self.db.execute(
            text(
                """
            SELECT DISTINCT ci.id
            FROM content_items ci
            LEFT JOIN content_chunks cc ON cc.content_item_id = ci.id
            WHERE ci.embedding IS NOT NULL
              AND ci.full_text IS NOT NULL
              AND ci.deleted_at IS NULL
              AND cc.id IS NULL
            LIMIT 100
        """
            )
        ).fetchall()

        for (item_id,) in rows:
            generate_chunk_embeddings_task.delay(str(item_id))

        return {"dispatched": len(rows), "status": "completed"}
    except Exception as e:
        logger.error(f"process_all_missing_chunks failed: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=120)
        return {"status": "failed", "error": str(e)}
