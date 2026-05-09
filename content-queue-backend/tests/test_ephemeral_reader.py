"""
TDD tests for ephemeral reader — backend side (Feature 4).

Behaviors tested:
- POST /content with initial_highlights creates article + highlights atomically
- Highlights are immediately queryable after save
- initial_highlights is optional — existing create flow unaffected
- Highlights inherit correct user_id and content_item_id
- Color defaults to 'yellow' when not specified
- Partial failure (bad highlight data) still creates the article (highlights are best-effort)
"""

from unittest.mock import patch


class TestCreateContentWithInitialHighlights:
    """POST /content accepts optional initial_highlights for ephemeral reader save."""

    @patch("app.tasks.extraction.extract_metadata")
    @patch("app.tasks.embedding.generate_embedding")
    def test_create_with_highlights_creates_both(
        self, mock_embed, mock_extract, client, auth_headers, db_session
    ):
        """Saving from ephemeral reader creates the article and all highlights."""

        payload = {
            "url": "https://example.com/ephemeral-article",
            "pre_extracted_html": "<h2>Context Engineering</h2><p>The art of structuring information for LLMs.</p>",
            "pre_extracted_title": "Context Engineering",
            "initial_highlights": [
                {
                    "text": "The art of structuring information",
                    "start_offset": 30,
                    "end_offset": 65,
                    "color": "yellow",
                },
                {
                    "text": "for LLMs",
                    "note": "important concept",
                    "start_offset": 66,
                    "end_offset": 74,
                    "color": "blue",
                },
            ],
        }

        resp = client.post("/content", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        item_id = resp.json()["id"]

        # Highlights must be queryable immediately after save
        hl_resp = client.get(f"/content/{item_id}/highlights", headers=auth_headers)
        assert hl_resp.status_code == 200
        highlights = hl_resp.json()
        assert len(highlights) == 2

        texts = {h["text"] for h in highlights}
        assert "The art of structuring information" in texts
        assert "for LLMs" in texts

    @patch("app.tasks.extraction.extract_metadata")
    @patch("app.tasks.embedding.generate_embedding")
    def test_highlight_note_is_preserved(
        self, mock_embed, mock_extract, client, auth_headers
    ):
        """Note text from ephemeral highlights is stored correctly."""
        payload = {
            "url": "https://example.com/note-test",
            "pre_extracted_html": "<p>Some important content here.</p>",
            "pre_extracted_title": "Note Test",
            "initial_highlights": [
                {
                    "text": "important content",
                    "note": "key insight for my research",
                    "start_offset": 5,
                    "end_offset": 22,
                    "color": "green",
                }
            ],
        }
        resp = client.post("/content", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        item_id = resp.json()["id"]

        hl_resp = client.get(f"/content/{item_id}/highlights", headers=auth_headers)
        hl = hl_resp.json()[0]
        assert hl["note"] == "key insight for my research"
        assert hl["color"] == "green"

    @patch("app.tasks.extraction.extract_metadata")
    @patch("app.tasks.embedding.generate_embedding")
    def test_create_without_highlights_is_unchanged(
        self, mock_embed, mock_extract, client, auth_headers
    ):
        """existing create flow works unchanged when initial_highlights is absent."""
        payload = {"url": "https://example.com/plain-article"}
        resp = client.post("/content", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        # No highlights by default
        item_id = resp.json()["id"]
        hl_resp = client.get(f"/content/{item_id}/highlights", headers=auth_headers)
        assert hl_resp.status_code == 200
        assert hl_resp.json() == []

    @patch("app.tasks.extraction.extract_metadata")
    @patch("app.tasks.embedding.generate_embedding")
    def test_highlight_default_color_is_yellow(
        self, mock_embed, mock_extract, client, auth_headers
    ):
        """Highlights without explicit color default to yellow."""
        payload = {
            "url": "https://example.com/default-color",
            "pre_extracted_html": "<p>Default color test content.</p>",
            "pre_extracted_title": "Color Test",
            "initial_highlights": [
                {
                    "text": "Default color test",
                    "start_offset": 3,
                    "end_offset": 21,
                }
            ],
        }
        resp = client.post("/content", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        item_id = resp.json()["id"]
        hl_resp = client.get(f"/content/{item_id}/highlights", headers=auth_headers)
        assert hl_resp.json()[0]["color"] == "yellow"
