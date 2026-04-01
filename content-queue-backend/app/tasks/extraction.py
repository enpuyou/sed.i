import json
import logging
import re
import requests
import trafilatura
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from app.core.celery_app import celery_app
from app.tasks.base import DatabaseTask
from app.models.content import ContentItem
from app.tasks.embedding import generate_embedding

logger = logging.getLogger(__name__)

SOCIAL_MEDIA_DOMAINS = {
    "instagram.com",
    "twitter.com",
    "x.com",
    "threads.net",
    "facebook.com",
    "tiktok.com",
    "reddit.com",
    "linkedin.com",
    "pinterest.com",
    "snapchat.com",
    "tumblr.com",
    "mastodon.social",
    "bsky.app",
}

VIDEO_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "twitch.tv",
    "dailymotion.com",
    "wistia.com",
}

SOURCE_RESTRICTION_MARKERS = [
    "paywall",
    "subscriber-only",
    "subscriber only",
    "subscription required",
    "subscribe to continue",
    "sign in to continue",
    "log in to continue",
    "already a subscriber",
    "continue reading",
    "unlock this article",
    "premium content",
    "metered",
]


def _detect_limited_extraction_reason(
    downloaded: bytes, html_text: str, page_url: str | None = None
) -> str | None:
    """
    Detect likely limited/truncated extraction from source restrictions.

    Avoid relying on absolute length alone, because some articles are genuinely
    short. Use paywall markers and relative extraction coverage instead.
    """
    source_soup = BeautifulSoup(downloaded, "html.parser")
    extracted_soup = BeautifulSoup(html_text, "html.parser")

    source_text = source_soup.get_text(" ", strip=True)
    extracted_text = extracted_soup.get_text(" ", strip=True)

    source_len = len(source_text)
    extracted_len = len(extracted_text)

    extracted_paragraphs = sum(
        1
        for p in extracted_soup.find_all("p")
        if len(p.get_text(" ", strip=True)) >= 45
    )
    extracted_has_media = bool(
        extracted_soup.find("figure")
        or extracted_soup.find("img")
        or extracted_soup.find("picture")
    )

    source_container = (
        source_soup.find("article")
        or source_soup.find("main")
        or source_soup.find("body")
        or source_soup
    )
    source_paragraphs = sum(
        1
        for p in source_container.find_all("p")
        if len(p.get_text(" ", strip=True)) >= 45
    )

    # Attribute-based hints catch paywall wrappers even when copy text is minimal.
    attrs = []
    for tag in source_soup.find_all(True, limit=500):
        classes = tag.get("class") or []
        if isinstance(classes, str):
            attrs.append(classes)
        else:
            attrs.extend(classes)
        element_id = tag.get("id")
        if element_id:
            attrs.append(element_id)

    source_markers_text = (source_text[:15000] + " " + " ".join(attrs)).lower()
    has_restriction_markers = any(
        marker in source_markers_text for marker in SOURCE_RESTRICTION_MARKERS
    )

    source_description = ""
    source_desc_meta = (
        source_soup.find("meta", property="og:description")
        or source_soup.find("meta", attrs={"name": "twitter:description"})
        or source_soup.find("meta", attrs={"name": "description"})
    )
    if source_desc_meta and source_desc_meta.get("content"):
        source_description = source_desc_meta.get("content").strip().lower()

    ratio = (extracted_len / source_len) if source_len > 0 else 0.0

    has_schema_paywall = False
    for script in source_soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue

        def _walk(node):
            if isinstance(node, dict):
                access_flag = node.get("isAccessibleForFree")
                if isinstance(access_flag, bool) and access_flag is False:
                    return True
                if isinstance(access_flag, str) and access_flag.strip().lower() in {
                    "false",
                    "no",
                    "0",
                }:
                    return True
                return any(_walk(v) for v in node.values())
            if isinstance(node, list):
                return any(_walk(v) for v in node)
            return False

        if _walk(data):
            has_schema_paywall = True
            break

    content_tier_meta = source_soup.find(
        "meta",
        attrs={"name": lambda value: value and "content_tier" in value.lower()},
    )
    has_paid_tier_meta = bool(
        content_tier_meta
        and any(
            token in (content_tier_meta.get("content") or "").lower()
            for token in ["paid", "premium", "subscriber", "metered"]
        )
    )

    if has_restriction_markers and extracted_len < 2200 and extracted_paragraphs <= 5:
        return "Content appears restricted by a paywall or source access controls"

    if (
        (has_schema_paywall or has_paid_tier_meta)
        and extracted_len < 2200
        and extracted_paragraphs <= 5
    ):
        return "Content appears restricted by a paywall or source access controls"

    # If the page is paywalled but we got substantial content (user was authenticated
    # via extension), skip ratio-based checks — the source HTML is the unauthenticated
    # version so ratio comparisons are meaningless.
    if has_schema_paywall or has_paid_tier_meta:
        return None

    # Some restricted pages collapse to hero image + caption with no real body.
    if (
        extracted_has_media
        and extracted_paragraphs == 0
        and extracted_len < 900
        and source_len >= 1500
    ):
        return "Limited content extracted from source page"

    # Teaser-only extraction: extracted content mostly mirrors the metadata
    # description with little additional body text.
    if source_description:
        desc_norm = re.sub(r"\s+", " ", source_description).strip()
        body_norm = re.sub(r"\s+", " ", extracted_text.lower()).strip()
        desc_prefix = desc_norm[: min(200, len(desc_norm))]
        if (
            len(desc_prefix) >= 80
            and desc_prefix in body_norm
            and extracted_len <= max(1200, int(len(desc_norm) * 3.2))
            and extracted_paragraphs <= 4
        ):
            return "Limited content extracted from source page"

    # Structural truncation signal: source appears to have substantial article
    # paragraph content, but extracted output contains only a small subset.
    if (
        (source_paragraphs >= 6 or source_len >= 5000)
        and extracted_paragraphs <= 4
        and ratio < 0.4
        and extracted_len < 1800
    ):
        return "Limited content extracted from source page"

    # Generic limited extraction: source page has substantial visible text,
    # but extracted article body is tiny and structurally very short.
    if (
        source_len >= 4000
        and extracted_len < 900
        and ratio < 0.18
        and extracted_paragraphs <= 3
    ):
        return "Limited content extracted from source page"

    return None


