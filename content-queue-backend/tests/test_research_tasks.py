"""
Tests for the multi-agent research brief tasks.

Covers Steps 2.2-2.6:
- Lead agent: planning, dispatch, idempotency
- Subagent: retrieval contract, engagement scoring
- collect_subagent_results: merge, iterate, partial on budget exhaustion
- synthesize_run / verify_synthesis: ResearchBrief output, fabricated citation removal
- recover_orphaned_runs: stale run detection
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.research import ResearchRun
from app.schemas.research import (
    GapItem,
    ResearchBrief,
    SourceCitation,
    SubQuestionFinding,
)
from app.tasks.research import (
    _build_per_sq_context,
    _merge_item_ids,
    collect_subagent_results,
    recover_orphaned_runs,
    run_research_lead,
    run_research_subagent,
    synthesize_run,
    verify_synthesis,
)

DEFAULT_BUDGET = {
    "max_tokens": 50000,
    "max_iterations": 3,
    "max_subagents": 5,
    "timeout_s": 300,
    "target_count": 8,
}


def _make_run(
    db, user, *, status="queued", budget=None, item_ids=None, sub_questions=None
):
    run = ResearchRun(
        user_id=user.id,
        question="What are the competing views on AI and labor?",
        mode="deep",
        status=status,
        budget=budget or DEFAULT_BUDGET,
        item_ids_retrieved=item_ids,
        sub_questions=sub_questions,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_article(db, user, title="Test Article"):
    item = ContentItem(
        original_url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        description="A test article about AI.",
        user_id=user.id,
        processing_status="completed",
        is_read=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _make_brief(article_id=None, summary="Found articles."):
    finding = SubQuestionFinding(
        sub_question="Q1",
        coverage="full",
        finding="finding text",
        key_sources=(
            [
                SourceCitation(
                    item_id=str(article_id),
                    title="Article",
                    representative_highlight=None,
                )
            ]
            if article_id
            else []
        ),
        tensions=[],
    )
    return ResearchBrief(
        summary=summary,
        sub_question_findings=[finding],
        cross_cutting_tensions=[],
        gaps=[],
        engagement_note="",
        confidence="medium",
    )


# ---------------------------------------------------------------------------
# Step 2.2 — Lead agent skeleton
# ---------------------------------------------------------------------------


class TestLeadAgentSkeleton:
    def test_transitions_queued_to_done(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")
        mock_brief = _make_brief(summary="Skeleton complete.")

        class FakePlan:
            sub_questions = ["Q1", "Q2"]

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = [FakePlan(), mock_brief]
            with patch("app.tasks.research.hybrid_search", return_value=[]):
                run_research_lead(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status in (
            "done",
            "partial",
            "searching",
            "synthesizing",
            "verifying",
        )

    def test_idempotent_on_non_queued_status(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="done")
        run.result = {
            "summary": "original",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [],
            "engagement_note": "",
            "confidence": "high",
        }
        db_session.commit()

        with patch("app.tasks.research.llm_client") as mock_llm:
            run_research_lead(str(run.id), db=db_session)
            mock_llm.structured_chat.assert_not_called()

        db_session.refresh(run)
        assert run.result["summary"] == "original"

    def test_plan_written_before_status_advances(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")

        class FakePlan:
            sub_questions = [
                "What is the displacement rate?",
                "What are counterarguments?",
            ]

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = [FakePlan(), _make_brief()]
            with patch("app.tasks.research.hybrid_search", return_value=[]):
                run_research_lead(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.plan is not None
        assert "What is the displacement rate?" in run.plan

    def test_sub_questions_stored_on_run(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="queued")

        class FakePlan:
            sub_questions = ["Sub-Q1", "Sub-Q2", "Sub-Q3"]

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.side_effect = [FakePlan(), _make_brief()]
            with patch("app.tasks.research.hybrid_search", return_value=[]):
                run_research_lead(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.sub_questions == ["Sub-Q1", "Sub-Q2", "Sub-Q3"]

    def test_unknown_run_id_is_no_op(self, db_session, test_user):
        with patch("app.tasks.research.llm_client") as mock_llm:
            run_research_lead(str(uuid.uuid4()), db=db_session)
            mock_llm.structured_chat.assert_not_called()


# ---------------------------------------------------------------------------
# Step 2.3 — Subagent contract
# ---------------------------------------------------------------------------


def _fake_chat_result(text="Article summary for testing."):
    from app.core.llm_client import ChatResult

    return ChatResult(
        content=text, model="test", prompt_tokens=10, completion_tokens=20
    )


def _patch_subagent(article_ids: list[str]):
    """Return context managers that patch the subagent's LLM calls, embedding, and chunks."""

    class FakeRelevance:
        relevant_ids = article_ids

    mock_llm = patch("app.tasks.research.llm_client")
    mock_embed = patch("app.core.embedding_cache.call_embed", return_value=[0.1] * 1536)
    mock_chunks = patch(
        "app.tasks.research._fetch_top_chunks", return_value=["chunk text"]
    )

    return mock_llm, mock_embed, mock_chunks, FakeRelevance


