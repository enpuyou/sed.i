"""
TDD tests for semantic tag extraction (Phase 1 — connections plan).

Behaviors tested:
- generate_tags writes semantic (multi-word) labels to item.tags
- generate_tags calls upsert_tag_embeddings after writing tags
- LLM failure leaves item.tags unchanged
- upsert_tag_embeddings writes new labels to tag_embeddings table
- upsert_tag_embeddings is idempotent (running twice produces no duplicates)
- items with no full_text fall back to title + description without error
"""

from unittest.mock import patch

from app.models.content import ContentItem
from app.tasks.tagging import generate_tags, upsert_tag_embeddings
from app.models.tag_embedding import TagEmbedding


class TestGenerateTags:
    """generate_tags — semantic label extraction into item.tags."""

    def test_stores_semantic_labels_in_tags(self, db_session, test_user):
        """Tracer bullet: semantic labels end up in item.tags and upsert is called."""
        item = ContentItem(
            original_url="https://example.com/alignment",
            title="The Alignment Problem",
            description="Explores deceptive alignment and mesa-optimization in deep learning.",
            user_id=test_user.id,
            processing_status="completed",
            embedding=[0.1] * 1536,
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        semantic_tags = [
            "deceptive alignment",
            "mesa-optimization",
            "reward misspecification",
        ]

        with (
            patch("app.tasks.tagging.generate_tags_with_llm") as mock_llm,
            patch("app.tasks.tagging.upsert_tag_embeddings") as mock_upsert,
        ):
            mock_llm.return_value = semantic_tags
            generate_tags(str(item.id), db=db_session)

        db_session.refresh(item)
        assert "deceptive alignment" in item.tags
        assert len(item.tags) >= 3
        mock_upsert.assert_called_once()

    def test_llm_failure_leaves_tags_unchanged(self, db_session, test_user):
        """When the LLM raises, item.tags is not corrupted."""
        item = ContentItem(
            original_url="https://example.com/failure-test",
            title="Some Article",
            user_id=test_user.id,
            processing_status="completed",
            embedding=[0.1] * 1536,
            tags=["existing-tag"],
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        with patch(
            "app.tasks.tagging.generate_tags_with_llm",
            side_effect=Exception("OpenAI down"),
        ):
            generate_tags(str(item.id), db=db_session)

        db_session.refresh(item)
        assert item.tags == ["existing-tag"]

    def test_no_full_text_falls_back_to_title_description(self, db_session, test_user):
        """Articles without full_text don't error out — title+description is enough."""
        item = ContentItem(
            original_url="https://example.com/no-fulltext",
            title="Short Article",
            description="A brief description only.",
            user_id=test_user.id,
            processing_status="completed",
            embedding=[0.1] * 1536,
        )
        db_session.add(item)
        db_session.commit()

        with (
            patch("app.tasks.tagging.generate_tags_with_llm") as mock_llm,
            patch("app.tasks.tagging.upsert_tag_embeddings"),
        ):
            mock_llm.return_value = ["some concept", "another idea"]
            result = generate_tags(str(item.id), db=db_session)

        assert result["status"] != "failed"

    def test_existing_tags_passed_to_llm(self, db_session, test_user):
        """Existing user tags are forwarded to generate_tags_with_llm as context."""
        item = ContentItem(
            original_url="https://example.com/existing-tags",
            title="Some Article",
            user_id=test_user.id,
            processing_status="completed",
            embedding=[0.1] * 1536,
            tags=["machine learning", "python"],
        )
        db_session.add(item)
        db_session.commit()

        with (
            patch("app.tasks.tagging.generate_tags_with_llm") as mock_llm,
            patch("app.tasks.tagging.upsert_tag_embeddings"),
        ):
            mock_llm.return_value = [
                "machine learning",
                "gradient descent",
                "model fine-tuning",
            ]
            generate_tags(str(item.id), db=db_session)
            _, kwargs = mock_llm.call_args
            assert kwargs.get("existing_tags") == ["machine learning", "python"]


class TestUpsertTagEmbeddings:
    """upsert_tag_embeddings — writes label→embedding rows to tag_embeddings."""

    def test_writes_new_labels_to_db(self, db_session):
        """New labels are embedded and stored."""
        from app.core.llm_client import EmbedResult

        mock_embedding = [0.2] * 1536
        fake_result = EmbedResult(
            embeddings=[mock_embedding], model="text-embedding-3-small", prompt_tokens=5
        )

        with patch("app.core.llm_client.llm_client.embed", return_value=fake_result):
            upsert_tag_embeddings(["deceptive alignment"], db=db_session)

        row = (
            db_session.query(TagEmbedding)
            .filter_by(label="deceptive alignment")
            .first()
        )
        assert row is not None
        assert len(row.embedding) == 1536

    def test_is_idempotent(self, db_session):
        """Running twice with the same labels does not create duplicates."""
        from app.core.llm_client import EmbedResult

        mock_embedding = [0.3] * 1536
        fake_result = EmbedResult(
            embeddings=[mock_embedding], model="text-embedding-3-small", prompt_tokens=5
        )

        with patch("app.core.llm_client.llm_client.embed", return_value=fake_result):
            upsert_tag_embeddings(["mesa-optimization"], db=db_session)
            upsert_tag_embeddings(["mesa-optimization"], db=db_session)

        count = (
            db_session.query(TagEmbedding).filter_by(label="mesa-optimization").count()
        )
        assert count == 1

    def test_skips_already_embedded_labels(self, db_session):
        """Labels already in tag_embeddings are not re-embedded."""
        existing = TagEmbedding(label="existing concept", embedding=[0.1] * 1536)
        db_session.add(existing)
        db_session.commit()

        with patch("app.core.llm_client.llm_client.embed") as mock_embed:
            upsert_tag_embeddings(["existing concept"], db=db_session)
            mock_embed.assert_not_called()