def _detect_limited_extension_content_reason(
    html_text: str,
    description: str | None,
) -> str | None:
    """Detect likely teaser-only extraction when source HTML is unavailable."""
    extracted_soup = BeautifulSoup(html_text, "html.parser")
    extracted_text = extracted_soup.get_text(" ", strip=True)
    extracted_text_lower = extracted_text.lower()

    word_count = len(extracted_text.split())
    extracted_paragraphs = sum(
        1
        for p in extracted_soup.find_all("p")
        if len(p.get_text(" ", strip=True)) >= 45
    )

    if any(marker in extracted_text_lower for marker in SOURCE_RESTRICTION_MARKERS):
        if word_count <= 450 and extracted_paragraphs <= 5:
            return "Content appears restricted by a paywall or source access controls"

    if description:
        desc_norm = re.sub(r"\s+", " ", description.strip().lower())
        body_norm = re.sub(r"\s+", " ", extracted_text_lower)
        desc_prefix = desc_norm[: min(180, len(desc_norm))]
        if (
            len(desc_prefix) >= 80
            and desc_prefix in body_norm
            and word_count <= max(260, int(len(desc_norm.split()) * 3.2))
            and extracted_paragraphs <= 4
        ):
            return "Limited content extracted from source page"

    # Truncation ellipsis in short content is a strong paywall signal regardless of domain.
    if (
        ("..." in extracted_text or "…" in extracted_text)
        and word_count <= 500
        and extracted_paragraphs <= 6
    ):
        return "Limited content extracted from source page"

    # Short content with no paywall markers but structurally thin — likely teaser.
    if word_count <= 150 and extracted_paragraphs <= 2:
        return "Limited content extracted from source page"

    return None


def _is_pdf_url(url: str) -> bool:
    """Heuristic: URL path ends in .pdf or contains /pdf/."""
    url_lower = url.lower().split("?")[0]
    return url_lower.endswith(".pdf") or "/pdf/" in url_lower


def _detect_content_type(url: str, response_headers: dict) -> str:
    """Detect content type from response Content-Type header or URL."""
    ct = response_headers.get("content-type", "").lower()
    if "application/pdf" in ct:
        return "pdf"
    if _is_pdf_url(url):
        return "pdf"
    if "video" in ct:
        return "video"

    try:
        domain = urlparse(url).hostname or ""
        domain = domain.lower().removeprefix("www.")
        if any(domain == d or domain.endswith(f".{d}") for d in VIDEO_DOMAINS):
            return "video"
        if any(domain == d or domain.endswith(f".{d}") for d in SOCIAL_MEDIA_DOMAINS):
            return "social"
    except Exception:
        pass

    return "article"


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.extraction.extract_metadata",
)
def extract_metadata(self, item_id: str):
    """
    Background task: download URL, extract content, save HTML to DB.

    For PDFs: uses YOLO layout detection pipeline (extract_with_yolo).
    For articles: Phase 1 fetches OG metadata + thumbnail, then triggers
                  extract_full_content for full HTML with images/links.
    """
    item = self.db.query(ContentItem).filter(ContentItem.id == item_id).first()
    if not item:
        logger.error(f"ContentItem {item_id} not found")
        return

    # Extension path: full text already provided — only fetch OG metadata if needed
    if item.full_text and len(item.full_text.strip()) > 100:
        # If the extension already sent all key metadata, skip the HTTP fetch entirely.
        # This avoids 403 errors on paywalled/rate-limited sites (e.g. NYT, Nature).
        has_metadata = bool(item.thumbnail_url and item.description)
        extension_limited_reason = None
        if not has_metadata:
            logger.info(
                f"Extension-submitted item {item_id} — fetching OG metadata only, skipping extraction pipeline"
            )
            try:
                request_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                }
                resp = requests.get(
                    item.original_url, timeout=15, headers=request_headers
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.content, "html.parser")
                metadata = _extract_page_metadata(soup, item.original_url)
                # Only fill fields that aren't already set by the extension
                if metadata:
                    if metadata.get("title") and (
                        not item.title or "youtu" not in item.original_url
                    ):
                        item.title = metadata["title"]
                    if metadata.get("description") and not item.description:
                        item.description = metadata["description"]
                    if metadata.get("thumbnail") and not item.thumbnail_url:
                        item.thumbnail_url = metadata["thumbnail"]
                    if metadata.get("author") and not item.author:
                        item.author = metadata["author"]
                    if metadata.get("published_date") and not item.published_date:
                        try:
                            from datetime import datetime

                            item.published_date = datetime.fromisoformat(
                                metadata["published_date"].replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass
                    if metadata.get("content_vertical"):
                        item.content_vertical = metadata["content_vertical"]
                    if metadata.get("vertical_metadata"):
                        item.vertical_metadata = metadata["vertical_metadata"]

                item.content_type = _detect_content_type(
                    item.original_url, dict(resp.headers)
                )
                if item.content_type == "article":
                    extension_limited_reason = _detect_limited_extraction_reason(
                        resp.content,
                        item.full_text,
                    )
            except Exception as exc:
                logger.warning(
                    f"Could not fetch OG metadata for {item.original_url}: {exc}"
                )
        else:
            logger.info(
                f"Extension-submitted item {item_id} — metadata already present, fetching page for extraction check"
            )
            item.content_type = _detect_content_type(item.original_url, {})
            if item.content_type == "article":
                try:
                    request_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    }
                    resp = requests.get(
                        item.original_url, timeout=15, headers=request_headers
                    )
                    resp.raise_for_status()
                    extension_limited_reason = _detect_limited_extraction_reason(
                        resp.content,
                        item.full_text,
                    )
                except Exception as exc:
                    logger.warning(
                        f"Could not fetch page for extraction check on {item.original_url}: {exc}"
                    )

        if item.content_type == "article":
            item.processing_error = (
                extension_limited_reason
                or _detect_limited_extension_content_reason(
                    item.full_text,
                    item.description,
                )
            )
        # Status stays "completed" as set by the API handler
        self.db.commit()
        return

    url = item.original_url
    logger.info(f"Extracting content for {url}")

    try:
        item.processing_status = "processing"
        self.db.commit()

        request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        resp = requests.get(url, timeout=30, headers=request_headers)
        resp.raise_for_status()

        content_type = _detect_content_type(url, dict(resp.headers))
        item.content_type = content_type

        if content_type == "pdf":
            _process_pdf(item, resp.content, url)
            item.processing_status = "completed"
            self.db.commit()
            logger.info(f"PDF extraction complete for {url}")
            generate_embedding.delay(str(item.id))
        else:
            # Phase 1: extract metadata + thumbnail from OG tags immediately
            soup = BeautifulSoup(resp.content, "html.parser")
            metadata = _extract_page_metadata(soup, url)
            item.title = metadata.get("title")
            item.description = metadata.get("description")
            item.thumbnail_url = metadata.get("thumbnail")
            item.author = metadata.get("author")
            if metadata.get("published_date"):
                try:
                    from dateutil import parser as dateparser

                    item.published_date = dateparser.parse(metadata["published_date"])
                except Exception:
                    pass
            item.content_vertical = metadata.get("content_vertical")
            item.vertical_metadata = metadata.get("vertical_metadata")
            # processing_status stays "processing" — full content not yet extracted
            self.db.commit()
            logger.info(f"Metadata extracted for {url}, queuing full content")
            # Phase 2: extract full text + images as a separate task
            extract_full_content.delay(item_id)

        return {"item_id": item_id, "status": "ok"}

    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code == 401:
            logger.error(f"Authorization required (401) for {url}")
            item.processing_status = "failed"
            item.processing_error = (
                "Authorization required (401) - Source site requires login/subscription"
            )
            self.db.commit()
            return
        # 403 = permanent block, don't retry
        if status_code == 403:
            logger.error(f"Access forbidden (403) for {url}")
            item.processing_status = "failed"
            item.processing_error = "Access forbidden (403) - Site blocks bots"
            self.db.commit()
            return
        logger.error(f"HTTP {status_code} fetching {url}")
        item.processing_status = "failed"
        item.processing_error = f"HTTP {status_code}: {str(exc)}"
        self.db.commit()
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
    except requests.Timeout:
        logger.error(f"Timeout fetching {url}")
        item.processing_status = "failed"
        item.processing_error = "Request timed out"
        self.db.commit()
        raise self.retry(
            exc=Exception("Timeout"), countdown=60 * (2**self.request.retries)
        )
    except Exception as exc:
        logger.error(f"Extraction failed for {url}: {exc}", exc_info=True)
        item.processing_status = "failed"
        item.processing_error = str(exc)
        self.db.commit()


