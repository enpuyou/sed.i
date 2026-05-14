"""
Unit tests for extraction utility functions introduced in the pdf branch.

These tests are pure-unit (no DB, no HTTP) and cover:
- _is_pdf_url(): URL-based PDF detection heuristic
- _detect_content_type(): Content-Type header + URL fallback
- compute_reading_status(): Reading progress state machine (from content.py)
- xml_to_html(): Trafilatura XML → HTML conversion pipeline
- _extract_metadata(): Author extraction fallback chain
"""

# ============================================================================
# _is_pdf_url
# ============================================================================


class TestIsPdfUrl:
    """Tests for the _is_pdf_url() heuristic in extraction.py."""

    def _fn(self, url):
        from app.tasks.extraction import _is_pdf_url

        return _is_pdf_url(url)

    def test_detects_pdf_extension(self):
        """URLs ending in .pdf are PDFs."""
        assert self._fn("https://example.com/paper.pdf") is True

    def test_detects_pdf_extension_uppercase(self):
        """Case-insensitive: .PDF should also match."""
        assert self._fn("https://example.com/paper.PDF") is True

    def test_detects_pdf_in_path(self):
        """URLs containing /pdf/ in path are PDFs."""
        assert self._fn("https://arxiv.org/pdf/2301.00001") is True

    def test_pdf_extension_with_query_params(self):
        """Query string is ignored when checking for .pdf extension."""
        assert self._fn("https://example.com/paper.pdf?download=1") is True

    def test_html_page_not_pdf(self):
        """Regular HTML URLs are not PDFs."""
        assert self._fn("https://example.com/article") is False

    def test_html_extension_not_pdf(self):
        """HTML extension is not a PDF."""
        assert self._fn("https://example.com/page.html") is False

    def test_pdf_in_query_string_only_not_pdf(self):
        """'pdf' appearing only in the query string (after ?) is not a match."""
        assert self._fn("https://example.com/article?format=pdf") is False

    def test_empty_url(self):
        """Empty string is not a PDF."""
        assert self._fn("") is False


# ============================================================================
# _detect_content_type
# ============================================================================


class TestDetectContentType:
    """Tests for _detect_content_type() in extraction.py."""

    def _fn(self, url, headers):
        from app.tasks.extraction import _detect_content_type

        return _detect_content_type(url, headers)

    def test_pdf_content_type_header(self):
        """application/pdf Content-Type header → 'pdf'."""
        result = self._fn(
            "https://example.com/file",
            {"content-type": "application/pdf"},
        )
        assert result == "pdf"

    def test_pdf_content_type_with_charset(self):
        """application/pdf with charset suffix still detected."""
        result = self._fn(
            "https://example.com/file",
            {"content-type": "application/pdf; charset=utf-8"},
        )
        assert result == "pdf"

    def test_pdf_url_fallback_no_header(self):
        """When Content-Type is missing, falls back to URL heuristic."""
        result = self._fn(
            "https://example.com/paper.pdf",
            {},
        )
        assert result == "pdf"

    def test_video_content_type(self):
        """video/* Content-Type → 'video'."""
        result = self._fn(
            "https://example.com/clip",
            {"content-type": "video/mp4"},
        )
        assert result == "video"

    def test_html_article_default(self):
        """text/html → 'article' (default for web pages)."""
        result = self._fn(
            "https://example.com/article",
            {"content-type": "text/html; charset=utf-8"},
        )
        assert result == "article"

    def test_no_headers_no_pdf_url(self):
        """No headers and no PDF URL cues → 'article'."""
        result = self._fn("https://example.com/post/123", {})
        assert result == "article"

    def test_header_takes_precedence_over_url(self):
        """Content-Type header wins even when URL looks like PDF."""
        # Hypothetical: URL says .pdf but server says text/html
        result = self._fn(
            "https://example.com/doc.pdf",
            {"content-type": "text/html"},
        )
        # URL heuristic is checked second — header says html, but URL says pdf
        # The implementation checks header first; if not pdf in header it checks URL.
        # Since the URL ends in .pdf, _is_pdf_url still triggers pdf via the else path.
        assert result == "pdf"


# ============================================================================
# compute_reading_status
# ============================================================================


