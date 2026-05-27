"""
Prefect ingestion flow — observable DAG for content processing.

The flow wraps the existing ingestion pipeline as Prefect tasks so that:
  - Each step appears in the Prefect UI with timing, retry count, and status
  - Retries happen per-step, not per whole chain
  - Failures identify exactly which step failed and why

Pipeline: fetch-metadata → extract-full-content → embed → tag → chunk-embed

Design: each Prefect task delegates to the same underlying plain function used
by the Celery task — no logic duplication. The plain functions are:
  - app.tasks.extraction.extract_full_content_for_item
  - app.tasks.embedding.generate_embedding_for_item
  - app.tasks.tagging.generate_tags
  - app.tasks.chunk_embeddings.generate_chunk_embeddings

Phase 1 (fetch-metadata) is still done by the Celery task's inner logic because
it uses the DatabaseTask base class heavily. The Prefect flow takes over from
Phase 2 onward, where the plain functions are clean entry points.

Opt-in: set PREFECT_ENABLED=true to route new ingestion jobs through this flow.
Default is false — existing Celery chain runs unchanged.

Running locally:
    prefect server start               # UI at localhost:4200
    prefect worker start -p default    # worker on the default work pool
"""

from __future__ import annotations

import logging

from prefect import flow, task, get_run_logger

logger = logging.getLogger(__name__)


@task(name="extract-full-content", retries=2, retry_delay_seconds=15)
def extract_full_content(item_id: str) -> None:
    """
    Re-fetch URL and extract full article HTML via trafilatura.
    No-op when content was pre-provided by the browser extension.
    """
    from app.tasks.extraction import extract_full_content_for_item
    from app.core.database import SessionLocal

    run_logger = get_run_logger()
    run_logger.info(f"Extracting full content for {item_id}")

    db = SessionLocal()
    try:
        extract_full_content_for_item(item_id, db=db)
    except Exception as e:
        run_logger.error(f"extract-full-content failed for {item_id}: {e}")
        raise
    finally:
        db.close()


@task(name="generate-embedding", retries=3, retry_delay_seconds=5)
def generate_embedding(item_id: str) -> None:
    """Generate the item-level semantic embedding."""
    from app.tasks.embedding import generate_embedding_for_item
    from app.core.database import SessionLocal

    run_logger = get_run_logger()
    run_logger.info(f"Generating embedding for {item_id}")

    db = SessionLocal()
    try:
        generate_embedding_for_item(item_id, db=db)
    except Exception as e:
        run_logger.error(f"generate-embedding failed for {item_id}: {e}")
        raise
    finally:
        db.close()


@task(name="generate-tags", retries=2, retry_delay_seconds=10)
def generate_tags(item_id: str) -> None:
    """Generate semantic tags."""
    from app.tasks.tagging import generate_tags as _generate_tags
    from app.core.database import SessionLocal

    run_logger = get_run_logger()
    run_logger.info(f"Generating tags for {item_id}")

    db = SessionLocal()
    try:
        _generate_tags(item_id, db=db)
    except Exception as e:
        run_logger.error(f"generate-tags failed for {item_id}: {e}")
        raise
    finally:
        db.close()


@task(name="generate-chunk-embeddings", retries=2, retry_delay_seconds=10)
def generate_chunk_embeddings(item_id: str) -> None:
    """Generate per-chunk embeddings for hybrid search."""
    from app.tasks.chunk_embeddings import generate_chunk_embeddings as _gen_chunks
    from app.core.database import SessionLocal

    run_logger = get_run_logger()
    run_logger.info(f"Generating chunk embeddings for {item_id}")

    db = SessionLocal()
    try:
        _gen_chunks(item_id, db=db)
    except Exception as e:
        run_logger.error(f"generate-chunk-embeddings failed for {item_id}: {e}")
        raise
    finally:
        db.close()


@flow(
    name="ingest-content",
    description="Full ingestion pipeline: extract → embed → tag → chunk-embed",
)
def ingest_content(item_id: str) -> dict:
    """
    Observable ingestion flow for a single ContentItem.

    Assumes Phase 1 (metadata fetch) already ran via the Celery task that
    triggered this flow. Runs Phase 2 onward: full content extraction,
    embedding, tagging, and chunk embedding.

    Each step is independently retried. The Prefect UI shows which step
    failed and how many retries were attempted.
    """
    run_logger = get_run_logger()
    run_logger.info(f"Starting ingestion flow for item {item_id}")

    try:
        extract_full_content(item_id)
        generate_embedding(item_id)
        generate_tags(item_id)
        generate_chunk_embeddings(item_id)
    except Exception as exc:
        # Mark the item as failed in the DB — without this, the item stays
        # stuck in "processing" because only the Celery path sets "failed".
        run_logger.error(f"Ingestion flow failed for {item_id}: {exc}")
        try:
            from app.core.database import SessionLocal
            from app.models.content import ContentItem
            from uuid import UUID

            db = SessionLocal()
            try:
                item = (
                    db.query(ContentItem)
                    .filter(ContentItem.id == UUID(item_id))
                    .first()
                )
                if item:
                    item.processing_status = "failed"
                    item.processing_error = str(exc)
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass
        raise

    run_logger.info(f"Ingestion flow complete for {item_id}")
    return {"item_id": item_id, "status": "completed"}
