"""
Unit tests for S3 storage gating (app/core/storage.py).

Both S3_STORAGE_ENABLED and AWS_S3_BUCKET must be set for upload_pdf/
presign_url to actually call S3 — added 2026-09-25 so a bucket can be
provisioned and tested without silently going live for all users the
moment AWS_S3_BUCKET is set.
"""

from unittest.mock import patch

from app.core.config import settings
from app.core.storage import upload_pdf, presign_url


class TestUploadPdfGating:
    def test_noop_when_flag_disabled_even_with_bucket_set(self):
        with patch.object(settings, "S3_STORAGE_ENABLED", False), patch.object(
            settings, "AWS_S3_BUCKET", "some-bucket"
        ), patch("app.core.storage._s3_client") as mock_client:
            result = upload_pdf("user1", "item1", b"pdf bytes")

        assert result is None
        mock_client.assert_not_called()

    def test_noop_when_bucket_empty_even_with_flag_enabled(self):
        with patch.object(settings, "S3_STORAGE_ENABLED", True), patch.object(
            settings, "AWS_S3_BUCKET", ""
        ), patch("app.core.storage._s3_client") as mock_client:
            result = upload_pdf("user1", "item1", b"pdf bytes")

        assert result is None
        mock_client.assert_not_called()

    def test_uploads_when_both_flag_and_bucket_set(self):
        with patch.object(settings, "S3_STORAGE_ENABLED", True), patch.object(
            settings, "AWS_S3_BUCKET", "some-bucket"
        ), patch("app.core.storage._s3_client") as mock_client:
            result = upload_pdf("user1", "item1", b"pdf bytes")

        assert result == "pdfs/user1/item1.pdf"
        mock_client.return_value.put_object.assert_called_once()


class TestPresignUrlGating:
    def test_noop_when_flag_disabled_even_with_bucket_set(self):
        with patch.object(settings, "S3_STORAGE_ENABLED", False), patch.object(
            settings, "AWS_S3_BUCKET", "some-bucket"
        ), patch("app.core.storage._s3_client") as mock_client:
            result = presign_url("pdfs/user1/item1.pdf")

        assert result is None
        mock_client.assert_not_called()

    def test_presigns_when_both_flag_and_bucket_set(self):
        with patch.object(settings, "S3_STORAGE_ENABLED", True), patch.object(
            settings, "AWS_S3_BUCKET", "some-bucket"
        ), patch("app.core.storage._s3_client") as mock_client:
            mock_client.return_value.generate_presigned_url.return_value = (
                "https://example.com/signed"
            )
            result = presign_url("pdfs/user1/item1.pdf")

        assert result == "https://example.com/signed"