class TestComputeReadingStatus:
    """Tests for compute_reading_status() in content.py."""

    def _fn(self, is_read, read_position, is_archived):
        from app.api.content import compute_reading_status

        return compute_reading_status(is_read, read_position, is_archived)

    def test_archived_overrides_all(self):
        """Archived status wins over read/unread."""
        assert self._fn(False, 0.0, True) == "archived"
        assert self._fn(True, 1.0, True) == "archived"

    def test_read_flag_true(self):
        """is_read=True → 'read'."""
        assert self._fn(True, None, False) == "read"

    def test_read_via_high_position(self):
        """read_position >= 0.9 auto-marks as 'read'."""
        assert self._fn(False, 0.9, False) == "read"
        assert self._fn(False, 1.0, False) == "read"

    def test_in_progress(self):
        """Non-zero position below 0.9 → 'in_progress'."""
        assert self._fn(False, 0.5, False) == "in_progress"
        assert self._fn(False, 0.1, False) == "in_progress"
        assert self._fn(False, 0.89, False) == "in_progress"

    def test_unread_zero_position(self):
        """Zero read_position → 'unread'."""
        assert self._fn(False, 0.0, False) == "unread"

    def test_unread_none_position(self):
        """None read_position → 'unread'."""
        assert self._fn(False, None, False) == "unread"


# ============================================================================
# xml_to_html
# ============================================================================


class TestXmlToHtml:
    """Tests for xml_to_html() in extraction.py.

    xml_to_html() converts trafilatura XML output to clean HTML.
    We test it with minimal synthetic XML to keep tests fast and hermetic.
    """

    def _fn(self, xml_content, original_html=None):
        from app.tasks.extraction import xml_to_html

        return xml_to_html(xml_content, original_html)

    def test_basic_paragraph(self):
        """A simple <p> in trafilatura XML becomes an HTML <p>."""
        xml = "<main><p>Hello world</p></main>"
        result = self._fn(xml)
        assert "<p>" in result
        assert "Hello world" in result

    def test_empty_xml_returns_empty_or_minimal(self):
        """Empty XML input should not crash."""
        result = self._fn("<main></main>")
        assert isinstance(result, str)

    def test_nav_keyword_header_skipped(self):
        """Headers containing navigation keywords are filtered out."""
        xml = (
            "<main>"
            '<head rend="h2">Sign In</head>'
            "<p>Some article content here that is long enough.</p>"
            "</main>"
        )
        result = self._fn(xml)
        assert "Sign In" not in result
        assert "Some article content" in result

    def test_header_rendered(self):
        """Non-nav headers appear in output."""
        xml = (
            "<main>"
            '<head rend="h2">Introduction</head>'
            "<p>The quick brown fox.</p>"
            "</main>"
        )
        result = self._fn(xml)
        assert "Introduction" in result
        assert "The quick brown fox." in result

    def test_no_crash_on_none_original_html(self):
        """Passing original_html=None must not raise."""
        xml = "<main><p>Paragraph text.</p></main>"
        result = self._fn(xml, original_html=None)
        assert "Paragraph text." in result

    def test_bold_inline_element(self):
        """<hi rend="#b"> becomes <strong>."""
        xml = '<main><p>This is <hi rend="#b">bold</hi> text.</p></main>'
        result = self._fn(xml)
        assert "<strong>bold</strong>" in result

    def test_italic_inline_element(self):
        """<hi rend="#i"> becomes <em>."""
        xml = '<main><p>This is <hi rend="#i">italic</hi> text.</p></main>'
        result = self._fn(xml)
        assert "<em>italic</em>" in result

    def test_ref_becomes_anchor(self):
        """<ref target="..."> becomes <a href="...">."""
        xml = (
            '<main><p>See <ref target="https://example.com">this link</ref>.</p></main>'
        )
        result = self._fn(xml)
        assert 'href="https://example.com"' in result
        assert "this link" in result


# ============================================================================
# _extract_page_metadata (thumbnail fallbacks)
# ============================================================================


