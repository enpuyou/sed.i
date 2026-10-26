"""
Unit tests for app.core.fetch.fetch_bounded — size-bounded URL fetch.

Pure-unit: requests.get is replaced by a fake streaming response, so the tests
assert how many bytes are read and what is rejected, with no network.

Acceptance: an HTML page larger than the cap is truncated (still ingested), a
clearly binary content type is not downloaded (empty body, headers kept), an
oversized PDF is rejected, and the read never exceeds the cap regardless of
Content-Length.
"""

import socket

import pytest
import requests

from app.core import fetch as fetch_mod
from app.core.fetch import FetchRejectedError, fetch_bounded

_REAL_GETADDRINFO = socket.getaddrinfo


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
    monkeypatch.setattr(fetch_mod, "_get_hop", lambda *a, **k: resp)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every hostname resolves to a public address; no real DNS in these tests."""
    monkeypatch.setattr(
        fetch_mod.socket,
        "getaddrinfo",
        lambda host, *a, **k: _addrinfo("93.184.216.34"),
    )


def _addrinfo(*ips):
    return [
        (
            socket.AF_INET6 if ":" in ip else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (ip, 0),
        )
        for ip in ips
    ]


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


# --- SSRF: address validation -------------------------------------------------
# Acceptance: a URL whose host is (or resolves to, or redirects to) a private,
# loopback, link-local, CGNAT, multicast or reserved address is rejected with
# FetchRejectedError before any request is sent; a public host is fetched.


def _ok():
    return FakeResponse(b"<html>ok</html>", {"content-type": "text/html"})


def test_public_hostname_allowed(monkeypatch, small_caps):
    _patch_get(monkeypatch, _ok())
    page = fetch_bounded("https://example.test/a", timeout=5, headers={})
    assert page.content == b"<html>ok</html>"


@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "172.16.5.5",
        "192.168.1.1",
        "100.64.0.1",
        "100.127.255.254",
        "fc00::1",
        "fd12:3456::1",
        "fe80::1",
        "0.0.0.0",
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "2002:7f00:0001::1",  # 6to4 embedding 127.0.0.1
        "64:ff9b::7f00:1",  # NAT64 embedding 127.0.0.1
    ],
)
def test_private_resolution_rejected(monkeypatch, small_caps, ip):
    monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip))
    called = []
    monkeypatch.setattr(
        fetch_mod, "_get_hop", lambda *a, **k: called.append(1) or _ok()
    )
    with pytest.raises(FetchRejectedError, match="Address not allowed"):
        fetch_bounded("https://internal.test/", timeout=5, headers={})
    assert not called


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "169.254.169.254",
        "2130706433",  # decimal 127.0.0.1
        "0x7f000001",  # hex
        "017700000001",  # octal
        "0177.0.0.1",
        "127.1",
        "2852039166",  # decimal 169.254.169.254
    ],
)
def test_numeric_hosts_rejected_without_dns(monkeypatch, small_caps, host):
    def boom(*a, **k):
        raise AssertionError("must not need DNS for a numeric host")

    monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", boom)
    with pytest.raises(FetchRejectedError, match="Address not allowed"):
        fetch_bounded(f"http://{host}/x", timeout=5, headers={})


def test_mixed_public_and_private_resolution_rejected(monkeypatch, small_caps):
    monkeypatch.setattr(
        fetch_mod.socket,
        "getaddrinfo",
        lambda *a, **k: _addrinfo("93.184.216.34", "10.0.0.5"),
    )
    with pytest.raises(FetchRejectedError, match="Address not allowed"):
        fetch_bounded("https://mixed.test/", timeout=5, headers={})


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/x",
        "file:///etc/passwd",
        "gopher://example.test/",
        "https://user:pw@example.test/",
        "https://user@example.test/",
        "https:///nohost",
        "not a url",
    ],
)
def test_bad_scheme_userinfo_or_host_rejected(monkeypatch, small_caps, url):
    with pytest.raises(FetchRejectedError):
        fetch_bounded(url, timeout=5, headers={})


def test_unresolvable_host_is_a_request_error(monkeypatch, small_caps):
    def nxdomain(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", nxdomain)
    with pytest.raises(requests.ConnectionError):
        fetch_bounded("https://nope.test/", timeout=5, headers={})


def _redirect(location):
    return FakeResponse(b"", {"location": location}, status_code=302)


def test_redirect_to_public_is_followed(monkeypatch, small_caps):
    seen = []
    resps = iter([_redirect("/next"), _ok()])

    def hop(url, **k):
        seen.append(url)
        return next(resps)

    monkeypatch.setattr(fetch_mod, "_get_hop", hop)
    page = fetch_bounded("https://example.test/a", timeout=5, headers={})
    assert seen == ["https://example.test/a", "https://example.test/next"]
    assert page.content == b"<html>ok</html>"


def test_redirect_to_private_address_rejected(monkeypatch, small_caps):
    def dns(host, *a, **k):
        return _addrinfo("169.254.169.254" if host == "meta.test" else "93.184.216.34")

    monkeypatch.setattr(fetch_mod.socket, "getaddrinfo", dns)
    seen = []

    def hop(url, **k):
        seen.append(url)
        return _redirect("http://meta.test/latest/meta-data/")

    monkeypatch.setattr(fetch_mod, "_get_hop", hop)
    with pytest.raises(FetchRejectedError, match="Address not allowed"):
        fetch_bounded("https://example.test/a", timeout=5, headers={})
    assert seen == ["https://example.test/a"]


def test_redirect_to_literal_loopback_and_bad_scheme_rejected(monkeypatch, small_caps):
    for loc in ("http://127.0.0.1:6379/", "http://2130706433/", "file:///etc/passwd"):
        monkeypatch.setattr(
            fetch_mod, "_get_hop", lambda *a, loc=loc, **k: _redirect(loc)
        )
        with pytest.raises(FetchRejectedError):
            fetch_bounded("https://example.test/a", timeout=5, headers={})


def test_redirect_chain_over_five_rejected(monkeypatch, small_caps):
    n = []

    def hop(url, **k):
        n.append(url)
        return _redirect(f"/r{len(n)}")

    monkeypatch.setattr(fetch_mod, "_get_hop", hop)
    with pytest.raises(FetchRejectedError, match="Too many redirects"):
        fetch_bounded("https://example.test/a", timeout=5, headers={})
    assert len(n) == 6  # the original request plus 5 followed hops


def test_five_redirects_then_success_allowed(monkeypatch, small_caps):
    resps = iter([_redirect("/1")] * 5 + [_ok()])
    monkeypatch.setattr(fetch_mod, "_get_hop", lambda *a, **k: next(resps))
    page = fetch_bounded("https://example.test/a", timeout=5, headers={})
    assert page.content == b"<html>ok</html>"


def test_allow_private_addresses_flag_for_local_dev(monkeypatch, small_caps):
    monkeypatch.setattr(fetch_mod.settings, "FETCH_ALLOW_PRIVATE_ADDRESSES", True)
    _patch_get(monkeypatch, _ok())
    page = fetch_bounded("http://127.0.0.1:8000/x", timeout=5, headers={})
    assert page.content == b"<html>ok</html>"


def test_flag_does_not_relax_scheme_check(monkeypatch, small_caps):
    monkeypatch.setattr(fetch_mod.settings, "FETCH_ALLOW_PRIVATE_ADDRESSES", True)
    with pytest.raises(FetchRejectedError):
        fetch_bounded("file:///etc/passwd", timeout=5, headers={})


def test_peer_address_checked_after_connect_defeats_rebinding(monkeypatch, small_caps):
    """DNS validation passes (rebinding: first answer public), but the socket
    that actually connects is loopback; the connection is refused by the
    peer-address check. Uses a local listener only, no external network."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    calls = []

    def rebinding_dns(host, p, *a, **k):
        # First lookup (our validation) answers public; later lookups (the
        # one urllib3 does to connect) answer loopback.
        calls.append(host)
        if len(calls) == 1:
            return _addrinfo("93.184.216.34")
        return _REAL_GETADDRINFO("127.0.0.1", port, socket.AF_INET, socket.SOCK_STREAM)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_dns)
    try:
        with pytest.raises(FetchRejectedError, match="Address not allowed"):
            fetch_bounded("http://rebind.test:%d/" % port, timeout=5, headers={})
    finally:
        srv.close()