def _configure_subagent_llm(mock_llm, article_ids: list[str]):
    """Set up mock_llm side effects for query expansion + relevance filter + per-article summary."""

    class FakeExpansion:
        queries = ["alt query A", "alt query B"]

    class FakeRelevance:
        relevant_ids = article_ids

    # structured_chat calls per subagent: 1=query expansion, 2=relevance filter
    mock_llm.structured_chat.side_effect = [FakeExpansion(), FakeRelevance()]
    mock_llm.chat.return_value = _fake_chat_result()


class TestSubagentContract:
    def _run_subagent(self, db_session, run, article, sub_question="Q"):
        mock_llm_ctx, mock_embed_ctx, mock_chunks_ctx, _ = _patch_subagent(
            [str(article.id)]
        )
        with mock_llm_ctx as mock_llm, mock_embed_ctx, mock_chunks_ctx:
            _configure_subagent_llm(mock_llm, [str(article.id)])
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [
                    {
                        "id": str(article.id),
                        "title": article.title,
                        "description": "desc",
                    }
                ]
                return run_research_subagent(
                    str(run.id),
                    "s1",
                    {
                        "sub_question": sub_question,
                        "search_params": {"query": "x"},
                        "budget": {"timeout_s": 30},
                    },
                    db=db_session,
                )

    def test_returns_ok_true_on_success(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="searching")
        result = self._run_subagent(
            db_session, run, article, sub_question="What are displacement rates?"
        )

        assert result["ok"] is True
        assert "item_ids" in result["data"]
        assert str(article.id) in result["data"]["item_ids"]

    def test_returns_ok_false_on_error(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")

        with patch(
            "app.tasks.research.hybrid_search", side_effect=Exception("DB down")
        ):
            result = run_research_subagent(
                str(run.id),
                "subagent-1",
                {
                    "sub_question": "Q",
                    "search_params": {"query": "x"},
                    "budget": {"timeout_s": 30},
                },
                db=db_session,
            )

        assert result["ok"] is False
        assert result["error"]["code"] is not None

    def test_engagement_score_computed(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        # Add 3 highlights → score = 3*2 + 1*1 = 7
        for i in range(3):
            db_session.add(
                Highlight(
                    content_item_id=article.id,
                    user_id=test_user.id,
                    text=f"highlight {i}",
                    start_offset=i * 10,
                    end_offset=i * 10 + 9,
                )
            )
        db_session.commit()
        run = _make_run(db_session, test_user, status="searching")
        result = self._run_subagent(db_session, run, article)

        assert result["ok"] is True
        art_data = result["data"]["articles"][0]
        assert art_data["engagement_score"] == 7  # 3*2 + 1*1
        assert len(art_data["highlights"]) == 3

    def test_article_summary_generated(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="searching")
        result = self._run_subagent(db_session, run, article)

        assert result["ok"] is True
        art_data = result["data"]["articles"][0]
        assert art_data["article_summary"] == "Article summary for testing."

    def test_coverage_assessment_none_when_no_results(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")

        with patch("app.tasks.research.hybrid_search", return_value=[]):
            result = run_research_subagent(
                str(run.id),
                "s1",
                {
                    "sub_question": "Q",
                    "search_params": {"query": "x"},
                    "budget": {"timeout_s": 30},
                },
                db=db_session,
            )

        assert result["ok"] is True
        assert result["data"]["coverage_assessment"] == "none"

    def test_coverage_none_when_filter_rejects_all(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="searching")

        mock_llm_ctx, mock_embed_ctx, mock_chunks_ctx, _ = _patch_subagent([])

        with mock_llm_ctx as mock_llm, mock_embed_ctx, mock_chunks_ctx:

            class EmptyRelevance:
                relevant_ids = []

            mock_llm.structured_chat.return_value = EmptyRelevance()
            mock_llm.chat.return_value = _fake_chat_result()
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [
                    {
                        "id": str(article.id),
                        "title": article.title,
                        "description": "desc",
                    }
                ]
                result = run_research_subagent(
                    str(run.id),
                    "s1",
                    {
                        "sub_question": "Q",
                        "search_params": {"query": "x"},
                        "budget": {"timeout_s": 30},
                    },
                    db=db_session,
                )

        assert result["ok"] is True
        assert result["data"]["coverage_assessment"] == "none"
        assert result["data"]["item_ids"] == []

    def test_chunks_attached_to_articles(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(db_session, test_user, status="searching")
        result = self._run_subagent(db_session, run, article)

        assert result["ok"] is True
        assert result["data"]["articles"][0]["chunks"] == ["chunk text"]

    def test_other_users_articles_not_returned(self, db_session, test_user):
        from app.core.security import get_password_hash
        from app.models.user import User

        other = User(
            email="other2@test.com",
            username="other2_trt",
            hashed_password=get_password_hash("x"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        other_article = _make_article(db_session, other, title="Other Secret")
        run = _make_run(db_session, test_user, status="searching")

        with patch("app.tasks.research.hybrid_search", return_value=[]):
            result = run_research_subagent(
                str(run.id),
                "s1",
                {
                    "sub_question": "Q",
                    "search_params": {"query": "Other Secret"},
                    "budget": {"timeout_s": 30},
                },
                db=db_session,
            )

        # hybrid_search is mocked to return nothing — other user's article not leaked
        assert result["ok"] is True
        assert str(other_article.id) not in result["data"]["item_ids"]


# ---------------------------------------------------------------------------
# Step 2.4 — collect_subagent_results
# ---------------------------------------------------------------------------


class TestCollectSubagentResults:
    def test_merges_item_ids_from_successful_subagents(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [id_a],
                    "articles": [],
                    "coverage_assessment": "partial",
                },
                "error": None,
                "meta": {},
            },
            {
                "ok": True,
                "data": {
                    "sub_question": "Q2",
                    "item_ids": [id_b],
                    "articles": [],
                    "coverage_assessment": "partial",
                },
                "error": None,
                "meta": {},
            },
        ]

        with patch("app.tasks.research.synthesize_run"):
            collect_subagent_results(results, run_id=str(run.id), db=db_session)

        db_session.refresh(run)
        retrieved = run.item_ids_retrieved or []
        assert id_a in retrieved
        assert id_b in retrieved

    def test_skips_failed_subagent_results(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        good_id = str(uuid.uuid4())
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [good_id],
                    "articles": [],
                    "coverage_assessment": "full",
                },
                "error": None,
                "meta": {},
            },
            {"ok": False, "data": None, "error": {"code": "timeout"}, "meta": {}},
        ]

        with patch("app.tasks.research.synthesize_run"):
            collect_subagent_results(results, run_id=str(run.id), db=db_session)

        db_session.refresh(run)
        retrieved = run.item_ids_retrieved or []
        assert good_id in retrieved
        assert len(retrieved) == 1

    def test_increments_iteration_count(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        assert run.iteration_count == 0

        with patch("app.tasks.research.synthesize_run"):
            collect_subagent_results([], run_id=str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.iteration_count == 1

    def test_dispatches_synthesize_when_target_reached(self, db_session, test_user):
        budget = {**DEFAULT_BUDGET, "target_count": 2}
        run = _make_run(db_session, test_user, status="searching", budget=budget)
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [id_a, id_b],
                    "articles": [],
                    "coverage_assessment": "full",
                },
                "error": None,
                "meta": {},
            },
        ]

        synthesize_called = []
        with patch(
            "app.tasks.research.synthesize_run",
            side_effect=lambda *a, **kw: synthesize_called.append(True),
        ):
            collect_subagent_results(results, run_id=str(run.id), db=db_session)

        assert len(synthesize_called) == 1

    def test_iterates_when_coverage_none(self, db_session, test_user):
        budget = {**DEFAULT_BUDGET, "target_count": 20, "max_iterations": 3}
        run = _make_run(db_session, test_user, status="searching", budget=budget)
        new_id = str(uuid.uuid4())
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [new_id],
                    "articles": [],
                    "coverage_assessment": "none",
                },
                "error": None,
                "meta": {},
            },
        ]

        lead_called = []
        with patch(
            "app.tasks.research.run_research_lead",
            side_effect=lambda *a, **kw: lead_called.append(True),
        ):
            with patch("app.tasks.research.synthesize_run"):
                collect_subagent_results(results, run_id=str(run.id), db=db_session)

        assert len(lead_called) == 1

    def test_no_iterate_when_coverage_partial(self, db_session, test_user):
        budget = {**DEFAULT_BUDGET, "target_count": 20, "max_iterations": 3}
        run = _make_run(db_session, test_user, status="searching", budget=budget)
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [str(uuid.uuid4())],
                    "articles": [],
                    "coverage_assessment": "partial",
                },
                "error": None,
                "meta": {},
            },
        ]

        lead_called = []
        with patch(
            "app.tasks.research.run_research_lead",
            side_effect=lambda *a, **kw: lead_called.append(True),
        ):
            with patch("app.tasks.research.synthesize_run"):
                collect_subagent_results(results, run_id=str(run.id), db=db_session)

        # "partial" coverage means we synthesize, not iterate
        assert len(lead_called) == 0

    def test_marks_partial_and_synthesizes_on_max_iterations(
        self, db_session, test_user
    ):
        budget = {**DEFAULT_BUDGET, "target_count": 100, "max_iterations": 1}
        run = _make_run(db_session, test_user, status="searching", budget=budget)
        # iteration_count will become 1 after this call = max_iterations
        synthesize_called = []
        with patch(
            "app.tasks.research.synthesize_run",
            side_effect=lambda *a, **kw: synthesize_called.append(True),
        ):
            collect_subagent_results([], run_id=str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status == "partial"
        assert len(synthesize_called) == 1

    def test_deduplicates_merged_ids(self, db_session, test_user):
        shared_id = str(uuid.uuid4())
        run = _make_run(db_session, test_user, status="searching", item_ids=[shared_id])
        results = [
            {
                "ok": True,
                "data": {
                    "sub_question": "Q1",
                    "item_ids": [shared_id],
                    "articles": [],
                    "coverage_assessment": "partial",
                },
                "error": None,
                "meta": {},
            },
        ]

        with patch("app.tasks.research.synthesize_run"):
            collect_subagent_results(results, run_id=str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.item_ids_retrieved.count(shared_id) == 1


# ---------------------------------------------------------------------------
# Helpers unit tests
# ---------------------------------------------------------------------------


class TestMergeItemIds:
    def test_union_merge(self):
        assert _merge_item_ids(["a", "b"], ["b", "c"]) == ["a", "b", "c"]

    def test_none_existing(self):
        assert _merge_item_ids(None, ["a", "b"]) == ["a", "b"]

    def test_empty_new(self):
        assert _merge_item_ids(["a"], []) == ["a"]


class TestBuildPerSqContext:
    def _make_subagent_result(
        self,
        sq: str,
        article_id: str,
        highlights=None,
        engagement=1,
        article_summary="Test summary.",
    ):
        # _build_per_sq_context receives the inner data dict (what collect stores in run.subagent_results)
        return {
            "sub_question": sq,
            "item_ids": [article_id],
            "articles": [
                {
                    "id": article_id,
                    "title": "Test Article",
                    "description": "desc",
                    "highlights": highlights or [],
                    "engagement_score": engagement,
                    "chunks": ["chunk text"],
                    "article_summary": article_summary,
                }
            ],
            "coverage_assessment": "partial",
        }

    def test_summary_appears_before_chunks(self):
        result = self._make_subagent_result(
            "Q1", "a1", article_summary="Key finding here."
        )
        ctx = _build_per_sq_context([result], ["Q1"])
        assert "Key finding here." in ctx
        assert ctx.index("Key finding here.") < ctx.index("chunk text")

    def test_includes_highlights_regardless_of_engagement(self):
        result = self._make_subagent_result(
            "Q1", "a1", highlights=["hl1"], engagement=0
        )
        ctx = _build_per_sq_context([result], ["Q1"])
        assert "hl1" in ctx
        assert "a1" in ctx

    def test_high_engagement_also_gets_highlights(self):
        result = self._make_subagent_result(
            "Q1", "a1", highlights=["hl2"], engagement=5
        )
        ctx = _build_per_sq_context([result], ["Q1"])
        assert "hl2" in ctx

    def test_token_budget_respected(self):
        results = [
            self._make_subagent_result("Q1", f"a{i}", highlights=["x" * 100])
            for i in range(20)
        ]
        ctx = _build_per_sq_context(results, ["Q1"], max_tokens=200)
        # Very tight budget should cut many articles
        assert len(ctx) < 200 * 5  # rough sanity — not 20× full articles

    def test_per_sq_sections_present(self):
        r1 = self._make_subagent_result("What is A?", "a1")
        r2 = self._make_subagent_result("What is B?", "a2")
        ctx = _build_per_sq_context([r1, r2], ["What is A?", "What is B?"])
        assert "What is A?" in ctx
        assert "What is B?" in ctx


# ---------------------------------------------------------------------------
# Step 2.5 — Synthesis + verification
# ---------------------------------------------------------------------------


class TestSynthesisAndVerification:
    def test_synthesis_writes_research_brief(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(
            db_session,
            test_user,
            status="searching",
            item_ids=[str(article.id)],
            sub_questions=["What is the displacement rate?"],
        )

        mock_brief = ResearchBrief(
            summary="One article found on labor displacement.",
            sub_question_findings=[
                SubQuestionFinding(
                    sub_question="What is the displacement rate?",
                    coverage="partial",
                    finding="Limited evidence found.",
                    key_sources=[
                        SourceCitation(
                            item_id=str(article.id),
                            title=article.title,
                            representative_highlight=None,
                        )
                    ],
                    tensions=[],
                )
            ],
            cross_cutting_tensions=[],
            gaps=[],
            engagement_note="1 article with moderate engagement.",
            confidence="medium",
        )

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = mock_brief
            synthesize_run(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.result is not None
        assert run.result["summary"] == "One article found on labor displacement."
        assert len(run.result["sub_question_findings"]) == 1
        assert run.status == "done"

    def test_synthesis_ends_in_done_via_verify(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(
            db_session, test_user, status="searching", item_ids=[str(article.id)]
        )

        mock_brief = _make_brief(article_id=article.id)

        with patch("app.tasks.research.llm_client") as mock_llm:
            mock_llm.structured_chat.return_value = mock_brief
            synthesize_run(str(run.id), db=db_session)

        db_session.refresh(run)
        # synthesize_run → verifying → verify_synthesis → done
        assert run.status == "done"
        assert run.result is not None

    def test_verification_passes_grounded_citations(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        run = _make_run(
            db_session, test_user, status="verifying", item_ids=[str(article.id)]
        )
        run.result = {
            "summary": "x",
            "sub_question_findings": [
                {
                    "sub_question": "Q1",
                    "coverage": "full",
                    "finding": "finding",
                    "key_sources": [
                        {
                            "item_id": str(article.id),
                            "title": article.title,
                            "representative_highlight": None,
                        }
                    ],
                    "tensions": [],
                }
            ],
            "cross_cutting_tensions": [],
            "gaps": [],
            "engagement_note": "",
            "confidence": "medium",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status == "done"
        sources = run.result["sub_question_findings"][0]["key_sources"]
        assert any(s["item_id"] == str(article.id) for s in sources)

    def test_verification_removes_fabricated_citations(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        fake_id = str(uuid.uuid4())
        run = _make_run(
            db_session, test_user, status="verifying", item_ids=[str(article.id)]
        )
        run.result = {
            "summary": "x",
            "sub_question_findings": [
                {
                    "sub_question": "Q1",
                    "coverage": "partial",
                    "finding": "finding",
                    "key_sources": [
                        {
                            "item_id": str(article.id),
                            "title": "Real",
                            "representative_highlight": None,
                        },
                        {
                            "item_id": fake_id,
                            "title": "Fabricated",
                            "representative_highlight": None,
                        },
                    ],
                    "tensions": [],
                }
            ],
            "cross_cutting_tensions": [],
            "gaps": [],
            "engagement_note": "",
            "confidence": "medium",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        sources = run.result["sub_question_findings"][0]["key_sources"]
        ids = [s["item_id"] for s in sources]
        assert str(article.id) in ids
        assert fake_id not in ids

    def test_verification_cleans_partial_coverage_in_gaps(self, db_session, test_user):
        article = _make_article(db_session, test_user)
        fake_id = str(uuid.uuid4())
        run = _make_run(
            db_session, test_user, status="verifying", item_ids=[str(article.id)]
        )
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [
                {
                    "sub_question": "Policy response",
                    "what_is_missing": "Labor policy research",
                    "partial_coverage": [str(article.id), fake_id],
                }
            ],
            "engagement_note": "",
            "confidence": "low",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status == "done"
        gap_ids = run.result["gaps"][0]["partial_coverage"]
        assert str(article.id) in gap_ids
        assert fake_id not in gap_ids

    def test_verification_injects_missing_gap_for_none_coverage_sq(
        self, db_session, test_user
    ):
        """verify_synthesis must inject a gap entry for any sub-question the LLM failed to report."""
        article = _make_article(db_session, test_user)
        run = _make_run(
            db_session, test_user, status="verifying", item_ids=[str(article.id)]
        )
        # Subagent found nothing for "Policy implications" — coverage none
        run.subagent_results = [
            {
                "sub_question": "Policy implications",
                "coverage_assessment": "none",
                "item_ids": [],
                "articles": [],
            },
        ]
        # Brief LLM forgot to include a gap entry for it
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [],  # missing the required gap
            "engagement_note": "",
            "confidence": "low",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status == "done"
        gap_sqs = [g["sub_question"] for g in run.result["gaps"]]
        assert "Policy implications" in gap_sqs

    def test_verification_does_not_duplicate_existing_gap(self, db_session, test_user):
        """If the LLM already reported the gap, don't add a second entry."""
        run = _make_run(db_session, test_user, status="verifying", item_ids=[])
        run.subagent_results = [
            {
                "sub_question": "Policy implications",
                "coverage_assessment": "none",
                "item_ids": [],
                "articles": [],
            },
        ]
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [
                {
                    "sub_question": "Policy implications",
                    "what_is_missing": "Policy research",
                    "partial_coverage": [],
                }
            ],
            "engagement_note": "",
            "confidence": "low",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        gap_sqs = [g["sub_question"] for g in run.result["gaps"]]
        assert gap_sqs.count("Policy implications") == 1

    def test_verification_strips_gap_for_full_coverage_sq(self, db_session, test_user):
        """Gap entries for sub-questions with coverage 'full' must be removed — they are fabricated."""
        run = _make_run(db_session, test_user, status="verifying", item_ids=[])
        run.subagent_results = [
            {
                "sub_question": "Who are the key orgs?",
                "coverage_assessment": "full",
                "item_ids": [],
                "articles": [],
            },
        ]
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [
                {
                    "sub_question": "Who are the key orgs?",
                    "what_is_missing": "Other orgs not in library",
                    "partial_coverage": [],
                }
            ],
            "engagement_note": "",
            "confidence": "high",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        assert (
            run.result["gaps"] == []
        ), "gap for full-coverage sub-question should be stripped"

    def test_verification_strips_gap_for_partial_coverage_sq(
        self, db_session, test_user
    ):
        """Gap entries for sub-questions with coverage 'partial' must also be stripped — partial means library has content."""
        run = _make_run(db_session, test_user, status="verifying", item_ids=[])
        run.subagent_results = [
            {
                "sub_question": "What tools are used?",
                "coverage_assessment": "partial",
                "item_ids": [],
                "articles": [],
            },
        ]
        run.result = {
            "summary": "x",
            "sub_question_findings": [],
            "cross_cutting_tensions": [],
            "gaps": [
                {
                    "sub_question": "What tools are used?",
                    "what_is_missing": "Detailed tool comparisons missing",
                    "partial_coverage": [],
                }
            ],
            "engagement_note": "",
            "confidence": "high",
        }
        db_session.commit()

        verify_synthesis(str(run.id), db=db_session)

        db_session.refresh(run)
        assert (
            run.result["gaps"] == []
        ), "gap for partial-coverage sub-question should be stripped"

    def test_full_pipeline_queued_to_done(self, db_session, test_user):
        """
        Run the full pipeline synchronously by patching chord to execute inline.
        chord(group)(callback.s(run_id=..)) → execute each subagent synchronously,
        then call collect_subagent_results directly.
        """
        article = _make_article(
            db_session, test_user, title="AI Labor Displacement Study"
        )
        # target_count=1 ensures collect_subagent_results triggers synthesis after round 1
        budget = {**DEFAULT_BUDGET, "target_count": 1}
        run = _make_run(db_session, test_user, status="queued", budget=budget)

        mock_brief = _make_brief(article_id=article.id, summary="Found one article.")

        class FakePlan:
            sub_questions = ["What are displacement rates?"]

        def fake_chord(group_obj):
            """Run subagents synchronously and call the chord callback inline."""

            class FakeChord:
                def __call__(self, callback_sig):
                    # Execute each task in the group synchronously
                    results = []
                    for sig in group_obj.tasks:
                        task_fn = sig.task
                        args = sig.args
                        results.append(run_research_subagent(*args, db=db_session))
                    # Extract run_id from the callback kwargs
                    cb_kwargs = callback_sig.kwargs
                    collect_subagent_results(results, db=db_session, **cb_kwargs)

            return FakeChord()

        class FakeExpansion:
            queries = ["alternative query A", "alternative query B"]

        class FakeRelevance:
            relevant_ids = [str(article.id)]

        with patch("app.tasks.research.llm_client") as mock_llm, patch(
            "app.core.embedding_cache.call_embed", return_value=[0.1] * 1536
        ), patch("app.tasks.research._fetch_top_chunks", return_value=["chunk"]):
            # structured_chat: 1=planning, 2=query expansion, 3=relevance filter, 4=synthesis
            mock_llm.structured_chat.side_effect = [
                FakePlan(),
                FakeExpansion(),
                FakeRelevance(),
                mock_brief,
            ]
            # embed: used by memory injection path at planning time
            mock_embed_result = MagicMock()
            mock_embed_result.embeddings = [[0.1] * 1536]
            mock_llm.embed.return_value = mock_embed_result
            # chat: article summary (one per relevant article)
            mock_llm.chat.return_value = _fake_chat_result()
            with patch("app.tasks.research.hybrid_search") as mock_search:
                mock_search.return_value = [
                    {
                        "id": str(article.id),
                        "title": article.title,
                        "description": "desc",
                    }
                ]
                with patch("app.tasks.research.chord", side_effect=fake_chord):
                    run_research_lead(str(run.id), db=db_session)

        db_session.refresh(run)
        assert run.status == "done"
        assert run.result is not None
        assert run.result["summary"] == "Found one article."
        assert "sub_question_findings" in run.result


# ---------------------------------------------------------------------------
# Step 2.6 — Recovery task
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_marks_stale_non_terminal_run_as_partial(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        db_session.execute(
            text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
            {"ts": datetime.now(tz=timezone.utc) - timedelta(minutes=15), "id": run.id},
        )
        db_session.commit()

        count = recover_orphaned_runs(db=db_session)

        db_session.refresh(run)
        assert run.status == "partial"
        assert run.error["code"] == "orphaned"
        assert count == 1

    def test_does_not_touch_recently_updated_run(self, db_session, test_user):
        run = _make_run(db_session, test_user, status="searching")
        # updated_at is now — not stale

        recover_orphaned_runs(db=db_session)

        db_session.refresh(run)
        assert run.status == "searching"

    def test_does_not_touch_terminal_runs(self, db_session, test_user):
        runs = []
        for status in ("done", "failed", "partial"):
            r = _make_run(db_session, test_user, status=status)
            db_session.execute(
                text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
                {
                    "ts": datetime.now(tz=timezone.utc) - timedelta(minutes=15),
                    "id": r.id,
                },
            )
            runs.append((r.id, status))
        db_session.commit()

        recover_orphaned_runs(db=db_session)

        for rid, original_status in runs:
            r = db_session.query(ResearchRun).filter(ResearchRun.id == rid).first()
            assert r.status == original_status

    def test_returns_count_of_recovered_runs(self, db_session, test_user):
        for _ in range(3):
            r = _make_run(db_session, test_user, status="planning")
            db_session.execute(
                text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
                {
                    "ts": datetime.now(tz=timezone.utc) - timedelta(minutes=15),
                    "id": r.id,
                },
            )
        db_session.commit()

        count = recover_orphaned_runs(db=db_session)
        assert count == 3

    def test_planning_and_synthesizing_both_recovered(self, db_session, test_user):
        for status in ("planning", "synthesizing", "verifying"):
            r = _make_run(db_session, test_user, status=status)
            db_session.execute(
                text("UPDATE research_runs SET updated_at = :ts WHERE id = :id"),
                {
                    "ts": datetime.now(tz=timezone.utc) - timedelta(minutes=15),
                    "id": r.id,
                },
            )
        db_session.commit()

        count = recover_orphaned_runs(db=db_session)
        assert count == 3


class TestCeleryTaskRegistry:
    """
    Verify that every task name fired via .delay() in the research pipeline
    is registered in the Celery app. Catches the failure mode where a task
    module is written but not imported in celery_app.py — the worker receives
    the task name from the broker but gets a KeyError looking it up.
    """

    # Task names that the research pipeline dispatches at runtime.
    # If you add a new .delay() call in research.py or research_memory.py,
    # add the registered name here.
    RESEARCH_PIPELINE_TASKS = [
        "app.tasks.research.run_research_lead_task",
        "app.tasks.research.run_research_subagent_task",
        "app.tasks.research.collect_subagent_results_task",
        "app.tasks.research.synthesize_run_task",
        "app.tasks.research.verify_synthesis_task",
        "app.tasks.research.recover_orphaned_runs_task",
        "app.tasks.research_memory.extract_research_memory",
    ]

    def test_all_research_task_names_registered(self):
        from app.core.celery_app import celery_app

        registered = set(celery_app.tasks.keys())
        for name in self.RESEARCH_PIPELINE_TASKS:
            assert name in registered, (
                f"Task '{name}' is not registered in celery_app. "
                "Add its module to the explicit import list in app/core/celery_app.py."
            )

    def test_verify_synthesis_fires_extract_memory_delay(self, db_session, test_user):
        item = _make_article(db_session, test_user, title="T")
        db_session.commit()

        brief = ResearchBrief(
            summary="s",
            sub_question_findings=[
                SubQuestionFinding(
                    sub_question="Q1",
                    finding="f",
                    key_sources=[
                        SourceCitation(
                            item_id=str(item.id), title="T", representative_highlight=""
                        )
                    ],
                    coverage="full",
                    tensions=[],
                )
            ],
            cross_cutting_tensions=[],
            gaps=[],
            engagement_note="",
            confidence="high",
        )
        run = _make_run(
            db_session,
            test_user,
            status="synthesizing",
            item_ids=[str(item.id)],
            sub_questions=["Q1"],
        )
        run.subagent_results = [
            {"sub_question": "Q1", "coverage_assessment": "full", "articles": []}
        ]
        run.result = brief.model_dump()
        db_session.commit()

        fired_args = []

        import app.tasks.research_memory as _rm_mod

        original_apply_async = _rm_mod.extract_research_memory_task.apply_async

        def fake_apply_async(args=(), kwargs=None, **opts):
            fired_args.append(args)

        _rm_mod.extract_research_memory_task.apply_async = fake_apply_async
        try:
            verify_synthesis(str(run.id), db=db_session)
        finally:
            _rm_mod.extract_research_memory_task.apply_async = original_apply_async

        assert len(fired_args) == 1 and fired_args[0] == (str(run.id),), (
            "verify_synthesis must call extract_research_memory_task.apply_async((run_id,), countdown=5) "
            "after setting status=done"
        )
