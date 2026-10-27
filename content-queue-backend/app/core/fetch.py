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

SSRF policy: only http/https URLs without userinfo are fetched. The host is
resolved and every address must be globally routable (not private, loopback,
link-local, CGNAT such as Railway's 100.64.0.0/10 private network, multicast,
reserved, unspecified, nor an IPv4-mapped/6to4/NAT64 wrapper of those);
numeric shorthand hosts (2130706433, 0x7f000001, 0177.0.0.1, 127.1) are
decoded and checked without DNS. Redirects are followed manually (max 5), and
scheme and address are re-validated at every hop. DNS rebinding: the address
validation is a pre-check, so after the TCP connect the actual peer address is
checked again (_GuardedHTTPConnection) before any TLS handshake or request
bytes are sent; the connection that carries the request is therefore always to
a validated peer. Residual gaps: (a) a bare TCP SYN reaches a rebinding
target before the peer check closes it (no payload; port-open oracle only);
(b) with an HTTP(S)_PROXY configured the peer is the proxy, so the proxy is
what must enforce egress policy. FETCH_ALLOW_PRIVATE_ADDRESSES=True disables
the address checks (local development only).

Does NOT: retry, or parse content.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

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
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_NUMERIC_HOST = re.compile(r"^[0-9][0-9a-fx.]*$", re.IGNORECASE)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


class FetchRejectedError(requests.RequestException):
    """The fetch is refused: a disallowed URL/address, too many redirects, or a
    response over a hard limit (a PDF over FETCH_PDF_MAX_BYTES)."""


def _address_allowed(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = addr.ipv4_mapped
        if embedded is None and addr.sixtofour is not None:
            embedded = addr.sixtofour
        if embedded is None and addr in _NAT64:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if embedded is not None:
            return _address_allowed(embedded)
        if addr in _NAT64_LOCAL:
            return False
    return addr.is_global and not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _numeric_host(host: str) -> ipaddress._BaseAddress | None:
    """Decode an IP literal, including inet_aton shorthand (decimal, hex, octal,
    short dotted forms) that getaddrinfo would accept and resolve."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _validate_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        parsed.port  # raises ValueError on a malformed port
    except ValueError:
        raise FetchRejectedError("URL not allowed") from None
    if parsed.scheme not in ("http", "https"):
        raise FetchRejectedError("URL scheme not allowed")
    if "@" in parsed.netloc:
        raise FetchRejectedError("URL not allowed")
    if not host:
        raise FetchRejectedError("URL not allowed")
    if settings.FETCH_ALLOW_PRIVATE_ADDRESSES:
        return

    literal = _numeric_host(host)
    if literal is not None:
        addrs = [literal]
    else:
        try:
            infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise requests.ConnectionError(f"Name resolution failed: {exc}") from exc
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addrs or not all(_address_allowed(a) for a in addrs):
        raise FetchRejectedError("Address not allowed")


class _PeerGuard:
    """Re-checks the connected peer so a DNS answer that changed between our
    validation and urllib3's own lookup (rebinding) cannot reach the request."""

    def _new_conn(self):
        sock = super()._new_conn()
        if not settings.FETCH_ALLOW_PRIVATE_ADDRESSES:
            try:
                peer = ipaddress.ip_address(sock.getpeername()[0].split("%")[0])
                allowed = _address_allowed(peer)
            except (OSError, ValueError):
                allowed = False
            if not allowed:
                sock.close()
                raise FetchRejectedError("Address not allowed")
        return sock


class _GuardedHTTPConnection(_PeerGuard, HTTPConnection):
    pass


class _GuardedHTTPSConnection(_PeerGuard, HTTPSConnection):
    pass


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _GuardedHTTPPool,
            "https": _GuardedHTTPSPool,
        }


def _get_hop(url: str, *, timeout: float, headers: Mapping[str, str]):
    """One GET with no redirect following, through the peer-checking adapter."""
    session = requests.Session()
    adapter = _GuardedAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    try:
        return session.get(
            url, timeout=timeout, headers=headers, stream=True, allow_redirects=False
        )
    except requests.ConnectionError as exc:
        session.close()
        # urllib3 wraps the peer guard's rejection; surface it unchanged.
        rejected = (
            next(
                (
                    a
                    for a in getattr(exc.args[0], "args", ())
                    if isinstance(a, FetchRejectedError)
                ),
                None,
            )
            if exc.args
            else None
        )
        if rejected is not None:
            raise rejected from None
        raise
    except BaseException:
        session.close()
        raise


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
    resp = None
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_url(url)
        resp = _get_hop(url, timeout=timeout, headers=headers)
        location = resp.headers.get("location")
        if resp.status_code not in _REDIRECT_STATUSES or not location:
            break
        resp.close()
        url = urljoin(url, location)
    else:
        raise FetchRejectedError("Too many redirects")
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