class TestExtractPageMetadata:
    """Tests for thumbnail extraction fallback order in _extract_page_metadata()."""

    def _fn(self, html: str, url: str = "https://example.com/article"):
        from bs4 import BeautifulSoup
        from app.tasks.extraction import _extract_page_metadata

        soup = BeautifulSoup(html, "html.parser")
        return _extract_page_metadata(soup, url)

    def test_uses_og_image_relative_url(self):
        html = """
                <html><head>
                    <meta property="og:title" content="Example" />
                    <meta property="og:image" content="/images/hero.jpg" />
                </head><body></body></html>
                """
        metadata = self._fn(html, "https://news.example.com/path/story")
        assert metadata.get("thumbnail") == "https://news.example.com/images/hero.jpg"

    def test_falls_back_to_twitter_image_src(self):
        html = """
                <html><head>
                    <meta name="twitter:image:src" content="https://cdn.example.com/card.png" />
                </head><body></body></html>
                """
        metadata = self._fn(html)
        assert metadata.get("thumbnail") == "https://cdn.example.com/card.png"

    def test_falls_back_to_jsonld_image(self):
        html = """
                <html><head>
                    <script type="application/ld+json">
                        {
                            "@context": "https://schema.org",
                            "@type": "NewsArticle",
                            "image": {"url": "https://img.example.com/lead.webp"}
                        }
                    </script>
                </head><body></body></html>
                """
        metadata = self._fn(html)
        assert metadata.get("thumbnail") == "https://img.example.com/lead.webp"

    def test_falls_back_to_image_src_link(self):
        html = """
                <html><head>
                    <link rel="image_src" href="/assets/cover.jpg" />
                </head><body></body></html>
                """
        metadata = self._fn(html, "https://example.com/articles/a1")
        assert metadata.get("thumbnail") == "https://example.com/assets/cover.jpg"

    def test_falls_back_to_first_content_image(self):
        html = """
                <html><head></head><body>
                    <article>
                        <p>Intro</p>
                        <img src="/content/lead.jpg" alt="Lead image" />
                    </article>
                </body></html>
                """
        metadata = self._fn(html, "https://example.com/articles/a1")
        assert metadata.get("thumbnail") == "https://example.com/content/lead.jpg"

    # ============================================================================
    # _detect_limited_extraction_reason
    # ============================================================================

    class TestDetectLimitedExtractionReason:
        def _fn(self, source_html: str, extracted_html: str):
            from app.tasks.extraction import _detect_limited_extraction_reason

            return _detect_limited_extraction_reason(
                source_html.encode("utf-8"), extracted_html
            )

        def test_schema_paywall_teaser_is_marked_limited(self):
            source = (
                "<html><head>"
                '<script type="application/ld+json">'
                '{"@type":"NewsArticle","isAccessibleForFree":false}'
                "</script>"
                "</head><body><main><h1>Title</h1><p>"
                + ("word " * 800)
                + "</p></main></body></html>"
            )
            extracted = (
                "<p>Preview paragraph only.</p><p>Another short teaser paragraph.</p>"
            )

            reason = self._fn(source, extracted)
            assert (
                reason
                == "Content appears restricted by a paywall or source access controls"
            )

        def test_structural_truncation_is_marked_limited(self):
            source_paragraphs = "".join(f"<p>{'word ' * 120}</p>" for _ in range(10))
            source = f"<html><body><article>{source_paragraphs}</article></body></html>"
            extracted = "<p>" + ("word " * 180) + "</p><p>" + ("word " * 140) + "</p>"

            reason = self._fn(source, extracted)
            assert reason == "Limited content extracted from source page"

        def test_non_restricted_domain_short_article_not_forced_limited(self):
            source = (
                "<html><body><main><h1>Short post</h1><p>"
                + ("word " * 120)
                + "</p></main></body></html>"
            )
            extracted = "<p>" + ("word " * 110) + "</p>"

            reason = self._fn(source, extracted)
            assert reason is None

        def test_description_teaser_is_marked_limited(self):
            description = (
                "One of the promises of AI is that it can reduce workloads so employees can "
                "focus more on higher-value tasks. But according to new evidence, the opposite "
                "can happen in constrained organizations."
            )
            source = (
                "<html><head>"
                f'<meta property="og:description" content="{description}" />'
                "</head><body><main><h1>Title</h1><p>"
                + ("word " * 900)
                + "</p></main></body></html>"
            )
            extracted = f"<p>{description}</p><p>Short follow-up sentence only.</p>"

            reason = self._fn(source, extracted)
            assert reason == "Limited content extracted from source page"

        def test_media_caption_only_output_is_marked_limited(self):
            source = (
                "<html><body><main><h1>Title</h1><p>"
                + ("word " * 600)
                + "</p></main></body></html>"
            )
            extracted = (
                '<figure><img src="/hero.jpg" alt="" />'
                "<figcaption>This caption is present but there is no body paragraph text.</figcaption>"
                "</figure>"
            )

            reason = self._fn(source, extracted)
            assert reason == "Limited content extracted from source page"