@celery_app.task(bind=True, base=DatabaseTask, max_retries=2)
def extract_full_content(self, item_id: str):
    """
    Phase 2: download URL again and extract full article HTML with images and links.
    Uses trafilatura XML → xml_to_html pipeline with image context matching.
    Triggers embedding generation on success.
    """
    item = self.db.query(ContentItem).filter(ContentItem.id == item_id).first()
    if not item:
        logger.error(f"ContentItem {item_id} not found")
        return

    # Skip extraction if pre-extracted HTML is already present (e.g. from browser extension)
    if item.full_text and len(item.full_text.strip()) > 100:
        logger.info(
            f"Skipping extraction for {item_id} — pre-extracted HTML already present"
        )
        generate_embedding.delay(item_id)
        return

    if not item.title:
        item.title = _extract_domain_from_url(item.original_url)

    logger.info(f"Extracting full content for {item.original_url}")

    try:
        request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        resp = requests.get(item.original_url, timeout=30, headers=request_headers)
        resp.raise_for_status()
    except requests.Timeout:
        logger.warning(f"Timeout fetching full content for {item.original_url}")
        item.processing_error = "Request timed out"
        item.processing_status = "completed"  # Graceful: metadata already saved
        self.db.commit()
        return
    except requests.RequestException as exc:
        logger.warning(f"Request failed for full content {item.original_url}: {exc}")
        item.processing_error = f"Request error: {str(exc)[:200]}"
        item.processing_status = "completed"  # Graceful: metadata already saved
        self.db.commit()
        return

    downloaded = resp.content

    # Try trafilatura XML extraction (preserves structure, images, links)
    xml_content = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=True,
        include_images=True,
        include_links=True,
        output_format="xml",
        no_fallback=False,
    )

    html_text = None
    if xml_content:
        logger.debug(f"XML extracted ({len(xml_content)} chars), converting to HTML")
        html_text = xml_to_html(xml_content, original_html=downloaded)

    # Fallback: plain text → paragraphs
    if not html_text or len(html_text.strip()) < 100:
        logger.info(
            f"XML insufficient, falling back to plain text for {item.original_url}"
        )
        plain_text = trafilatura.extract(
            downloaded, include_comments=False, no_fallback=False
        )
        if plain_text:
            html_text = _text_to_html_paragraphs(plain_text)

    if html_text and len(html_text.strip()) > 100:
        item.full_text = html_text
        plain = BeautifulSoup(html_text, "html.parser").get_text()
        words = plain.split()
        item.word_count = len(words)
        item.reading_time_minutes = max(1, round(len(words) / 200))
        if item.content_type == "article":
            item.processing_error = _detect_limited_extraction_reason(
                downloaded, html_text
            )
        else:
            item.processing_error = None
        item.processing_status = "completed"
        self.db.commit()
        logger.info(
            f"Full content extracted ({item.word_count} words) for {item.original_url}"
        )
        generate_embedding.delay(item_id)
    else:
        logger.warning(f"No substantial text extracted from {item.original_url}")
        item.processing_error = "Could not extract article text"
        item.processing_status = "completed"
        self.db.commit()


