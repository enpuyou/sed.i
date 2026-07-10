"""Tests for synthesize_topic and assist_draft MCP tools (Steps 1.5 + 1.6)."""

from unittest.mock import patch

import pytest

from app.mcp.tools.synthesis import (
    Citation,
    DraftAddition,
    PerspectiveItem,
    SourceCitation,
    SynthesisResponse,
    assist_draft,
    synthesize_topic,
)
from app.models.content import ContentItem
from app.models.draft import Draft
from app.models.memory import UserProfile


class TestSynthesizeTopicQuick:
    def test_returns_structured_response(self, db, user, article):
        mock_response = SynthesisResponse(
            summary="The library contains 1 article on this topic.",
            perspectives=[
                PerspectiveItem(
                    stance="nuanced", summary="...", source_ids=[str(article.id)]
                )
            ],
            key_concepts=["attention mechanism"],
            sources=[
                SourceCitation(item_id=str(article.id), title=article.title, quote=None)
            ],
            confidence="medium",
        )
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": article.description,
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.return_value = mock_response
            result = synthesize_topic(
                topic="attention mechanisms", depth="quick", user=user, db=db
            )

        assert "summary" in result
        assert "perspectives" in result
        assert "sources" in result
        assert result["confidence"] in ("high", "medium", "low")

    def test_seeds_from_user_profile(self, db, user, article):
        db.merge(UserProfile(user_id=user.id, current_focus="agent evals"))
        db.commit()

        captured_messages = []

        def capture(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return SynthesisResponse(
                summary="x",
                perspectives=[],
                key_concepts=[],
                sources=[],
                confidence="low",
            )

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.side_effect = capture
            synthesize_topic(topic="evals", depth="quick", user=user, db=db)

        combined = " ".join(m["content"] for m in captured_messages)
        assert "agent evals" in combined

    def test_no_results_returns_low_confidence_early(self, db, user):
        with patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[],
        ), patch("app.mcp.tools.synthesis.llm_client"):
            result = synthesize_topic(
                topic="obscure topic", depth="quick", user=user, db=db
            )

        assert result["confidence"] == "low"

    def test_sources_only_from_retrieved_items(self, db, user, article):
        mock_response = SynthesisResponse(
            summary="found it",
            perspectives=[],
            key_concepts=[],
            sources=[
                SourceCitation(item_id=str(article.id), title=article.title, quote=None)
            ],
            confidence="high",
        )
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.return_value = mock_response
            result = synthesize_topic(
                topic=article.title, depth="quick", user=user, db=db
            )

        returned_ids = [s["item_id"] for s in result["sources"]]
        assert str(article.id) in returned_ids

    def test_other_users_articles_not_in_context(self, db, user, other_user):
        other_article = ContentItem(
            original_url="https://secret.com/article",
            title="Secret Article",
            user_id=other_user.id,
            processing_status="completed",
        )
        db.add(other_article)
        db.commit()

        captured = []

        def capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return SynthesisResponse(
                summary="x",
                perspectives=[],
                key_concepts=[],
                sources=[],
                confidence="low",
            )

        # hybrid_search is user-scoped — patch it to return empty (other user's article not returned)
        with patch("app.mcp.tools.synthesis.hybrid_search", return_value=[]), patch(
            "app.mcp.tools.synthesis.llm_client"
        ):
            result = synthesize_topic(topic="secret", depth="quick", user=user, db=db)

        # Should return early with low confidence since no results
        assert result["confidence"] == "low"
        # Secret Article should not appear anywhere in a synthesized context
        combined = " ".join(str(m) for m in captured)
        assert "Secret Article" not in combined

    def test_deep_mode_raises_not_implemented(self, db, user):
        with pytest.raises(NotImplementedError):
            synthesize_topic(topic="anything", depth="deep", user=user, db=db)

    def test_hallucinated_source_filtered_out(self, db, user, article):
        fake_id = "00000000-0000-0000-0000-000000000000"
        mock_response = SynthesisResponse(
            summary="found it",
            perspectives=[],
            key_concepts=[],
            sources=[
                SourceCitation(
                    item_id=str(article.id), title=article.title, quote=None
                ),
                SourceCitation(item_id=fake_id, title="Made Up Article", quote=None),
            ],
            confidence="medium",
        )
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.return_value = mock_response
            result = synthesize_topic(topic="topic", depth="quick", user=user, db=db)

        returned_ids = [s["item_id"] for s in result["sources"]]
        assert str(article.id) in returned_ids
        assert fake_id not in returned_ids