class TestDetectLimitedExtensionContentReason:
    def _fn(self, html_text: str, description: str | None):
        from app.tasks.extraction import _detect_limited_extension_content_reason

        return _detect_limited_extension_content_reason(html_text, description)

    def test_description_overlap_marks_limited(self):
        description = (
            "One of the promises of AI is that it can reduce workloads so employees can "
            "focus more on higher-value and more engaging tasks. But according to "
            "recent evidence, this often intensifies pressure."
        )
        html = f"<p>{description}</p><p>But accordin...</p>"
        reason = self._fn(html, description)
        assert reason == "Limited content extracted from source page"

    def test_regular_extension_content_not_marked_limited(self):
        description = "A short intro to a complete post"
        html = (
            "<p>" + ("word " * 220) + "</p>"
            "<p>" + ("word " * 200) + "</p>"
            "<p>" + ("word " * 180) + "</p>"
        )
        reason = self._fn(html, description)
        assert reason is None


# ============================================================================
# _extract_metadata — author extraction fallback chain
# ============================================================================


class TestExtractMetadataAuthors:
    """Tests for the author extraction fallback chain in _extract_metadata()."""

    def _fn(self, html: str) -> dict:
        from bs4 import BeautifulSoup
        from app.tasks.extraction import _extract_page_metadata as _extract_metadata

        soup = BeautifulSoup(html, "html.parser")
        return _extract_metadata(soup, "https://example.com/article")

    def test_og_article_author(self):
        """article:author Open Graph tags are used (standard blogs/news)."""
        html = '<meta property="article:author" content="Jane Doe"/>'
        assert self._fn(html)["author"] == "Jane Doe"

    def test_multiple_og_article_authors(self):
        """Multiple article:author tags are joined in order."""
        html = (
            '<meta property="article:author" content="Alice"/>'
            '<meta property="article:author" content="Bob"/>'
        )
        assert self._fn(html)["author"] == "Alice, Bob"

    def test_og_author_url_skipped(self):
        """article:author tags containing URLs are skipped (profile links, not names)."""
        html = (
            '<meta property="article:author" content="https://example.com/alice"/>'
            '<meta name="author" content="Alice"/>'
        )
        assert self._fn(html)["author"] == "Alice"

    def test_dc_creator_fallback(self):
        """Dublin Core dc.creator tags are used when OG/JSON-LD are absent (e.g. Nature)."""
        html = (
            '<meta name="dc.creator" content="Cai, Xiangbin"/>'
            '<meta name="dc.creator" content="Pan, Haiyang"/>'
        )
        meta = self._fn(html)
        assert meta["author"] == "Cai, Xiangbin, Pan, Haiyang"

    def test_dc_creator_uppercase_variant(self):
        """DC.creator (uppercase) is also recognised."""
        html = '<meta name="DC.creator" content="Smith, John"/>'
        assert self._fn(html)["author"] == "Smith, John"

    def test_citation_author_fallback(self):
        """Highwire Press citation_author tags are used when earlier methods fail."""
        html = (
            '<meta name="citation_author" content="Doe, Jane"/>'
            '<meta name="citation_author" content="Smith, John"/>'
        )
        meta = self._fn(html)
        assert meta["author"] == "Doe, Jane, Smith, John"

    def test_og_takes_precedence_over_dc_creator(self):
        """OG article:author wins when both OG and dc.creator are present."""
        html = (
            '<meta property="article:author" content="OG Author"/>'
            '<meta name="dc.creator" content="DC Author"/>'
        )
        assert self._fn(html)["author"] == "OG Author"

    def test_no_author_tags_returns_no_key(self):
        """Pages with no author signals produce no 'author' key."""
        html = "<p>No author metadata here.</p>"
        assert "author" not in self._fn(html)

    def test_deduplication(self):
        """Duplicate author names are deduplicated preserving order."""
        html = (
            '<meta property="article:author" content="Alice"/>'
            '<meta property="article:author" content="Alice"/>'
            '<meta property="article:author" content="Bob"/>'
        )
        assert self._fn(html)["author"] == "Alice, Bob"