def _process_pdf(item: ContentItem, pdf_bytes: bytes, url: str):
    """Extract PDF content and populate item fields. Does not store pdf_bytes."""
    from app.tasks.extraction_implementations import extract_with_yolo
    import fitz

    html = extract_with_yolo(pdf_bytes, url)
    if not html:
        raise ValueError("PDF extraction returned empty result")

    # Extract title from the first heading in the rendered HTML — this is the
    # actual displayed title as extracted from the document body, which is more
    # reliable than the font-size heuristic used during prescan.
    # Remove it from full_text so the reader doesn't show it twice.
    html_soup = BeautifulSoup(html, "html.parser")
    html_title = None
    for tag in ("h1", "h2"):
        heading = html_soup.find(tag)
        if heading:
            text = heading.get_text(strip=True)
            if len(text) > 5:
                html_title = text
                heading.decompose()
                html = str(html_soup)
                break

    injected_abstract = None
    # BeautifulSoup may reorder attributes — match either order
    desc_match = re.search(
        r'<meta name="extraction-description" content="([^"]+)">', html
    ) or re.search(r'<meta content="([^"]+)" name="extraction-description"', html)
    if desc_match:
        injected_abstract = desc_match.group(1).replace("&quot;", '"')

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        meta = doc.metadata
        doc.close()

        # Title priority: heading from extracted HTML > PDF metadata > URL fallback
        if html_title:
            item.title = html_title
        else:
            item.title = (meta.get("title") or "").strip() or _title_from_url(url)

        if injected_abstract:
            item.description = injected_abstract

        author = (meta.get("author") or "").strip()
        item.author = author or None
    except Exception:
        item.title = html_title or _title_from_url(url)
        if injected_abstract:
            item.description = injected_abstract

    # Strip author bylines and abstract from HTML body — both are shown in the
    # Reader header, so displaying them again in the body duplicates them.
    body_soup = BeautifulSoup(html, "html.parser")

    # Collect direct children of the first .page div in order — this is where
    # the title, author lines, abstract heading, and abstract paragraph all live.
    page_div = body_soup.find("div", class_="page")
    page_blocks = list(page_div.children) if page_div else []
    page_blocks = [b for b in page_blocks if hasattr(b, "name") and b.name]

    # ── Author removal ────────────────────────────────────────────────────────
    # The title <h1> was already stripped above. Walk the remaining front-matter
    # blocks: remove short <p> tags (author names, affiliations, junk) and short
    # junk headings (e.g. arXiv IDs) until we hit the "abstract" heading/paragraph
    # or a long paragraph (>= 300 chars) that isn't an inline abstract.
    for block in page_blocks:
        tag = block.name
        text = block.get_text(strip=True)
        # Hit a standalone "Abstract" heading — done with author area
        if text.lower() == "abstract":
            break
        # Long paragraph — could be abstract (inline "ABSTRACT:" label) or body; stop either way
        if tag == "p" and len(text) >= 300:
            break
        # Short paragraph = author/affiliation/junk line, remove
        if tag == "p" and len(text) < 300:
            block.decompose()
        # Short heading that isn't a section (e.g. arXiv IDs injected as h1)
        if tag in ("h1", "h2", "h3") and len(text) < 60 and text.lower() != "abstract":
            block.decompose()

    # ── Abstract detection & removal ──────────────────────────────────────────
    # Two formats seen in the wild:
    #   A) Separate heading: <h1>ABSTRACT</h1> followed by <p>text...</p>
    #   B) Inline label:     <p>ABSTRACT: text...</p>  (no separate heading)
    # In both cases: extract text as description (if not set), remove from body.

    abstract_paragraph = None  # the <p> to remove

    # Format A: standalone heading
    abstract_heading = next(
        (
            h
            for h in body_soup.find_all(["h1", "h2", "h3", "h4"])
            if h.get_text(strip=True).lower() == "abstract"
        ),
        None,
    )
    if abstract_heading:
        # Walk siblings after the heading, skip junk headings, collect first real <p>
        for sibling in abstract_heading.find_next_siblings():
            if not hasattr(sibling, "name") or not sibling.name:
                continue
            stext = sibling.get_text(strip=True)
            # Hit a numbered section heading — stop
            if sibling.name in ("h1", "h2", "h3", "h4") and re.match(r"^\d", stext):
                break
            # Short junk heading (e.g. arXiv ID) — remove and keep looking
            if sibling.name in ("h1", "h2", "h3") and len(stext) < 60:
                sibling.decompose()
                continue
            if sibling.name == "p" and len(stext) > 50:
                abstract_paragraph = sibling
                break
        abstract_heading.decompose()

    # Format B: inline "ABSTRACT:" label inside a <p> (no separate heading)
    if not abstract_paragraph:
        for p in body_soup.find_all("p"):
            ptext = p.get_text(strip=True)
            if (
                re.match(r"^abstract\s*[:\-]?\s*\S", ptext, re.IGNORECASE)
                and len(ptext) > 100
            ):
                abstract_paragraph = p
                break

    if abstract_paragraph:
        raw = abstract_paragraph.get_text(strip=True)
        # Strip inline "ABSTRACT:" label from stored description
        desc_text = re.sub(
            r"^abstract\s*[:\-]?\s*", "", raw, flags=re.IGNORECASE
        ).strip()
        if not item.description:
            item.description = desc_text
        abstract_paragraph.decompose()

    # ── Thumbnail from first figure image ─────────────────────────────────────
    # PDFs have no og:image. Use the first figure image as thumbnail and remove
    # it from the body (the reader shows the thumbnail separately in the header).
    if not item.thumbnail_url:
        first_figure = body_soup.find("div", class_="figure-block")
        if first_figure:
            first_img = first_figure.find("img")
            if first_img and first_img.get("src", "").startswith("data:"):
                item.thumbnail_url = first_img["src"]
                first_figure.decompose()

    item.full_text = str(body_soup)

    text_only = re.sub(r"<[^>]+>", " ", item.full_text)
    words = len(text_only.split())
    item.word_count = words
    item.reading_time_minutes = max(1, words // 200)

    # Build structured metadata response for content_vertical and vertical_metadata
    item.content_vertical = "academic"
    item.vertical_metadata = {
        "source": "pdf",
        "is_academic": True,
    }


def _extract_page_metadata(soup: BeautifulSoup, url: str) -> dict:
    """
    Extract metadata from HTML using Open Graph tags with intelligent fallbacks.
    Priority: og: tags > twitter: tags > standard meta > fallback
    """
    metadata = {}

    # Title
    og_title = soup.find("meta", property="og:title")
    twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
    title_tag = soup.find("title")
    if og_title and og_title.get("content"):
        metadata["title"] = og_title["content"]
    elif twitter_title and twitter_title.get("content"):
        metadata["title"] = twitter_title["content"]
    elif title_tag and title_tag.string:
        metadata["title"] = title_tag.string.strip()
    else:
        metadata["title"] = _title_from_url(url)

    # Description
    og_desc = soup.find("meta", property="og:description")
    twitter_desc = soup.find("meta", attrs={"name": "twitter:description"})
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if og_desc and og_desc.get("content"):
        metadata["description"] = og_desc["content"]
    elif twitter_desc and twitter_desc.get("content"):
        metadata["description"] = twitter_desc["content"]
    elif meta_desc and meta_desc.get("content"):
        metadata["description"] = meta_desc["content"]

    thumbnail = _extract_thumbnail_url(soup, url)
    if thumbnail:
        metadata["thumbnail"] = thumbnail

    # Author — collect all article:author tags (handles multiple authors)
    author_metas = soup.find_all("meta", property="article:author")
    if not author_metas:
        author_metas = soup.find_all("meta", attrs={"name": "author"})
    author_names = []
    for m in author_metas:
        val = (m.get("content") or "").strip()
        if not val:
            continue
        # Skip if the value looks like a URL (profile page link, not a name)
        if (
            val.startswith("http://")
            or val.startswith("https://")
            or val.startswith("/")
        ):
            continue
        author_names.append(val)
    # Also try JSON-LD for structured author data
    if not author_names:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                # Handle both single object and list
                items = data if isinstance(data, list) else [data]
                for obj in items:
                    author_field = obj.get("author") or obj.get("creator")
                    if not author_field:
                        continue
                    if isinstance(author_field, str) and author_field:
                        author_names.append(author_field)
                    elif isinstance(author_field, dict):
                        name = author_field.get("name", "")
                        if name:
                            author_names.append(name)
                    elif isinstance(author_field, list):
                        for a in author_field:
                            name = a.get("name", "") if isinstance(a, dict) else str(a)
                            if name:
                                author_names.append(name)
                if author_names:
                    break
            except Exception:
                pass
    if author_names:
        metadata["author"] = ", ".join(
            dict.fromkeys(author_names)
        )  # deduplicate, preserve order

    # Published date
    pub_meta = (
        soup.find("meta", property="article:published_time")
        or soup.find("meta", property="datePublished")
        or soup.find("meta", attrs={"name": "datePublished"})
        or soup.find("meta", attrs={"name": "article:published_time"})
    )
    if pub_meta and pub_meta.get("content"):
        metadata["published_date"] = pub_meta["content"].strip()

    # Academic paper detection
    content_vertical = "general"
    vertical_metadata = {}

    # 1. URL checks
    domain = _extract_domain_from_url(url).lower()
    academic_domains = [
        "arxiv.org",
        "nature.com",
        "sciencedirect.com",
        "springer.com",
        "wiley.com",
        "tandfonline.com",
        "ieeexplore.ieee.org",
        "dl.acm.org",
        "academic.oup.com",
        "journals.sagepub.com",
        "pnas.org",
        "sciencemag.org",
        "cell.com",
        "jstor.org",
        "plos.org",
        "ncbi.nlm.nih.gov/pmc",
        "semanticscholar.org",
        "biorxiv.org",
        "medrxiv.org",
        "ssrn.com",
    ]
    if any(d in domain for d in academic_domains) or ".edu" in domain:
        content_vertical = "academic"
        vertical_metadata["is_academic"] = True

    # 2. Meta tag checks (Highwire Press tags, PRISM tags, Dublin Core tags commonly used by academic publishers)
    if content_vertical != "academic":
        academic_meta_tags = [
            soup.find("meta", attrs={"name": "citation_title"}),
            soup.find("meta", attrs={"name": "citation_author"}),
            soup.find("meta", attrs={"name": "citation_journal_title"}),
            soup.find("meta", attrs={"name": "prism.publicationName"}),
            soup.find("meta", attrs={"name": "dc.Type", "content": "research-article"}),
            soup.find("meta", attrs={"name": "DC.type", "content": "Article"}),
        ]
        if any(tag is not None for tag in academic_meta_tags):
            content_vertical = "academic"
            vertical_metadata["is_academic"] = True

            # Optionally pull DOI or specific academic tags into vertical_metadata here
            doi_tag = soup.find("meta", attrs={"name": "citation_doi"}) or soup.find(
                "meta", attrs={"name": "prism.doi"}
            )
            if doi_tag and doi_tag.get("content"):
                vertical_metadata["doi"] = doi_tag["content"].strip()

            journal_tag = soup.find("meta", attrs={"name": "citation_journal_title"})
            if journal_tag and journal_tag.get("content"):
                vertical_metadata["journal"] = journal_tag["content"].strip()

    metadata["content_vertical"] = content_vertical
    metadata["vertical_metadata"] = vertical_metadata

    return metadata


def _extract_thumbnail_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """
    Extract a best-effort thumbnail URL from metadata and page content.

    Priority:
    1) OG/Twitter/meta image tags
    2) JSON-LD image fields
    3) link[rel=image_src]
    4) first usable in-content <img> in article/main/body
    """

    def normalize(candidate: str | None) -> str | None:
        if not candidate:
            return None
        value = candidate.strip()
        if not value:
            return None
        if value.startswith("data:"):
            return None

        lowered = value.lower()
        if lowered.endswith(".svg"):
            return None
        if any(token in lowered for token in ["sprite", "favicon", "icon", "avatar"]):
            return None

        return urljoin(page_url, value)

    def src_from_image_tag(image_tag) -> str | None:
        direct_src = image_tag.get("src") or image_tag.get("data-src")
        if direct_src:
            return direct_src

        srcset = image_tag.get("srcset") or image_tag.get("data-srcset")
        if not srcset:
            return None

        entries = [entry.strip() for entry in srcset.split(",") if entry.strip()]
        if not entries:
            return None

        # Prefer the last srcset entry, which is commonly the largest candidate.
        return entries[-1].split(" ")[0].strip()

    meta_selectors = [
        ("meta", {"property": "og:image:secure_url"}, "content"),
        ("meta", {"property": "og:image"}, "content"),
        ("meta", {"name": "twitter:image:src"}, "content"),
        ("meta", {"name": "twitter:image"}, "content"),
        ("meta", {"itemprop": "image"}, "content"),
    ]

    for tag_name, attrs, key in meta_selectors:
        node = soup.find(tag_name, attrs=attrs)
        candidate = normalize(node.get(key) if node else None)
        if candidate:
            return candidate

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            image_field = obj.get("image")
            if isinstance(image_field, str):
                candidate = normalize(image_field)
                if candidate:
                    return candidate
            elif isinstance(image_field, dict):
                candidate = normalize(
                    image_field.get("url")
                    or image_field.get("contentUrl")
                    or image_field.get("@id")
                )
                if candidate:
                    return candidate
            elif isinstance(image_field, list):
                for image_item in image_field:
                    if isinstance(image_item, str):
                        candidate = normalize(image_item)
                    elif isinstance(image_item, dict):
                        candidate = normalize(
                            image_item.get("url")
                            or image_item.get("contentUrl")
                            or image_item.get("@id")
                        )
                    else:
                        candidate = None
                    if candidate:
                        return candidate

    image_link = soup.find("link", rel=lambda value: value and "image_src" in value)
    candidate = normalize(image_link.get("href") if image_link else None)
    if candidate:
        return candidate

    for container_selector in ["article", "main", "body"]:
        container = soup.find(container_selector)
        if not container:
            continue
        for image_tag in container.find_all("img"):
            candidate = normalize(src_from_image_tag(image_tag))
            if candidate:
                return candidate

    return None


def xml_to_html(xml_content: str, original_html: bytes = None) -> str:
    """
    Convert trafilatura XML output to clean HTML, preserving original header hierarchy
    and re-inserting images matched by surrounding text context.

    EXTRACTION PRINCIPLES:
    1. IGNORE: Navigation, headers, footers, sidebars - anything NOT in main content
    2. PRESERVE: All main content including paragraphs, images, lists, quotes
    3. FILTER DUPLICATES: Skip page title/meta description appearing in content
    4. NORMALIZE HEADERS: Map to H2-H4 range, remove title-matching headers
    5. SKIP NAVIGATION: Filter nav keywords, skip headers before first substantial paragraph
    """
    from bs4 import BeautifulSoup as BS

    try:
        soup = BS(xml_content, "xml")
        main = soup.find("main") or soup

        # --- Extract original header hierarchy and image context from source HTML ---
        original_header_map = {}
        original_images = []
        page_title = None
        page_title_words = set()
        page_description = None

        if original_html:
            try:
                orig = BS(original_html, "html.parser")

                # Headers
                for h in orig.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                    text = h.get_text(strip=True).lower()
                    level = int(h.name[1])
                    if text and len(text) > 3 and text not in original_header_map:
                        original_header_map[text] = level

                # Page title / description for duplicate filtering
                title_tag = orig.find("title")
                if title_tag:
                    page_title = title_tag.get_text(strip=True).lower()
                    page_title_words = {w for w in page_title.split() if len(w) > 4}

                desc_tag = orig.find("meta", property="og:description") or orig.find(
                    "meta", attrs={"name": "description"}
                )
                if desc_tag and desc_tag.get("content"):
                    page_description = desc_tag["content"].strip().lower()

                # Images with surrounding context — deduplicated by base URL
                seen_base_urls = set()
                for img in orig.find_all("img"):
                    # CDN/lazy-loading aware: prefer data-src variants
                    src = (
                        img.get("data-src")
                        or img.get("data-original-src")
                        or img.get("data-lazy-src")
                        or img.get("src", "")
                    )
                    alt = img.get("alt", "")

                    if not src:
                        continue

                    src = src.strip()
                    if src.startswith("//"):
                        src = f"https:{src}"

                    # Deduplicate by base URL (strip query params/fragments)
                    base_url = src.split("?")[0].split("#")[0].strip()
                    if base_url in seen_base_urls:
                        continue
                    seen_base_urls.add(base_url)

                    # Skip tiny icons
                    width = img.get("width", "")
                    if width and str(width).isdigit() and int(width) < 50:
                        continue

                    # Skip icons/logos/social/video thumbnails/placeholders
                    if src and any(
                        x in src.lower()
                        for x in [
                            "icon",
                            "logo",
                            "avatar",
                            "badge",
                            "video",
                            "play",
                            "thumbnail",
                            "poster",
                            "vid",
                            "facebook",
                            "twitter",
                            "linkedin",
                            "placeholder",
                            "vid-thumbnail",
                            "video-poster",
                            "video_placeholder",
                        ]
                    ):
                        continue

                    # Get surrounding text context
                    prev_context = ""
                    curr = img
                    search_steps = 0
                    while curr and search_steps < 5:
                        prev = curr.find_previous_sibling()
                        if prev:
                            text = prev.get_text(strip=True)
                            if len(text) > 50:
                                prev_context = text[-100:]
                                break
                            curr = prev
                        else:
                            curr = curr.parent
                            search_steps += 1

                    next_context = ""
                    curr = img
                    search_steps = 0
                    while curr and search_steps < 5:
                        nxt = curr.find_next_sibling()
                        if nxt:
                            text = nxt.get_text(strip=True)
                            if len(text) > 50:
                                next_context = text[:100]
                                break
                            curr = nxt
                        else:
                            curr = curr.parent
                            search_steps += 1

                    caption = ""
                    parent = img.parent
                    if parent and parent.name == "figure":
                        figcaption = parent.find("figcaption")
                        if figcaption:
                            caption = figcaption.get_text(strip=True)

                    if prev_context or next_context:
                        original_images.append(
                            {
                                "src": src,
                                "alt": alt,
                                "caption": caption,
                                "prev_context": prev_context,
                                "next_context": next_context,
                                "inserted": False,
                            }
                        )
            except Exception as e:
                logger.warning(f"Could not analyse original HTML: {e}")

        # --- Determine header level offset ---
        use_original_hierarchy = len(original_header_map) > 0

        # --- First pass: collect and filter headers ---
        nav_keywords = [
            "search",
            "menu",
            "sign in",
            "log in",
            "login",
            "get premium",
            "subscribe",
            "navigation",
            "skip to",
            "toggle",
            "⌘",
        ]

        headers_to_process = []
        for elem in main.find_all(recursive=False):
            text = elem.get_text(strip=True)
            if not text or len(text) < 3:
                continue

            is_header = False
            text_lower = text.lower()

            if elem.name == "head":
                is_header = True
            elif elem.name == "p" and use_original_hierarchy:
                if text_lower in original_header_map:
                    is_header = True

            if not is_header:
                continue

            if any(kw in text_lower for kw in nav_keywords):
                continue

            if page_title and (
                text_lower in page_title
                or page_title in text_lower
                or any(w in text_lower for w in page_title_words)
            ):
                continue

            if use_original_hierarchy and text_lower in original_header_map:
                raw_level = original_header_map[text_lower]
            else:
                rend = elem.get("rend", "h2")
                raw_level = (
                    int(rend[1:])
                    if (rend.startswith("h") and rend[1:].isdigit())
                    else 2
                )

            headers_to_process.append((elem, text, raw_level))

        # Renormalise header levels to H2–H4
        header_level_map = {}
        if headers_to_process:
            min_h = min(h[2] for h in headers_to_process)
            max_h = max(h[2] for h in headers_to_process)
            if (max_h - min_h + 1) > 3:
                for _, _, raw in headers_to_process:
                    if raw not in header_level_map:
                        header_level_map[raw] = 2 + min(2, len(header_level_map))
            else:
                new_offset = min_h - 2
                for raw in range(min_h, max_h + 1):
                    header_level_map[raw] = raw - new_offset

        # --- Helper functions ---
        def format_image_html(img_data):
            caption_html = ""
            if img_data.get("caption"):
                caption_html = (
                    f'<figcaption style="text-align:center; font-size:0.9em; '
                    f'color:var(--color-text-muted); margin-top:0.5em; font-style:italic;">'
                    f"{img_data['caption']}</figcaption>"
                )
            return (
                f'<figure style="margin:1.5em 0; text-align:center;">'
                f'<img src="{img_data["src"]}" alt="{img_data.get("alt", "")}" '
                f'style="max-width:100%; height:auto; border-radius:4px;"/>'
                f"{caption_html}</figure>"
            )

        def process_inline_elements(element):
            result = []
            for child in element.children:
                if isinstance(child, str):
                    # Preserve all text including spaces between inline elements.
                    # Only skip completely empty strings (not whitespace-only).
                    if child:
                        result.append(str(child))
                elif child.name == "ref":
                    result.append(
                        f'<a href="{child.get("target", "#")}">{child.get_text(strip=False)}</a>'
                    )
                elif child.name == "hi":
                    rend = child.get("rend", "")
                    hi_text = child.get_text(strip=False)
                    if rend in ["#b", "b"]:
                        result.append(f"<strong>{hi_text}</strong>")
                    elif rend in ["#i", "i"]:
                        result.append(f"<em>{hi_text}</em>")
                    else:
                        result.append(hi_text)
                else:
                    result.extend(process_inline_elements(child))
            return result

        # --- Second pass: output HTML ---
        html_parts = []
        header_index = 0

        BLOCK_TAGS = {"p", "head", "list", "quote", "graphic", "table", "figure"}

        def render_inline_node(node) -> str:
            """Render a bare-text NavigableString, <ref>, or <hi> as inline HTML."""
            from bs4 import NavigableString

            if isinstance(node, NavigableString):
                return str(node)
            if not hasattr(node, "name"):
                return ""
            if node.name == "ref":
                return f'<a href="{node.get("target", "#")}">{node.get_text(strip=False)}</a>'
            if node.name == "hi":
                rend = node.get("rend", "")
                t = node.get_text(strip=False)
                if rend in ["#b", "b"]:
                    return f"<strong>{t}</strong>"
                if rend in ["#i", "i"]:
                    return f"<em>{t}</em>"
                return t
            return ""

        # Iterate main.children (includes NavigableStrings) so we don't miss bare text
        # between <ref> siblings that trafilatura emits outside <p> tags.
        children = list(main.children)
        i = 0
        while i < len(children):
            node = children[i]
            from bs4 import NavigableString, Tag

            # Skip pure-whitespace text nodes at the top level
            if isinstance(node, NavigableString):
                i += 1
                continue

            if not isinstance(node, Tag):
                i += 1
                continue

            elem = node

            # Priority: check if this element is a pre-approved header
            if header_index < len(headers_to_process):
                stored_elem, stored_text, raw_level = headers_to_process[header_index]
                if stored_elem == elem:
                    new_level = max(2, min(4, header_level_map.get(raw_level, 2)))
                    # Insert images whose next_context matches this header
                    for img in original_images:
                        if not img["inserted"] and img["next_context"]:
                            clean_next = (
                                img["next_context"].replace("\n", " ").strip()[:50]
                            )
                            if (
                                len(clean_next) > 10
                                and clean_next in stored_text.replace("\n", " ").strip()
                            ):
                                html_parts.append(format_image_html(img))
                                img["inserted"] = True
                    html_parts.append(f"<h{new_level}>{stored_text}</h{new_level}>")
                    header_index += 1
                    i += 1
                    continue

            if elem.name == "p":
                # Don't strip yet — trailing spaces before inline siblings matter
                p_html = "".join(process_inline_elements(elem))

                # Absorb any immediately following inline siblings:
                # bare text (NavigableString), <ref>, <hi> — until the next block tag.
                # Trafilatura often emits these as top-level siblings of <p> rather
                # than as children inside it, causing text loss.
                j = i + 1
                while j < len(children):
                    sib = children[j]
                    sib_name = getattr(sib, "name", None)
                    if sib_name in BLOCK_TAGS:
                        break
                    inline = render_inline_node(sib)
                    p_html += inline
                    j += 1
                i = j  # advance past all consumed siblings

                # Strip only outer whitespace now that all inline content is assembled
                p_html = p_html.strip()
                # Ensure space before/after <a> when trafilatura omits it
                p_html = re.sub(r"([^\s>])(<a\s)", r"\1 \2", p_html)
                p_html = re.sub(r"(</a>)([^\s<.,;:!?])", r"\1 \2", p_html)
                # p_text used for image context matching — strip HTML tags
                from bs4 import BeautifulSoup as _BS

                p_text = _BS(f"<p>{p_html}</p>", "html.parser").get_text()
                if not p_html:
                    continue

                # Skip short credit/attribution paragraphs
                clean_p_text = p_text.strip().lower()
                if len(clean_p_text) < 20 and any(
                    x in clean_p_text
                    for x in [
                        "cnn",
                        "social media",
                        "instagram",
                        "twitter",
                        "facebook",
                        "photo:",
                        "credit:",
                        "image source",
                    ]
                ):
                    continue

                # Skip if matches meta description
                if page_description:
                    para_text = BS(f"<p>{p_html}</p>", "html.parser").get_text().lower()
                    if page_description in para_text or para_text == page_description:
                        continue

                # Insert images whose next_context matches start of this paragraph
                for img in original_images:
                    if not img["inserted"] and img["next_context"]:
                        clean_next = img["next_context"].replace("\n", " ").strip()[:50]
                        if (
                            len(clean_next) > 20
                            and clean_next in p_text.replace("\n", " ").strip()
                        ):
                            html_parts.append(format_image_html(img))
                            img["inserted"] = True
                            logger.info(
                                f"Re-inserted image (before match) {img['src'][:30]}..."
                            )

                html_parts.append(f"<p>{p_html}</p>")

                # Insert images whose prev_context matches end of this paragraph
                for img in original_images:
                    if not img["inserted"] and img["prev_context"]:
                        clean_prev = (
                            img["prev_context"].replace("\n", " ").strip()[-50:]
                        )
                        if (
                            len(clean_prev) > 20
                            and clean_prev in p_text.replace("\n", " ").strip()
                        ):
                            html_parts.append(format_image_html(img))
                            img["inserted"] = True
                            logger.info(
                                f"Re-inserted image (after match) {img['src'][:30]}..."
                            )

            elif elem.name == "list":
                items = elem.find_all("item")
                if items:
                    is_ordered = (
                        elem.get("type", "") == "ordered"
                        or "ordered" in elem.get("rend", "").lower()
                    )
                    tag = "ol" if is_ordered else "ul"
                    html_parts.append(f"<{tag}>")
                    for item in items:
                        text = item.get_text(strip=True)
                        if text:
                            html_parts.append(f"<li>{text}</li>")
                    html_parts.append(f"</{tag}>")
                i += 1

            elif elem.name == "quote":
                text = elem.get_text(strip=True)
                if text:
                    html_parts.append(f"<blockquote>{text}</blockquote>")
                i += 1

            elif elem.name == "graphic":
                src = elem.get("src")
                if src:
                    # Use base URL matching to avoid duplicating context-matched images
                    src_base = src.split("?")[0].split("#")[0].strip()
                    img_already_inserted = False
                    for img in original_images:
                        target_base = img["src"].split("?")[0].split("#")[0].strip()
                        if target_base == src_base:
                            if img["inserted"]:
                                img_already_inserted = True
                            img["inserted"] = True

                    if not img_already_inserted:
                        alt = elem.get("alt", "")
                        caption = ""
                        nxt = elem.find_next_sibling()
                        if (
                            nxt
                            and nxt.name in ["p", "hi"]
                            and len(nxt.get_text(strip=True)) < 200
                        ):
                            caption = nxt.get_text(strip=True)
                        html_parts.append(
                            format_image_html(
                                {"src": src, "alt": alt, "caption": caption}
                            )
                        )
                i += 1

            else:
                i += 1

        if html_parts:
            # Post-process: remove sequential duplicate images
            final_parts = []
            seen_last_img_base = None
            for part in html_parts:
                if '<img src="' in part:
                    try:
                        current_src = part.split('<img src="')[1].split('"')[0]
                        current_base = current_src.split("?")[0].split("#")[0].strip()
                        if current_base == seen_last_img_base:
                            logger.info(
                                f"Filtered sequential duplicate image: {current_base}"
                            )
                            continue
                        seen_last_img_base = current_base
                    except Exception:
                        pass
                else:
                    # Reset after a substantial paragraph so same image can appear in new section
                    if part.startswith("<p>") and len(part) > 100:
                        seen_last_img_base = None
                final_parts.append(part)
            return "\n".join(final_parts)
        else:
            return _text_to_html_paragraphs(soup.get_text(strip=False))

    except Exception as e:
        logger.warning(f"xml_to_html failed: {e}")
        try:
            from bs4 import BeautifulSoup as BS

            text = BS(xml_content, "xml").get_text(strip=False)
            if text:
                return _text_to_html_paragraphs(text)
        except Exception:
            pass
        return _text_to_html_paragraphs(xml_content)


def _text_to_html_paragraphs(text: str) -> str:
    """Convert plain text to HTML with paragraph breaks."""
    paragraphs = text.split("\n\n")
    return "\n".join(
        f"<p>{para.strip().replace(chr(10), '<br>')}</p>"
        for para in paragraphs
        if para.strip()
    )


def _extract_domain_from_url(url: str) -> str:
    domain = urlparse(url).netloc
    return domain.replace("www.", "").capitalize()


def _title_from_url(url: str) -> str:
    """Derive a readable title from URL stem as fallback."""
    from pathlib import Path

    path = url.split("?")[0].rstrip("/")
    name = Path(path).stem
    return name.replace("-", " ").replace("_", " ").title() or url


# Keep these for any direct (non-Celery) callers
def extract_pdf_content(pdf_bytes: bytes, url: str = "") -> str:
    from app.tasks.extraction_implementations import extract_with_yolo

    return extract_with_yolo(pdf_bytes, url)


def extract_pdf_metadata(pdf_bytes: bytes, url: str) -> dict:
    return {"content_type": "pdf", "title": "PDF Document"}
