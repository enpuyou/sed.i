"""
Unit tests for app.core.fetch.fetch_bounded — size-bounded URL fetch.

Pure-unit: requests.get is replaced by a fake streaming response, so the tests
assert how many bytes are read and what is rejected, with no network.

Acceptance: an HTML page larger than the cap is truncated (still ingested), a
clearly binary content type is not downloaded (empty body, headers kept), an
oversized PDF is rejected, and the read never exceeds the cap regardless of
Content-Length.
"""

import pytest
import requests

from app.core import fetch as fetch_mod
from app.core.fetch import FetchRejectedError, fetch_bounded


class FakeResponse:
    def __init__(self, body: bytes, headers: dict, status_code: int = 200):
        self._body = body
        self.headers = headers
        self.status_code = status_code
        self.url = "https://example.test/x"
        self.bytes_served = 0
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            piece = self._body[i : i + chunk_size]
            self.bytes_served += len(piece)
            yield piece

    def close(self):
        self.closed = True


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(fetch_mod.settings, "FETCH_HTML_MAX_BYTES", 100_000)
    monkeypatch.setattr(fetch_mod.settings, "FETCH_PDF_MAX_BYTES", 300_000)
    monkeypatch.setattr(fetch_mod.settings, "FETCH_MAX_SECONDS", 60)


def _patch_get(monkeypatch, resp):
    monkeypatch.setattr(fetch_mod.requests, "get", lambda *a, **k: resp)


def test_small_html_returned_whole(monkeypatch, small_caps):
    resp = FakeResponse(
        b"<html>" + b"a" * 1000 + b"</html>", {"content-type": "text/html"}
    )
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert page.content == resp._body
    assert page.truncated is False
    assert resp.closed


def test_large_html_is_truncated_not_rejected(monkeypatch, small_caps):
    resp = FakeResponse(b"x" * 1_000_000, {"content-type": "text/html; charset=utf-8"})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert page.truncated is True
    assert len(page.content) == 100_000
    # Stopped reading near the cap instead of draining the 1 MB body.
    assert resp.bytes_served <= 100_000 + 65536
    assert resp.closed


def test_content_length_does_not_override_read_cap(monkeypatch, small_caps):
    resp = FakeResponse(
        b"x" * 500_000, {"content-type": "text/html", "content-length": "10"}
    )
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert len(page.content) == 100_000


@pytest.mark.parametrize(
    "content_type", ["video/mp4", "application/zip", "image/png", "audio/mpeg"]
)
def test_binary_types_skip_body_and_return_empty_content(
    monkeypatch, small_caps, content_type
):
    resp = FakeResponse(b"x" * 1000, {"content-type": content_type})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert page.content == b""
    assert page.headers["content-type"] == content_type
    assert resp.bytes_served == 0
    assert resp.closed


def test_missing_content_type_allowed_as_html(monkeypatch, small_caps):
    resp = FakeResponse(b"<html>ok</html>", {})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert page.content == b"<html>ok</html>"


def test_octet_stream_is_read_under_the_cap_not_skipped(monkeypatch, small_caps):
    resp = FakeResponse(
        b"<html>mislabelled</html>" * 10_000,
        {"content-type": "application/octet-stream"},
    )
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/download", timeout=5, headers={})

    assert page.content.startswith(b"<html>")
    assert len(page.content) == 100_000
    assert page.truncated is True


def test_octet_stream_pdf_url_uses_pdf_limit(monkeypatch, small_caps):
    resp = FakeResponse(
        b"%PDF-1.4" + b"p" * 200_000, {"content-type": "application/octet-stream"}
    )
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/paper.pdf", timeout=5, headers={})

    assert len(page.content) == 200_008
    assert page.truncated is False


def test_pdf_within_cap_returned_whole(monkeypatch, small_caps):
    resp = FakeResponse(b"%PDF" + b"p" * 200_000, {"content-type": "application/pdf"})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/p.pdf", timeout=5, headers={})

    assert len(page.content) == 200_004
    assert page.truncated is False


def test_pdf_over_cap_rejected_by_content_length_without_reading(
    monkeypatch, small_caps
):
    resp = FakeResponse(
        b"%PDF" + b"p" * 10,
        {"content-type": "application/pdf", "content-length": "5000000"},
    )
    _patch_get(monkeypatch, resp)

    with pytest.raises(FetchRejectedError) as exc:
        fetch_bounded("https://example.test/p.pdf", timeout=5, headers={})

    assert "too large" in str(exc.value).lower()
    assert resp.bytes_served == 0


def test_pdf_over_cap_rejected_when_length_header_missing(monkeypatch, small_caps):
    resp = FakeResponse(b"%PDF" + b"p" * 1_000_000, {"content-type": "application/pdf"})
    _patch_get(monkeypatch, resp)

    with pytest.raises(FetchRejectedError):
        fetch_bounded("https://example.test/p.pdf", timeout=5, headers={})

    assert resp.bytes_served <= 300_000 + 65536


def test_http_error_propagates_as_http_error(monkeypatch, small_caps):
    resp = FakeResponse(b"nope", {"content-type": "text/html"}, status_code=403)
    _patch_get(monkeypatch, resp)

    with pytest.raises(requests.HTTPError) as exc:
        fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert exc.value.response.status_code == 403
    assert resp.closed


def test_total_time_cap_raises_timeout(monkeypatch, small_caps):
    monkeypatch.setattr(fetch_mod.settings, "FETCH_MAX_SECONDS", 1)
    clock = iter([0.0, 0.0, 5.0, 10.0, 15.0])
    monkeypatch.setattr(fetch_mod, "_now", lambda: next(clock))
    resp = FakeResponse(b"x" * 400_000, {"content-type": "text/html"})
    _patch_get(monkeypatch, resp)

    with pytest.raises(requests.Timeout):
        fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert resp.closed


def test_rejection_is_a_request_exception():
    """Existing `except requests.RequestException` handlers must catch it."""
    assert issubclass(FetchRejectedError, requests.RequestException)


def test_pdf_exactly_at_the_cap_is_accepted(monkeypatch, small_caps):
    resp = FakeResponse(b"p" * 300_000, {"content-type": "application/pdf"})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/p.pdf", timeout=5, headers={})

    assert len(page.content) == 300_000
    assert page.truncated is False


def test_html_exactly_at_the_cap_is_not_flagged_truncated(monkeypatch, small_caps):
    resp = FakeResponse(b"h" * 100_000, {"content-type": "text/html"})
    _patch_get(monkeypatch, resp)

    page = fetch_bounded("https://example.test/a", timeout=5, headers={})

    assert len(page.content) == 100_000
    assert page.truncated is False
