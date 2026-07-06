"""
TDD tests for Feature A: Knowledge Graph Entity Index.

Behaviors tested:
- Entity model: upsert deduplicates by (user_id, lower(name))
- EntityMention: links entity to article, stores context sentence
- EntityRelation: links two entities with a typed relation
- extract_entities(): writes entities + mentions + relations to DB from article text
- extract_entities(): is idempotent (re-running does not duplicate rows)
- extract_entities(): LLM failure leaves DB unchanged
- extract_entities(): skips articles with no full_text gracefully
- entity_graph.get_article_entities(): returns entity ids for an article
- entity_graph.get_entity_neighbors(): returns 1-hop entity ids from seeds
- entity_graph.articles_for_entities(): returns article ids mentioning given entities
"""

from types import SimpleNamespace
from unittest.mock import patch
import uuid


from app.models.content import ContentItem
from app.models.entity import Entity, EntityMention, EntityRelation


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_article(db, user, title, text="Some article text about interesting topics."):
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


def _fake_extraction_result(entities, relations):
    """Build the structured result that extract_entities_with_llm returns."""
    return SimpleNamespace(
        entities=[
            SimpleNamespace(
                name=e["name"],
                type=e["type"],
                description=e.get("description", ""),
            )
            for e in entities
        ],
        relations=[
            SimpleNamespace(
                source=r["source"],
                target=r["target"],
                predicate=r.get("predicate", r.get("relation_type", "")),
                description=r.get("description", ""),
            )
            for r in relations
        ],
    )


# ── Model layer tests ─────────────────────────────────────────────────────────


class TestEntityModels:
    """DB schema: entities, entity_mentions, entity_relations tables."""

    def test_entity_created_and_retrieved(self, db_session, test_user):
        entity = Entity(
            user_id=test_user.id,
            name="Geoffrey Hinton",
            entity_type="PERSON",
            description="Pioneer of deep learning",
        )
        db_session.add(entity)
        db_session.commit()

        row = db_session.query(Entity).filter_by(name="Geoffrey Hinton").first()
        assert row is not None
        assert row.entity_type == "PERSON"
        assert row.user_id == test_user.id

    def test_entity_name_uniqueness_per_user(self, db_session, test_user):
        """Same name + user_id → upsert, not duplicate."""
        from app.core.entity_graph import upsert_entity

        e1 = upsert_entity(
            test_user.id,
            "backpropagation",
            "CONCEPT",
            "Gradient descent method",
            db_session,
        )
        e2 = upsert_entity(
            test_user.id,
            "Backpropagation",
            "CONCEPT",
            "Updated description",
            db_session,
        )
        db_session.commit()

        # Same entity, case-insensitive
        assert e1.id == e2.id
        count = (
            db_session.query(Entity)
            .filter(
                Entity.user_id == test_user.id,
                Entity.name.ilike("backpropagation"),
            )
            .count()
        )
        assert count == 1

    def test_entity_mention_links_entity_to_article(self, db_session, test_user):
        article = _make_article(db_session, test_user, "Deep Learning Overview")
        entity = Entity(
            user_id=test_user.id, name="neural network", entity_type="CONCEPT"
        )
        db_session.add(entity)
        db_session.commit()

        mention = EntityMention(
            entity_id=entity.id,
            content_item_id=article.id,
            user_id=test_user.id,
            context_text="The paper introduces a new neural network architecture.",
        )
        db_session.add(mention)
        db_session.commit()

        row = db_session.query(EntityMention).filter_by(entity_id=entity.id).first()
        assert row.content_item_id == article.id
        assert "neural network" in row.context_text

    def test_entity_relation_links_two_entities(self, db_session, test_user):
        e1 = Entity(user_id=test_user.id, name="Hinton", entity_type="PERSON")
        e2 = Entity(user_id=test_user.id, name="backpropagation", entity_type="CONCEPT")
        db_session.add_all([e1, e2])
        db_session.commit()

        article = _make_article(db_session, test_user, "History of Deep Learning")
        rel = EntityRelation(
            source_entity_id=e1.id,
            target_entity_id=e2.id,
            relation_type="DEVELOPED",
            description="Hinton co-developed backpropagation",
            content_item_id=article.id,
        )
        db_session.add(rel)
        db_session.commit()

        row = (
            db_session.query(EntityRelation)
            .filter_by(source_entity_id=e1.id, target_entity_id=e2.id)
            .first()
        )
        assert row.relation_type == "DEVELOPED"

    def test_entity_cascade_deletes_mentions_on_article_delete(
        self, db_session, test_user
    ):
        """EntityMention rows are deleted when their article is deleted (CASCADE)."""
        article = _make_article(db_session, test_user, "Temporary Article")
        entity = Entity(
            user_id=test_user.id, name="some concept", entity_type="CONCEPT"
        )
        db_session.add(entity)
        db_session.commit()

        mention = EntityMention(
            entity_id=entity.id,
            content_item_id=article.id,
            user_id=test_user.id,
            context_text="Context sentence.",
        )
        db_session.add(mention)
        db_session.commit()

        db_session.delete(article)
        db_session.commit()

        remaining = (
            db_session.query(EntityMention).filter_by(entity_id=entity.id).count()
        )
        assert remaining == 0


