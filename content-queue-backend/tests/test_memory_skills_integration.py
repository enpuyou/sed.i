"""Integration tests: memory → synthesis flow."""

from unittest.mock import patch

import pytest

from app.models.content import ContentItem
from app.mcp.tools.synthesis import SynthesisResponse, synthesize_topic
from app.tasks.memory import ConsolidationResult, consolidate_memory
from app.core.request_router import classify_request, RouteDecision


@pytest.fixture
def articles(db_session, test_user):
    """Three articles — enough to clear the MIN_ACTIVITY_ITEMS threshold."""
    items = []
    for i, title in enumerate(
        [
            "Distributed Systems Overview",
            "CAP Theorem in Practice",
            "Consistency vs Availability",
        ]
    ):
        item = ContentItem(
            original_url=f"https://example.com/integration-test-{i}",
            title=title,
            description="A deep dive into CAP theorem",
            user_id=test_user.id,
            processing_status="completed",
        )
        db_session.add(item)
        items.append(item)
    db_session.commit()
    for item in items:
        db_session.refresh(item)
    return items


@pytest.fixture
def article(articles):
    return articles[0]


class TestMemorySkillsIntegration:
    def test_profile_context_flows_into_synthesis(self, db_session, test_user, article):
        """consolidate → profile written → synthesize_topic reads memory_text → appears in prompt."""
        with patch("app.tasks.memory.llm_client") as mock_mem:
            mock_mem.structured_chat.return_value = ConsolidationResult(
                current_focus="distributed systems",
                reading_velocity="deep",
                memory_text="User is studying distributed systems and CAP theorem tradeoffs.",
            )
            consolidate_memory(str(test_user.id), db=db_session)

        captured = []
        with patch("app.mcp.tools.synthesis.llm_client") as mock_syn, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": article.description,
                    "user_id": test_user.id,
                }
            ],
        ):

            def capture(**kwargs):
                captured.extend(kwargs.get("messages", []))
                return SynthesisResponse(
                    summary="x",
                    perspectives=[],
                    key_concepts=[],
                    sources=[],
                    confidence="low",
                )

            mock_syn.structured_chat.side_effect = capture
            synthesize_topic(
                topic="CAP theorem", depth="quick", user=test_user, db=db_session
            )

        combined = " ".join(m["content"] for m in captured)
        assert "distributed systems" in combined
        assert "CAP theorem" in combined

    def test_memory_text_injected_into_synthesis_prompt(
        self, db_session, test_user, article
    ):
        """memory_text prose (not just current_focus) flows into synthesis prompt."""
        with patch("app.tasks.memory.llm_client") as mock_mem:
            mock_mem.structured_chat.return_value = ConsolidationResult(
                current_focus="distributed systems",
                reading_velocity="deep",
                memory_text="User is preparing for a distributed systems engineering role.",
            )
            consolidate_memory(str(test_user.id), db=db_session)

        captured = []
        with patch("app.mcp.tools.synthesis.llm_client") as mock_syn, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": test_user.id,
                }
            ],
        ):
            mock_syn.structured_chat.side_effect = lambda **kw: (
                captured.extend(kw.get("messages", []))
                or SynthesisResponse(
                    summary="x",
                    perspectives=[],
                    key_concepts=[],
                    sources=[],
                    confidence="low",
                )
            )
            synthesize_topic(
                topic="CAP theorem", depth="quick", user=test_user, db=db_session
            )

        combined = " ".join(m["content"] for m in captured)
        assert "preparing for a distributed systems engineering role" in combined

    def test_router_directs_weekly_question_to_skill(self):
        mock_decision = RouteDecision(route="skill", skill="weekly-digest")
        with patch("app.core.request_router.llm_client") as mock:
            mock.structured_chat.return_value = mock_decision
            route, skill = classify_request("what did I save this week?")

        assert route == "skill"
        assert skill == "weekly-digest"

    def test_memory_consolidation_does_not_affect_other_users(
        self, db_session, test_user, article
    ):
        from app.models.user import User
        from app.core.security import get_password_hash
        from app.models.memory import UserProfile

        other = User(
            email="isolation@test.com",
            username="isolation_user",
            hashed_password=get_password_hash("x"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        with patch("app.tasks.memory.llm_client") as mock_mem:
            mock_mem.structured_chat.return_value = ConsolidationResult(
                current_focus="isolation test",
                reading_velocity="fast",
                memory_text="Test isolation.",
            )
            consolidate_memory(str(test_user.id), db=db_session)

        assert db_session.get(UserProfile, other.id) is None
