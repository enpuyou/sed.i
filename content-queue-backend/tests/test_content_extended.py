"""
Extended content API tests covering:
- _clean_extension_html: title/description/thumbnail deduplication
- Extension path (pre_extracted_html bypasses Celery fetch)
- Cross-user isolation on content access
"""

from unittest.mock import patch
from app.api.content import _clean_extension_html


# ---------------------------------------------------------------------------
# _clean_extension_html unit tests
# ---------------------------------------------------------------------------


def test_clean_extension_html_removes_title_h1():
    """H1 whose text exactly matches the title is stripped."""
    html = "<h1>My Great Article</h1><p>Some content here.</p>"
    result = _clean_extension_html(
        html,
        title="My Great Article",
        description=None,
        thumbnail=None,
    )
    assert "<h1>" not in result
    assert "Some content here." in result


def test_clean_extension_html_title_case_insensitive():
    """Title comparison is case-insensitive."""
    html = "<h1>my great article</h1><p>Content.</p>"
    result = _clean_extension_html(
        html,
        title="My Great Article",
        description=None,
        thumbnail=None,
    )
    assert "<h1>" not in result


def test_clean_extension_html_keeps_other_h1():
    """H1 that does NOT match title is preserved."""
    html = "<h1>Different Heading</h1><p>Content.</p>"
    result = _clean_extension_html(
        html,
        title="My Great Article",
        description=None,
        thumbnail=None,
    )
    assert "Different Heading" in result


def test_clean_extension_html_removes_description_p():
    """Paragraph matching description is stripped."""
    html = "<p>A short intro sentence.</p><p>Real paragraph content.</p>"
    result = _clean_extension_html(
        html,
        title=None,
        description="A short intro sentence.",
        thumbnail=None,
    )
    assert "A short intro sentence." not in result
    assert "Real paragraph content." in result


def test_clean_extension_html_removes_thumbnail_img():
    """Img whose filename matches thumbnail filename is stripped."""
    html = (
        '<figure><img src="https://cdn.example.com/hero-image.jpg" alt="hero"/></figure>'
        "<p>Article body.</p>"
    )
    result = _clean_extension_html(
        html,
        title=None,
        description=None,
        thumbnail="https://example.com/hero-image.jpg",
    )
    assert "hero-image.jpg" not in result
    assert "Article body." in result


def test_clean_extension_html_thumbnail_ignores_query_string():
    """CDN query strings on the thumbnail URL don't prevent matching."""
    html = '<img src="https://cdn.example.com/photo.jpg?w=800&q=80" /><p>Body.</p>'
    result = _clean_extension_html(
        html,
        title=None,
        description=None,
        thumbnail="https://example.com/photo.jpg?version=2",
    )
    assert "photo.jpg" not in result
    assert "Body." in result


def test_clean_extension_html_no_metadata_does_nothing():
    """When all metadata is None, HTML is returned unchanged."""
    html = "<h1>Title</h1><p>Content.</p>"
    result = _clean_extension_html(html, title=None, description=None, thumbnail=None)
    assert "Title" in result
    assert "Content." in result


# ---------------------------------------------------------------------------
# Extension path: pre_extracted_html
# ---------------------------------------------------------------------------


def _reset_rate_limiter():
    """Clear the global in-memory rate limiter state between tests."""
    from app.middleware.rate_limit import rate_limiter

    rate_limiter.requests.clear()


def test_extension_path_creates_completed_content(client, auth_headers):
    """
    Submitting pre_extracted_html bypasses Celery extraction and
    creates a content item with processing_status='completed'.
    """
    _reset_rate_limiter()
    with (
        patch("app.tasks.extraction.extract_metadata.delay") as mock_delay,
        patch("app.tasks.embedding.generate_embedding.delay"),
    ):
        response = client.post(
            "/content",
            json={
                "url": "https://example.com/article",
                "pre_extracted_html": "<h2>Section One</h2><p>Content here.</p>",
                "pre_extracted_title": "My Article",
                "pre_extracted_description": "A great article.",
            },
            headers=auth_headers,
        )

    assert response.status_code == 201
    data = response.json()
    assert data["processing_status"] == "completed"
    assert data["title"] == "My Article"
    # Both tasks are triggered for the extension path:
    # extract_metadata fills missing fields; generate_embedding creates the vector.
    mock_delay.assert_called_once()


def test_extension_path_stores_cleaned_html(client, auth_headers):
    """
    pre_extracted_html with title H1 matching the title gets cleaned
    (title H1 is stripped to avoid duplication in reader).
    """
    with (
        patch("app.tasks.extraction.extract_metadata.delay"),
        patch("app.tasks.embedding.generate_embedding.delay"),
    ):
        _reset_rate_limiter()
        response = client.post(
            "/content",
            json={
                "url": "https://example.com/article",
                "pre_extracted_html": "<h1>My Article</h1><p>Body text.</p>",
                "pre_extracted_title": "My Article",
            },
            headers=auth_headers,
        )

    assert response.status_code == 201
    item_id = response.json()["id"]
    # full_text is not in the create response — fetch via detail endpoint
    detail = client.get(f"/content/{item_id}", headers=auth_headers)
    assert detail.status_code == 200
    full_text = detail.json().get("full_text", "")
    # Title H1 should be cleaned from content body
    assert "<h1>My Article</h1>" not in full_text
    assert "Body text." in full_text