# ── extract_entities() task tests ────────────────────────────────────────────


class TestExtractEntities:
    """extract_entities() — Celery-callable function that populates entity tables."""

    def test_writes_entities_and_mentions(self, db_session, test_user):
        """Core behavior: LLM output → Entity + EntityMention rows in DB."""
        from app.tasks.entity_extraction import extract_entities

        article = _make_article(
            db_session,
            test_user,
            "Attention Is All You Need",
            "The transformer architecture was introduced by Vaswani et al. "
            "It uses self-attention mechanisms instead of recurrence.",
        )

        fake_result = _fake_extraction_result(
            entities=[
                {
                    "name": "transformer architecture",
                    "type": "CONCEPT",
                    "description": "Seq2seq model using attention",
                },
                {"name": "Vaswani", "type": "PERSON", "description": "Lead author"},
                {
                    "name": "self-attention",
                    "type": "CONCEPT",
                    "description": "Attention over own sequence",
                },
            ],
            relations=[
                {
                    "source": "Vaswani",
                    "target": "transformer architecture",
                    "relation_type": "INTRODUCED",
                },
                {
                    "source": "transformer architecture",
                    "target": "self-attention",
                    "relation_type": "USES",
                },
            ],
        )

        with patch(
            "app.tasks.entity_extraction.extract_entities_with_llm",
            return_value=fake_result,
        ):
            result = extract_entities(str(article.id), db=db_session)

        assert result["status"] == "completed"
        assert result["entities_written"] == 3
        assert result["relations_written"] == 2

        entities = db_session.query(Entity).filter_by(user_id=test_user.id).all()
        names = {e.name for e in entities}
        assert "transformer architecture" in names
        assert "Vaswani" in names

        mentions = (
            db_session.query(EntityMention).filter_by(content_item_id=article.id).all()
        )
        assert len(mentions) == 3

    def test_writes_entity_relations(self, db_session, test_user):
        """Relations between entities are persisted."""
        from app.tasks.entity_extraction import extract_entities

        article = _make_article(db_session, test_user, "Hinton and Backprop")

        fake_result = _fake_extraction_result(
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

        with patch(
            "app.tasks.entity_extraction.extract_entities_with_llm",
            return_value=fake_result,
        ):
            extract_entities(str(article.id), db=db_session)

        hinton = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="Hinton")
            .first()
        )
        backprop = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="backpropagation")
            .first()
        )
        assert hinton and backprop

        rel = (
            db_session.query(EntityRelation)
            .filter_by(
                source_entity_id=hinton.id,
                target_entity_id=backprop.id,
            )
            .first()
        )
        assert rel is not None
        assert rel.relation_type == "DEVELOPED"

    def test_idempotent_does_not_duplicate_entities(self, db_session, test_user):
        """Running extract_entities twice on the same article → no duplicate entities."""
        from app.tasks.entity_extraction import extract_entities

        article = _make_article(db_session, test_user, "Idempotency Test")

        fake_result = _fake_extraction_result(
            entities=[{"name": "attention mechanism", "type": "CONCEPT"}],
            relations=[],
        )

        with patch(
            "app.tasks.entity_extraction.extract_entities_with_llm",
            return_value=fake_result,
        ):
            extract_entities(str(article.id), db=db_session)
            extract_entities(str(article.id), db=db_session)

        count = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="attention mechanism")
            .count()
        )
        assert count == 1

        # Mentions also should not double-up for the same article
        mention_count = (
            db_session.query(EntityMention)
            .filter_by(content_item_id=article.id)
            .count()
        )
        assert mention_count == 1

    def test_llm_failure_leaves_db_unchanged(self, db_session, test_user):
        """When LLM raises, no partial entity rows are written."""
        from app.tasks.entity_extraction import extract_entities

        article = _make_article(db_session, test_user, "LLM Failure Test")

        with patch(
            "app.tasks.entity_extraction.extract_entities_with_llm",
            side_effect=Exception("OpenAI timeout"),
        ):
            result = extract_entities(str(article.id), db=db_session)

        assert result["status"] == "failed"
        assert db_session.query(Entity).filter_by(user_id=test_user.id).count() == 0

    def test_skips_article_not_found(self, db_session, test_user):
        from app.tasks.entity_extraction import extract_entities

        result = extract_entities(str(uuid.uuid4()), db=db_session)
        assert result["status"] == "not_found"

    def test_entity_names_deduplicated_across_articles(self, db_session, test_user):
        """Same entity name appearing in two articles → one Entity, two EntityMentions."""
        from app.tasks.entity_extraction import extract_entities

        article1 = _make_article(db_session, test_user, "Article One")
        article2 = _make_article(db_session, test_user, "Article Two")

        fake = _fake_extraction_result(
            entities=[{"name": "reinforcement learning", "type": "CONCEPT"}],
            relations=[],
        )

        with patch(
            "app.tasks.entity_extraction.extract_entities_with_llm", return_value=fake
        ):
            extract_entities(str(article1.id), db=db_session)
            extract_entities(str(article2.id), db=db_session)

        entity_count = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="reinforcement learning")
            .count()
        )
        assert entity_count == 1

        mention_count = (
            db_session.query(EntityMention)
            .join(Entity)
            .filter(
                Entity.name == "reinforcement learning",
                Entity.user_id == test_user.id,
            )
            .count()
        )
        assert mention_count == 2


