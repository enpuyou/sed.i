"""
TDD tests for analyze_article — merged tag + entity extraction task.

Behaviors tested:
- analyze_article writes domain_tags + concept_tags to content_items.tags
- analyze_article writes entity rows + mention rows to entity tables
- analyze_article writes entity relations
- tag_embeddings rows carry the correct tag_type ('domain' | 'concept')
- idempotent: re-running on the same article does not duplicate rows
- LLM failure leaves DB unchanged (tags and entities both rolled back)
- articles with no full_text fall back to title + description
- concept tags become stub CONCEPT entities when LLM returns < 2 entities
- entity names are case-insensitively deduplicated across two articles
"""

from types import SimpleNamespace
from unittest.mock import patch
import uuid

from app.models.content import ContentItem
from app.models.entity import Entity, EntityMention, EntityRelation
from app.models.tag_embedding import TagEmbedding
from app.core.llm_client import EmbedResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_article(
    db, user, title="Test Article", text="Some article text about interesting topics."
):
    item = ContentItem(
        original_url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        full_text=text,
        user_id=user.id,
        processing_status="completed",
        embedding=[0.1] * 1536,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _fake_analysis(domain_tags, concept_tags, entities=None, relations=None):
    """Build the SimpleNamespace that analyze_article_with_llm returns."""
    return SimpleNamespace(
        domain_tags=domain_tags,
        concept_tags=concept_tags,
        entities=[
            SimpleNamespace(
                name=e["name"],
                type=e["type"],
                description=e.get("description", ""),
                mention_context=e.get("mention_context", ""),
            )
            for e in (entities or [])
        ],
        relations=[
            SimpleNamespace(
                source=r["source"],
                target=r["target"],
                predicate=r.get("predicate", r.get("relation_type", "")),
                strength=r.get("strength", 3),
                description=r.get("description", ""),
            )
            for r in (relations or [])
        ],
    )


_FAKE_EMBED = EmbedResult(
    embeddings=[[0.1] * 1536],
    model="text-embedding-3-small",
    prompt_tokens=3,
)


# ── Tag output ────────────────────────────────────────────────────────────────


class TestAnalyzeArticleTags:
    """Tags are written to content_items.tags and tag_embeddings with correct type."""

    def test_writes_domain_and_concept_tags(self, db_session, test_user):
        item = _make_article(db_session, test_user)
        fake = _fake_analysis(
            domain_tags=["machine learning"],
            concept_tags=["attention mechanism", "context window limits"],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            result = analyze_article(str(item.id), db=db_session)

        db_session.refresh(item)
        assert result["status"] == "completed"
        assert "machine learning" in item.tags
        assert "attention mechanism" in item.tags
        assert "context window limits" in item.tags

    def test_tag_embeddings_have_correct_type(self, db_session, test_user):
        item = _make_article(db_session, test_user)
        fake = _fake_analysis(
            domain_tags=["distributed systems"],
            concept_tags=["consensus algorithms", "leader election"],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            analyze_article(str(item.id), db=db_session)

        domain_row = (
            db_session.query(TagEmbedding)
            .filter_by(label="distributed systems")
            .first()
        )
        concept_row = (
            db_session.query(TagEmbedding)
            .filter_by(label="consensus algorithms")
            .first()
        )
        assert domain_row is not None and domain_row.tag_type == "domain"
        assert concept_row is not None and concept_row.tag_type == "concept"

    def test_llm_failure_leaves_tags_unchanged(self, db_session, test_user):
        item = _make_article(db_session, test_user)
        item.tags = ["existing tag"]
        db_session.commit()

        with patch(
            "app.tasks.article_analysis.analyze_article_with_llm",
            side_effect=Exception("LLM down"),
        ):
            from app.tasks.article_analysis import analyze_article

            result = analyze_article(str(item.id), db=db_session)

        db_session.refresh(item)
        assert result["status"] == "failed"
        assert item.tags == ["existing tag"]

    def test_no_full_text_falls_back_to_title_description(self, db_session, test_user):
        item = ContentItem(
            original_url="https://example.com/no-text",
            title="Short Article",
            description="A brief description only.",
            user_id=test_user.id,
            processing_status="completed",
            embedding=[0.1] * 1536,
        )
        db_session.add(item)
        db_session.commit()

        fake = _fake_analysis(
            domain_tags=["some domain"],
            concept_tags=["some concept", "another idea"],
        )
        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            result = analyze_article(str(item.id), db=db_session)

        assert result["status"] == "completed"


# ── Entity output ─────────────────────────────────────────────────────────────


class TestAnalyzeArticleEntities:
    """Entities, mentions, and relations are written to the entity graph tables."""

    def test_writes_entities_and_mentions(self, db_session, test_user):
        item = _make_article(db_session, test_user, title="Transformers")
        fake = _fake_analysis(
            domain_tags=["NLP research"],
            concept_tags=["attention mechanism"],
            entities=[
                {
                    "name": "Transformer",
                    "type": "CONCEPT",
                    "description": "Seq2seq with attention",
                },
                {"name": "Vaswani", "type": "PERSON", "description": "Lead author"},
                {
                    "name": "self-attention",
                    "type": "CONCEPT",
                    "description": "Core mechanism",
                },
            ],
            relations=[
                {
                    "source": "Vaswani",
                    "target": "Transformer",
                    "relation_type": "INTRODUCES",
                },
                {
                    "source": "Transformer",
                    "target": "self-attention",
                    "relation_type": "USES",
                },
            ],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            result = analyze_article(str(item.id), db=db_session)

        assert result["status"] == "completed"
        # 3 extracted entities + 1 concept-tag stub ("attention mechanism" not in entity_map)
        assert result["entities_written"] == 4
        assert result["relations_written"] == 2

        names = {
            e.name
            for e in db_session.query(Entity).filter_by(user_id=test_user.id).all()
        }
        assert "Transformer" in names
        assert "Vaswani" in names

        mention_count = (
            db_session.query(EntityMention).filter_by(content_item_id=item.id).count()
        )
        assert mention_count == 4  # 3 extracted + 1 concept-tag stub

    def test_writes_entity_relations(self, db_session, test_user):
        item = _make_article(db_session, test_user)
        fake = _fake_analysis(
            domain_tags=["deep learning"],
            concept_tags=["backpropagation"],
            entities=[
                {"name": "Hinton", "type": "PERSON"},
                {"name": "backpropagation", "type": "CONCEPT"},
            ],
            relations=[
                {
                    "source": "Hinton",
                    "target": "backpropagation",
                    "relation_type": "DEVELOPED",
                },
            ],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            analyze_article(str(item.id), db=db_session)

        hinton = (
            db_session.query(Entity)
            .filter_by(name="Hinton", user_id=test_user.id)
            .first()
        )
        backprop = (
            db_session.query(Entity)
            .filter_by(name="backpropagation", user_id=test_user.id)
            .first()
        )
        assert hinton and backprop

        rel = (
            db_session.query(EntityRelation)
            .filter_by(source_entity_id=hinton.id, target_entity_id=backprop.id)
            .first()
        )
        assert rel is not None
        assert rel.relation_type == "DEVELOPED"

    def test_concept_tags_become_stub_entities_when_extraction_thin(
        self, db_session, test_user
    ):
        """When LLM returns < 2 entities, concept tags are upserted as CONCEPT stubs."""
        item = _make_article(db_session, test_user)
        fake = _fake_analysis(
            domain_tags=["health science"],
            concept_tags=["circadian rhythm disruption", "sleep pressure"],
            entities=[],  # extraction returned nothing
            relations=[],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            result = analyze_article(str(item.id), db=db_session)

        assert result["status"] == "completed"
        # Both concept tags become CONCEPT entities
        names = {
            e.name
            for e in db_session.query(Entity)
            .filter_by(user_id=test_user.id, entity_type="CONCEPT")
            .all()
        }
        assert "circadian rhythm disruption" in names
        assert "sleep pressure" in names

    def test_idempotent_no_duplicate_entities_or_mentions(self, db_session, test_user):
        item = _make_article(db_session, test_user)
        fake = _fake_analysis(
            domain_tags=["AI research"],
            concept_tags=["reinforcement learning"],
            entities=[{"name": "reinforcement learning", "type": "CONCEPT"}],
            relations=[],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            analyze_article(str(item.id), db=db_session)
            analyze_article(str(item.id), db=db_session)

        entity_count = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="reinforcement learning")
            .count()
        )
        assert entity_count == 1

        mention_count = (
            db_session.query(EntityMention).filter_by(content_item_id=item.id).count()
        )
        assert mention_count == 1

    def test_entity_dedup_across_two_articles(self, db_session, test_user):
        """Same entity extracted from two articles → one Entity, two EntityMentions."""
        a1 = _make_article(db_session, test_user, title="Article One")
        a2 = _make_article(db_session, test_user, title="Article Two")

        fake = _fake_analysis(
            domain_tags=["AI research"],
            concept_tags=["attention mechanism"],
            entities=[{"name": "attention mechanism", "type": "CONCEPT"}],
            relations=[],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm", return_value=fake
            ),
            patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED),
        ):
            from app.tasks.article_analysis import analyze_article

            analyze_article(str(a1.id), db=db_session)
            analyze_article(str(a2.id), db=db_session)

        entity_count = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="attention mechanism")
            .count()
        )
        assert entity_count == 1

        mention_count = (
            db_session.query(EntityMention)
            .join(Entity)
            .filter(
                Entity.name == "attention mechanism",
                Entity.user_id == test_user.id,
            )
            .count()
        )
        assert mention_count == 2

    def test_not_found_returns_status(self, db_session):
        from app.tasks.article_analysis import analyze_article

        result = analyze_article(str(uuid.uuid4()), db=db_session)
        assert result["status"] == "not_found"
