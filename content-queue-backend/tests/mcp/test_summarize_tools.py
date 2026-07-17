"""
TDD tests for MCP summarize tools: summarize_list, get_summary_job.
LLM calls are mocked via llm_client.chat — we test orchestration logic, not model output.
"""

import pytest
from unittest.mock import patch
from app.mcp.tools.summarize import summarize_list, get_summary_job
from app.core.llm_client import ChatResult


FAKE_SUMMARY = "Key themes: AI, agents, reasoning."


def _fake_chat_result(text=FAKE_SUMMARY):
    return ChatResult(
        content=text, model="gpt-4o-mini", prompt_tokens=10, completion_tokens=20
    )


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
        with patch(
            "app.core.llm_client.llm_client.chat", return_value=_fake_chat_result()
        ):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db
            )
        assert result["summary"] == FAKE_SUMMARY
        assert result["item_count"] == 2
        assert result["style"] == "overview"
        assert result["cached"] is False

    def test_result_contains_required_fields(self, db, user, list_with_articles):
        with patch(
            "app.core.llm_client.llm_client.chat", return_value=_fake_chat_result()
        ):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db
            )
        for field in ("summary", "style", "item_count", "cached"):
            assert field in result, f"Missing field: {field}"

    def test_respects_max_items(self, db, user, list_with_articles):
        with patch(
            "app.core.llm_client.llm_client.chat", return_value=_fake_chat_result()
        ):
            result = summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db, max_items=1
            )
        assert result["item_count"] <= 1

    def test_all_styles_accepted(self, db, user, list_with_articles):
        for style in ("overview", "themes", "gaps", "timeline"):
            with patch(
                "app.core.llm_client.llm_client.chat", return_value=_fake_chat_result()
            ):
                result = summarize_list(
                    list_id=str(list_with_articles.id), user=user, db=db, style=style
                )
            assert result["style"] == style

    def test_gaps_style_includes_draft_context(
        self, db, user, list_with_articles, draft
    ):
        """gaps style should mention draft in the prompt sent to llm_client.chat."""
        captured_messages = {}

        def capture_call(messages, **kwargs):
            captured_messages["messages"] = messages
            return _fake_chat_result()

        with patch("app.core.llm_client.llm_client.chat", side_effect=capture_call):
            summarize_list(
                list_id=str(list_with_articles.id), user=user, db=db, style="gaps"
            )

        all_text = " ".join(
            m["content"]
            for m in captured_messages.get("messages", [])
            if isinstance(m.get("content"), str)
        )
        assert draft.content in all_text or "draft" in all_text.lower()

    def test_returns_cached_result_on_second_call(self, db, user, list_with_articles):
        call_count = {"n": 0}

        def counting_chat(*a, **kw):
            call_count["n"] += 1
            return _fake_chat_result()

        with patch("app.core.llm_client.llm_client.chat", side_effect=counting_chat):
            r1 = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)
            r2 = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)

        assert call_count["n"] == 1  # LLM called only once (second is cached)
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

    def test_summary_job_id_not_in_synchronous_result(
        self, db, user, list_with_articles
    ):
        # summarize_list returns synchronously (no job queue); the result must not
        # contain a job_id key, which would indicate an accidental async regression.
        with patch(
            "app.core.llm_client.llm_client.chat", return_value=_fake_chat_result()
        ):
            r = summarize_list(list_id=str(list_with_articles.id), user=user, db=db)

        assert "job_id" not in r, (
            "summarize_list returned a job_id — the tool has become async. "
            "Wire up get_summary_job to actually retrieve the result, then update this test."
        )
