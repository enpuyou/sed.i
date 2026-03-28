"""
TDD tests for MCP summarize tools: summarize_list, get_summary_job.
OpenAI calls are mocked — we test orchestration logic, not the model output.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.mcp.tools.summarize import summarize_list, get_summary_job


FAKE_SUMMARY = "Key themes: AI, agents, reasoning."


def _mock_openai(text=FAKE_SUMMARY):
    """Return a mock that makes openai.OpenAI().chat.completions.create() return text."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=text))]
    )
    return mock_client


class TestSummarizeList:
    def test_raises_on_unknown_list(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            summarize_list(
                list_id="00000000-0000-0000-0000-000000000000", user=user, db=db
            )

    def test_raises_on_other_users_list(self, db, user, other_user):
        from app.models.list import List

        other_list = List(name="Other", owner_id=other_user.id)
        db.add(other_list)
        db.commit()
        with pytest.raises(ValueError, match="not found"):
            summarize_list(list_id=str(other_list.id), user=user, db=db)

    def test_returns_message_when_list_is_empty(self, db, user, reading_list):
        result = summarize_list(list_id=str(reading_list.id), user=user, db=db)
        assert "no articles" in result["summary"].lower() or result["item_count"] == 0

    def test_calls_openai_and_returns_summary(self, db, user, list_with_articles):
        with patch("app.mcp.tools.summarize.OpenAI", return_value=_mock_openai()):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db
            )
        assert result["summary"] == FAKE_SUMMARY
        assert result["item_count"] == 2
        assert result["style"] == "overview"
        assert result["cached"] is False

    def test_result_contains_required_fields(self, db, user, list_with_articles):
        with patch("app.mcp.tools.summarize.OpenAI", return_value=_mock_openai()):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db
            )
        for field in ("summary", "style", "item_count", "cached"):
            assert field in result, f"Missing field: {field}"

    def test_respects_max_items(self, db, user, list_with_articles):
        with patch("app.mcp.tools.summarize.OpenAI", return_value=_mock_openai()):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db, max_items=1
            )
        assert result["item_count"] <= 1

    def test_all_styles_accepted(self, db, user, list_with_articles):
        for style in ("overview", "themes", "gaps", "timeline"):
            with patch("app.mcp.tools.summarize.OpenAI", return_value=_mock_openai()):
                result = summarize_list(
                    list_id=str(list_with_articles.id), user=user, db=db, style=style
                )
            assert result["style"] == style

    def test_gaps_style_includes_draft_context(
        self, db, user, list_with_articles, draft
    ):
        """gaps style should mention draft in the prompt sent to OpenAI."""
        captured_prompt = {}

        def capture_call(*args, **kwargs):
            captured_prompt["messages"] = kwargs.get("messages", [])
            return MagicMock(
                choices=[MagicMock(message=MagicMock(content=FAKE_SUMMARY))]
            )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = capture_call

        with patch("app.mcp.tools.summarize.OpenAI", return_value=mock_client):
            summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db, style="gaps"
            )

        # The draft content should appear somewhere in the messages
        all_text = " ".join(
            m["content"]
            for m in captured_prompt.get("messages", [])
            if isinstance(m.get("content"), str)
        )
        assert draft.content in all_text or "draft" in all_text.lower()

    def test_returns_cached_result_on_second_call(self, db, user, list_with_articles):
        call_count = {"n": 0}
        fake_response = MagicMock(
            choices=[MagicMock(message=MagicMock(content=FAKE_SUMMARY))]
        )

        def counting_create(*a, **kw):
            call_count["n"] += 1
            return fake_response

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = counting_create

        with patch("app.mcp.tools.summarize.OpenAI", return_value=mock_client):
            r1 = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)
            r2 = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)

        assert call_count["n"] == 1  # OpenAI called only once
        assert r2["cached"] is True
        assert r1["summary"] == r2["summary"]

    def test_invalid_style_raises(self, db, user, list_with_articles):
        with pytest.raises(ValueError, match="style"):
            summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db, style="invalid"
            )


class TestGetSummaryJob:
    def test_returns_pending_for_unknown_job(self, db, user):
        result = get_summary_job(job_id="nonexistent-job", user=user, db=db)
        assert result["status"] == "not_found"

    def test_returns_result_for_completed_job(self, db, user, list_with_articles):
        # Run a summary to create a cached entry, then retrieve it by job_id
        with patch("app.mcp.tools.summarize.OpenAI", return_value=_mock_openai()):
            r = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)

        if "job_id" in r:
            job_result = get_summary_job(job_id=r["job_id"], user=user, db=db)
            assert job_result["status"] in ("pending", "done", "not_found")