class TestAssistDraft:
    def test_appends_to_draft(self, db, user, reading_list):
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search", return_value=[]
        ):
            mock_llm.structured_chat.return_value = DraftAddition(
                content="RAG grounds LLM responses in retrieved documents.",
                citations=[],
            )
            result = assist_draft(
                list_id=str(reading_list.id),
                instruction="write a sentence about RAG",
                user=user,
                db=db,
            )

        assert result["added"] == "RAG grounds LLM responses in retrieved documents."
        assert result["source_count"] >= 0

        draft = db.query(Draft).filter_by(list_id=reading_list.id).first()
        assert draft is not None
        assert "RAG" in draft.content

    def test_uses_neutral_style_default(self, db, user, reading_list):
        captured = []

        def capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return DraftAddition(content="x", citations=[])

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search", return_value=[]
        ):
            mock_llm.structured_chat.side_effect = capture
            assist_draft(
                list_id=str(reading_list.id),
                instruction="write intro",
                user=user,
                db=db,
            )

        combined = " ".join(str(m) for m in captured)
        assert "clear, concise prose" in combined

    def test_citations_only_reference_retrieved_items(
        self, db, user, article, reading_list
    ):
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.return_value = DraftAddition(
                content="Content about the article.",
                citations=[Citation(item_id=str(article.id), title=article.title)],
            )
            result = assist_draft(
                list_id=str(reading_list.id),
                instruction=article.title,
                user=user,
                db=db,
            )

        cited_ids = [c["item_id"] for c in result["citations"]]
        for cid in cited_ids:
            item = db.get(ContentItem, cid)
            assert item is not None
            assert item.user_id == user.id

    def test_hallucinated_citation_filtered_out(self, db, user, article, reading_list):
        fake_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search",
            return_value=[
                {
                    "id": str(article.id),
                    "title": article.title,
                    "description": "",
                    "user_id": user.id,
                }
            ],
        ):
            mock_llm.structured_chat.return_value = DraftAddition(
                content="Content.",
                citations=[
                    Citation(item_id=str(article.id), title=article.title),
                    Citation(item_id=fake_id, title="Fake Article"),
                ],
            )
            result = assist_draft(
                list_id=str(reading_list.id),
                instruction="something",
                user=user,
                db=db,
            )

        cited_ids = [c["item_id"] for c in result["citations"]]
        assert str(article.id) in cited_ids
        assert fake_id not in cited_ids

    def test_does_not_modify_library(self, db, user, reading_list):
        count_before = db.query(ContentItem).filter_by(user_id=user.id).count()

        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search", return_value=[]
        ):
            mock_llm.structured_chat.return_value = DraftAddition(
                content="x", citations=[]
            )
            assist_draft(
                list_id=str(reading_list.id),
                instruction="write something",
                user=user,
                db=db,
            )

        count_after = db.query(ContentItem).filter_by(user_id=user.id).count()
        assert count_before == count_after

    def test_raises_on_missing_list(self, db, user):
        with pytest.raises(ValueError, match="not found"):
            assist_draft(
                list_id="00000000-0000-0000-0000-000000000000",
                instruction="write something",
                user=user,
                db=db,
            )

    def test_appends_to_existing_draft(self, db, user, reading_list, draft):
        original_content = draft.content
        with patch("app.mcp.tools.synthesis.llm_client") as mock_llm, patch(
            "app.mcp.tools.synthesis.hybrid_search", return_value=[]
        ):
            mock_llm.structured_chat.return_value = DraftAddition(
                content="New paragraph added.", citations=[]
            )
            assist_draft(
                list_id=str(reading_list.id),
                instruction="add more content",
                user=user,
                db=db,
            )

        updated = db.query(Draft).filter_by(list_id=reading_list.id).first()
        assert original_content in updated.content
        assert "New paragraph added." in updated.content