# ── entity_graph access layer tests ──────────────────────────────────────────


class TestEntityGraph:
    """entity_graph.py — thin access layer over the three entity tables."""

    def _seed_graph(self, db, user):
        """Seed a small entity graph: two articles, three entities, two relations."""
        a1 = _make_article(db, user, "Transformers paper")
        a2 = _make_article(db, user, "BERT paper")

        e_vaswani = Entity(user_id=user.id, name="Vaswani", entity_type="PERSON")
        e_transformer = Entity(
            user_id=user.id, name="transformer", entity_type="CONCEPT"
        )
        e_bert = Entity(user_id=user.id, name="BERT", entity_type="CONCEPT")
        db.add_all([e_vaswani, e_transformer, e_bert])
        db.commit()

        db.add_all(
            [
                EntityMention(
                    entity_id=e_vaswani.id,
                    content_item_id=a1.id,
                    user_id=user.id,
                    context_text="Vaswani et al.",
                ),
                EntityMention(
                    entity_id=e_transformer.id,
                    content_item_id=a1.id,
                    user_id=user.id,
                    context_text="transformer model",
                ),
                EntityMention(
                    entity_id=e_transformer.id,
                    content_item_id=a2.id,
                    user_id=user.id,
                    context_text="based on transformer",
                ),
                EntityMention(
                    entity_id=e_bert.id,
                    content_item_id=a2.id,
                    user_id=user.id,
                    context_text="BERT is pretrained",
                ),
            ]
        )
        db.add(
            EntityRelation(
                source_entity_id=e_vaswani.id,
                target_entity_id=e_transformer.id,
                relation_type="INTRODUCED",
                content_item_id=a1.id,
            )
        )
        db.add(
            EntityRelation(
                source_entity_id=e_transformer.id,
                target_entity_id=e_bert.id,
                relation_type="ENABLES",
                content_item_id=a2.id,
            )
        )
        db.commit()
        return a1, a2, e_vaswani, e_transformer, e_bert

    def test_get_article_entities_returns_entity_ids(self, db_session, test_user):
        from app.core.entity_graph import get_article_entities

        a1, a2, e_vaswani, e_transformer, e_bert = self._seed_graph(
            db_session, test_user
        )

        entity_ids = get_article_entities(a1.id, db_session)
        assert e_vaswani.id in entity_ids
        assert e_transformer.id in entity_ids
        assert e_bert.id not in entity_ids

    def test_get_entity_neighbors_returns_1hop(self, db_session, test_user):
        """1-hop neighbors: entities directly connected via EntityRelation."""
        from app.core.entity_graph import get_entity_neighbors

        a1, a2, e_vaswani, e_transformer, e_bert = self._seed_graph(
            db_session, test_user
        )

        # Vaswani → transformer (INTRODUCED)
        neighbors = get_entity_neighbors([e_vaswani.id], db_session)
        assert e_transformer.id in neighbors
        assert e_bert.id not in neighbors  # 2 hops away

    def test_get_entity_neighbors_two_hops(self, db_session, test_user):
        """2-hop traversal reaches entities 2 steps away."""
        from app.core.entity_graph import get_entity_neighbors

        a1, a2, e_vaswani, e_transformer, e_bert = self._seed_graph(
            db_session, test_user
        )

        neighbors_2hop = get_entity_neighbors([e_vaswani.id], db_session, hops=2)
        assert e_transformer.id in neighbors_2hop
        assert e_bert.id in neighbors_2hop

    def test_articles_for_entities_returns_article_ids(self, db_session, test_user):
        """Given entity ids, return articles that mention any of them."""
        from app.core.entity_graph import articles_for_entities

        a1, a2, e_vaswani, e_transformer, e_bert = self._seed_graph(
            db_session, test_user
        )

        # transformer appears in both articles
        article_ids = articles_for_entities([e_transformer.id], db_session)
        assert a1.id in article_ids
        assert a2.id in article_ids

    def test_articles_for_entities_excludes_given_article(self, db_session, test_user):
        """exclude_item_id omits the source article from results."""
        from app.core.entity_graph import articles_for_entities

        a1, a2, e_vaswani, e_transformer, e_bert = self._seed_graph(
            db_session, test_user
        )

        article_ids = articles_for_entities(
            [e_transformer.id], db_session, exclude_item_id=a1.id
        )
        assert a1.id not in article_ids
        assert a2.id in article_ids

    def test_upsert_entity_returns_existing_on_duplicate(self, db_session, test_user):
        """upsert_entity with same name+user returns same row, doesn't duplicate."""
        from app.core.entity_graph import upsert_entity

        e1 = upsert_entity(
            test_user.id,
            "Gradient Descent",
            "CONCEPT",
            "Optimization method",
            db_session,
        )
        db_session.commit()
        e2 = upsert_entity(
            test_user.id, "gradient descent", "CONCEPT", "Updated desc", db_session
        )
        db_session.commit()

        assert e1.id == e2.id
        assert db_session.query(Entity).filter_by(user_id=test_user.id).count() == 1
