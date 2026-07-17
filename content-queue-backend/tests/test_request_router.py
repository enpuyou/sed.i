"""Tests for the two-tier request classifier (Step 1.4)."""

from unittest.mock import patch

import pytest

from app.core.request_router import RouteDecision, classify_request


class TestClassifyRequest:
    # --- Tier 1: no LLM call ---

    @pytest.mark.parametrize(
        "question",
        [
            "find articles about transformers",
            "show me what I read last month",
            "get my reading stats",
            "search tag:ml after:2026-01-01",
            "list my recent saves",
            "how many articles do I have",
        ],
    )
    def test_tier1_direct_makes_no_llm_call(self, question):
        with patch("app.core.request_router.llm_client") as mock:
            route, skill = classify_request(question)
        assert route == "direct"
        assert skill is None
        mock.structured_chat.assert_not_called()

    @pytest.mark.parametrize(
        "operator",
        ["after:", "before:", "tag:", "author:", "site:"],
    )
    def test_tier1_catches_all_filter_operators(self, operator):
        question = f"articles {operator}example.com"
        with patch("app.core.request_router.llm_client") as mock:
            route, _ = classify_request(question)
        assert route == "direct"
        mock.structured_chat.assert_not_called()

    # --- Tier 2: LLM called ---

    @pytest.mark.parametrize(
        "question,expected_route,expected_skill",
        [
            ("what did I save this week?", "skill", "weekly-digest"),
            ("give me a summary of my past 7 days", "skill", "weekly-digest"),
            ("summarize what I've been reading recently", "skill", "weekly-digest"),
            (
                "how does this article connect to what I know?",
                "skill",
                "connect-new-save",
            ),
            ("how is this related to my library?", "skill", "connect-new-save"),
            (
                "help me draft an intro using my reading",
                "skill",
                "draft-from-highlights",
            ),
            ("write a paragraph from my highlights", "skill", "draft-from-highlights"),
            (
                "what are the competing views I've saved on AI alignment?",
                "orchestrate",
                None,
            ),
            (
                "synthesize everything I know about production ML systems",
                "orchestrate",
                None,
            ),
            (
                "what themes keep coming up across my ML reading?",
                "orchestrate",
                None,
            ),
        ],
    )
    def test_tier2_routes_via_llm(self, question, expected_route, expected_skill):
        mock_decision = RouteDecision(route=expected_route, skill=expected_skill)
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, skill = classify_request(question)
        assert route == expected_route
        assert skill == expected_skill
        mock.structured_chat.assert_called_once()

    def test_empty_string_calls_llm_not_tier1(self):
        mock_decision = RouteDecision(route="orchestrate", skill=None)
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, skill = classify_request("")
        assert route == "orchestrate"
        mock.structured_chat.assert_called_once()

    def test_synthesis_signal_prevents_tier1_shortcut(self):
        # "find" prefix + synthesis signal must go to tier 2
        question = "find competing views on RAG"
        mock_decision = RouteDecision(route="orchestrate", skill=None)
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, _ = classify_request(question)
        mock.structured_chat.assert_called_once()

    def test_case_insensitive_operator_matching(self):
        # Operators are checked on lowercased query
        with patch("app.core.request_router.llm_client") as mock:
            route, _ = classify_request("Find articles TAG:ml")
        assert route == "direct"
        mock.structured_chat.assert_not_called()
