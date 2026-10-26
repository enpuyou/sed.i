"""
Size-bounded HTTP fetch for URL ingestion.

Scope: the single place the extraction tasks download a user-supplied URL.
Streams the body and stops at a byte cap, so a page's memory cost is bounded
by the cap instead of by whatever the remote server sends (trafilatura +
BeautifulSoup peak at ~37x the page size).

Policy: HTML (and any other type) is truncated at FETCH_HTML_MAX_BYTES, never
rejected for size, so the article still ingests. PDFs are capped separately
(FETCH_PDF_MAX_BYTES) and rejected above it. Clearly binary types (video,
audio, image, font, archives) are not downloaded: the caller gets an empty
body and the response headers, which is what extraction already did with their
bytes (nothing extractable). A wall-clock cap (checked every 16 KB) bounds the
total download time while data keeps arriving; a server that stalls entirely is
still bounded only by the per-read timeout the caller passes.

Does NOT: validate the target address (SSRF), retry, or parse content.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 16 * 1024
_SKIPPED_PREFIXES = ("video/", "audio/", "image/", "font/")
_SKIPPED_TYPES = {
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-bzip2",
    "application/x-xz",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
}
_GENERIC_BINARY_TYPES = {"application/octet-stream", "binary/octet-stream"}


class FetchRejectedError(requests.RequestException):
    """The response exceeds a hard limit (a PDF over FETCH_PDF_MAX_BYTES)."""


@dataclass
class FetchedPage:
    content: bytes
    headers: Mapping[str, str]
    status_code: int
    url: str
    truncated: bool
    _response: requests.Response

    def raise_for_status(self) -> None:
        self._response.raise_for_status()


def _now() -> float:
    return time.monotonic()


def _is_pdf(content_type: str, url: str) -> bool:
    if content_type == "application/pdf":
        return True
    looks_like_pdf_url = urlparse(url).path.lower().endswith(".pdf")
    return looks_like_pdf_url and content_type in (_GENERIC_BINARY_TYPES | {""})


def _is_skipped(content_type: str) -> bool:
    return content_type.startswith(_SKIPPED_PREFIXES) or content_type in _SKIPPED_TYPES


def fetch_bounded(
    url: str, *, timeout: float, headers: Mapping[str, str]
) -> FetchedPage:
    resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
    try:
        resp.raise_for_status()

        content_type = (
            resp.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        is_pdf = _is_pdf(content_type, url)
        if _is_skipped(content_type):
            logger.info(f"Skipping body of {url} ({content_type})")
            return FetchedPage(
                content=b"",
                headers=resp.headers,
                status_code=resp.status_code,
                url=resp.url,
                truncated=False,
                _response=resp,
            )

        limit = (
            settings.FETCH_PDF_MAX_BYTES if is_pdf else settings.FETCH_HTML_MAX_BYTES
        )
        declared = resp.headers.get("content-length", "")
        if is_pdf and declared.isdigit() and int(declared) > limit:
            raise FetchRejectedError(
                f"PDF too large ({int(declared) // (1024 * 1024)} MB, "
                f"limit {limit // (1024 * 1024)} MB)"
            )

        deadline = _now() + settings.FETCH_MAX_SECONDS
        pieces: list[bytes] = []
        total = 0
        truncated = False
        for piece in resp.iter_content(chunk_size=_CHUNK_BYTES):
            if _now() > deadline:
                raise requests.Timeout(
                    f"Download exceeded {settings.FETCH_MAX_SECONDS}s"
                )
            if total + len(piece) > limit:
                if is_pdf:
                    raise FetchRejectedError(
                        f"PDF too large (over {limit // (1024 * 1024)} MB)"
                    )
                # Keep only what fits so the cap is never exceeded in memory.
                piece = piece[: limit - total]
                truncated = True
            pieces.append(piece)
            total += len(piece)
            if truncated:
                break

        body = b"".join(pieces)
        pieces.clear()
        if truncated:
            logger.warning(f"Truncated {url} at {limit} bytes")
        return FetchedPage(
            content=body,
            headers=resp.headers,
            status_code=resp.status_code,
            url=resp.url,
            truncated=truncated,
            _response=resp,
        )
    finally:
        resp.close()
