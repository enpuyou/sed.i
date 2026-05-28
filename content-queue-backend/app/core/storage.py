"""
S3 object storage helpers for sed.i.

All operations are no-ops when AWS_S3_BUCKET is not configured, so the app
works in local dev and test environments without any AWS credentials.

Key layout:
  pdfs/<user_id>/<item_id>.pdf   — raw PDF bytes uploaded at ingestion time
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def _s3_client() -> Any:
    """Build a boto3 S3 client using the same credentials as Bedrock."""
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def upload_pdf(user_id: str, item_id: str, pdf_bytes: bytes) -> str | None:
    """
    Upload raw PDF bytes to S3 and return the object key.

    Returns None if S3 is not configured or the upload fails — callers should
    treat a None key as "PDF not in S3" and fall back to re-fetching from the
    original URL.
    """
    if not settings.AWS_S3_BUCKET:
        return None

    key = f"pdfs/{user_id}/{item_id}.pdf"
    try:
        _s3_client().put_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
        logger.info(f"Uploaded PDF to s3://{settings.AWS_S3_BUCKET}/{key}")
        return key
    except Exception as e:
        logger.warning(f"S3 upload failed for {item_id}: {e}")
        return None


def presign_url(s3_key: str, expiry: int | None = None) -> str | None:
    """
    Generate a presigned GET URL for a stored object.

    Returns None if S3 is not configured or the key is empty.
    """
    if not settings.AWS_S3_BUCKET or not s3_key:
        return None

    ttl = expiry if expiry is not None else settings.AWS_S3_PRESIGN_EXPIRY
    try:
        url = _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_S3_BUCKET, "Key": s3_key},
            ExpiresIn=ttl,
        )
        return url
    except Exception as e:
        logger.warning(f"Presign failed for {s3_key}: {e}")
        return None
