"""
Tests for embed_new_entities and the entity search lane in hybrid_search.

Behaviors tested:
- embed_new_entities embeds entities with no vector, writes to entities.embedding
- embed_new_entities skips entities that already have embeddings (idempotent)
- embed_new_entities returns 'nothing_to_embed' when all entities are already embedded
- _entity_search returns [] when no entity embeddings exist (graceful no-op)
- _entity_search returns articles connected via entity graph
- mode="full" hybrid_search includes entity lane results
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch


from app.models.content import ContentItem
from app.models.entity import Entity, EntityMention
from app.core.llm_client import EmbedResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_article(db, user, title="Test Article"):
    item = ContentItem(
        original_url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        full_text="Some article text.",
        user_id=user.id,
        processing_status="completed",
        embedding=[0.1] * 1536,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _make_entity(db, user, name, entity_type="CONCEPT", embedding=None):
    e = Entity(
        user_id=user.id,
        name=name,
        entity_type=entity_type,
        description=f"Description of {name}",
        embedding=embedding,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _link(db, entity, article, user):
    m = EntityMention(
        entity_id=entity.id,
        content_item_id=article.id,
        user_id=user.id,
        context_text="",
    )
    db.add(m)
    db.commit()


_FAKE_EMBED = EmbedResult(
    embeddings=[[0.5] * 1536],
    model="text-embedding-3-small",
    prompt_tokens=5,
)
_FAKE_EMBED_BATCH = EmbedResult(
    embeddings=[[0.5] * 1536, [0.6] * 1536],
    model="text-embedding-3-small",
    prompt_tokens=10,
)


# ── embed_new_entities ────────────────────────────────────────────────────────


class TestEmbedNewEntities:
    def test_embeds_entities_without_vectors(self, db_session, test_user):
        e = _make_entity(db_session, test_user, "attention mechanism")
        assert e.embedding is None

        with patch("app.core.llm_client.llm_client.embed", return_value=_FAKE_EMBED):
            from app.tasks.entity_embedding import embed_new_entities

            result = embed_new_entities(str(test_user.id), db=db_session)

        assert result["status"] == "completed"
        assert result["embedded"] == 1
        db_session.refresh(e)
        assert e.embedding is not None
        assert len(e.embedding) == 1536

    def test_skips_already_embedded_entities(self, db_session, test_user):
        _make_entity(db_session, test_user, "transformer", embedding=[0.9] * 1536)

        with patch("app.core.llm_client.llm_client.embed") as mock_embed:
            from app.tasks.entity_embedding import embed_new_entities

            result = embed_new_entities(str(test_user.id), db=db_session)

        assert result["status"] == "nothing_to_embed"
        mock_embed.assert_not_called()

    def test_embeds_multiple_entities_in_batch(self, db_session, test_user):
        _make_entity(db_session, test_user, "backpropagation")
        _make_entity(db_session, test_user, "gradient descent")

        with patch(
            "app.core.llm_client.llm_client.embed",
            return_value=_FAKE_EMBED_BATCH,
        ):
            from app.tasks.entity_embedding import embed_new_entities

            result = embed_new_entities(str(test_user.id), db=db_session)

        assert result["embedded"] == 2

    def test_embed_failure_leaves_db_unchanged(self, db_session, test_user):
        _make_entity(db_session, test_user, "RLHF")

        with patch(
            "app.core.llm_client.llm_client.embed",
            side_effect=Exception("API down"),
        ):
            from app.tasks.entity_embedding import embed_new_entities

            result = embed_new_entities(str(test_user.id), db=db_session)

        assert result["status"] == "failed"
        entity = (
            db_session.query(Entity)
            .filter_by(user_id=test_user.id, name="RLHF")
            .first()
        )
        assert entity.embedding is None


# ── _entity_search ────────────────────────────────────────────────────────────


class TestEntitySearch:
    def test_returns_empty_when_no_entity_embeddings(self, db_session, test_user):
        """No entity embeddings → graceful empty result, no error."""
        from app.core.hybrid_search import _entity_search

        # Entity exists but has no embedding
        _make_entity(db_session, test_user, "reinforcement learning")

        with patch("app.core.embedding_cache.call_embed", return_value=[0.1] * 1536):
            results = _entity_search(
                "reinforcement learning", test_user, db_session, limit=10
            )

        assert results == []

    def test_returns_articles_connected_via_entity_graph(self, db_session, test_user):
        """Entity search surfaces articles linked to matching entity nodes."""
        article = _make_article(db_session, test_user, "Transformers paper")
        entity = _make_entity(
            db_session,
            test_user,
            "transformer architecture",
            embedding=[0.8] * 1536,
        )
        _link(db_session, entity, article, test_user)

        query_embedding = [0.8] * 1536  # same direction → high cosine sim

        with (
            patch("app.core.embedding_cache.call_embed", return_value=query_embedding),
            patch(
                "app.core.entity_graph.get_entity_neighbors",
                return_value=[],
            ),
        ):
            from app.core.hybrid_search import _entity_search

            results = _entity_search(
                "transformer architecture", test_user, db_session, limit=10
            )

        assert any(r["id"] == str(article.id) for r in results)
        assert all(r["match_type"] == "entity" for r in results)

    def test_entity_lane_included_in_full_mode(self, db_session, test_user):
        """mode='full' calls _entity_search and includes entity-sourced articles."""
        from app.core.hybrid_search import hybrid_search

        article = _make_article(db_session, test_user, "RLHF and alignment")
        entity = _make_entity(
            db_session,
            test_user,
            "reinforcement learning from human feedback",
            embedding=[0.7] * 1536,
        )
        _link(db_session, entity, article, test_user)

        # Patch _entity_search to return our article deterministically
        entity_result = {
            "id": str(article.id),
            "title": article.title,
            "score": 1.0,
            "match_type": "entity",
        }

        with patch(
            "app.core.hybrid_search._entity_search",
            return_value=[entity_result],
        ) as mock_entity:
            results = hybrid_search(
                query="RLHF human feedback",
                user=test_user,
                db=db_session,
                mode="full",
            )

        mock_entity.assert_called_once()
        assert any(r["id"] == str(article.id) for r in results)


# ── Full pipeline E2E ─────────────────────────────────────────────────────────


class TestEntityPipelineE2E:
    """
    End-to-end test: analyze_article → embed_new_entities → _entity_search.

    Verifies the full loop without mocking intermediate tasks:
      1. analyze_article writes entities and mentions
      2. embed_new_entities writes embeddings to those entities
      3. _entity_search returns the article when queried on the entity concept

    The LLM call is mocked (no API key in test env); embeddings are synthetic
    but directionally consistent so cosine similarity returns a high score.
    """

    def test_analyze_embed_search_roundtrip(self, db_session, test_user):
        from app.core.llm_client import EmbedResult
        from app.tasks.article_analysis import analyze_article
        from app.tasks.entity_embedding import embed_new_entities
        from app.core.hybrid_search import _entity_search

        article = _make_article(
            db_session, test_user, title="Attention Is All You Need"
        )
        article.full_text = (
            "Transformers replaced recurrent networks using self-attention."
        )
        db_session.commit()

        # Entity and query share direction → high cosine similarity
        entity_embedding = [0.9] * 1536
        query_embedding = [0.9] * 1536

        fake_analysis = SimpleNamespace(
            domain_tags=["NLP research"],
            concept_tags=["attention mechanism"],
            entities=[
                SimpleNamespace(
                    name="self-attention",
                    type="CONCEPT",
                    description="Core mechanism replacing recurrence in transformers",
                    mention_context="Transformers replaced recurrent networks using self-attention.",
                ),
            ],
            relations=[],
        )

        with (
            patch(
                "app.tasks.article_analysis.analyze_article_with_llm",
                return_value=fake_analysis,
            ),
            patch(
                "app.core.llm_client.llm_client.embed",
                return_value=EmbedResult(
                    embeddings=[[0.1] * 1536],  # tag embeddings — direction irrelevant
                    model="text-embedding-3-small",
                    prompt_tokens=5,
                ),
            ),
        ):
            result = analyze_article(str(article.id), db=db_session)

        assert result["status"] == "completed"
        assert result["entities_written"] >= 1

        # Step 2: embed entity nodes with the directional vector
        with patch(
            "app.core.llm_client.llm_client.embed",
            return_value=EmbedResult(
                embeddings=[entity_embedding],
                model="text-embedding-3-small",
                prompt_tokens=10,
            ),
        ):
            embed_result = embed_new_entities(str(test_user.id), db=db_session)

        assert embed_result["embedded"] >= 1

        # Step 3: entity search should surface the article
        with patch("app.core.embedding_cache.call_embed", return_value=query_embedding):
            results = _entity_search(
                "self-attention transformers", test_user, db_session, limit=10
            )

        assert any(
            r["id"] == str(article.id) for r in results
        ), f"Article not found in entity search results. Got: {[r['id'] for r in results]}"
        assert all(r["match_type"] == "entity" for r in results)


# ── _score_entity_articles unit tests ────────────────────────────────────────


class TestScoreEntityArticles:
    """
    Pure-function tests for _score_entity_articles. No DB required.

    Covers the IDF dampening formula and capped-sum scoring:
      contribution = sim / log2(2 + article_count)
      score        = best + 0.3 * sum(rest)
    """

    def _row(self, article_id, entity_id, entity_article_count):
        return SimpleNamespace(
            article_id=article_id,
            entity_id=entity_id,
            entity_article_count=entity_article_count,
        )

    def test_single_entity_single_article(self):
        import math
        from app.core.hybrid_search import _score_entity_articles

        scores, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "ent1", 3)],
            sim_map={"ent1": 0.9},
        )
        expected = 0.9 / math.log2(2 + 3)
        assert abs(scores["art1"] - expected) < 1e-9

    def test_hub_entity_scores_lower_than_precise_entity(self):
        """Entity in 50 articles scores lower than entity in 1 article at same sim."""
        from app.core.hybrid_search import _score_entity_articles

        scores, _ = _score_entity_articles(
            mention_rows=[
                self._row("art_hub", "ent_hub", 50),
                self._row("art_precise", "ent_precise", 1),
            ],
            sim_map={"ent_hub": 0.9, "ent_precise": 0.9},
        )
        assert scores["art_precise"] > scores["art_hub"]

    def test_multiple_entities_same_article_capped_sum(self):
        """best + 0.3 * sum(rest) — not raw sum."""
        import math
        from app.core.hybrid_search import _score_entity_articles

        # Two entities, same article, same count
        c1 = 0.8 / math.log2(2 + 1)
        c2 = 0.5 / math.log2(2 + 1)
        best, rest = max(c1, c2), min(c1, c2)
        expected = best + 0.3 * rest

        scores, _ = _score_entity_articles(
            mention_rows=[
                self._row("art1", "ent1", 1),
                self._row("art1", "ent2", 1),
            ],
            sim_map={"ent1": 0.8, "ent2": 0.5},
        )
        assert abs(scores["art1"] - expected) < 1e-9

    def test_neighbor_with_lower_sim_scores_below_anchor(self):
        """Neighbor entity (real low sim) ranks below anchor (high sim) on same article."""
        from app.core.hybrid_search import _score_entity_articles

        # Anchor and neighbor both mention the same article
        scores_anchor, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "anchor", 2)],
            sim_map={"anchor": 0.85},
        )
        scores_neighbor, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "neighbor", 2)],
            sim_map={"neighbor": 0.20},
        )
        assert scores_anchor["art1"] > scores_neighbor["art1"]

    def test_entity_not_in_sim_map_is_excluded(self):
        """Mention rows for entities absent from sim_map produce no contribution."""
        from app.core.hybrid_search import _score_entity_articles

        scores, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "unknown_ent", 1)],
            sim_map={},
        )
        assert "art1" not in scores

    def test_empty_mention_rows_returns_empty(self):
        from app.core.hybrid_search import _score_entity_articles

        scores, _ = _score_entity_articles(mention_rows=[], sim_map={"ent1": 0.9})
        assert scores == {}

    def test_custom_secondary_weight(self):
        """secondary_weight parameter is respected."""
        import math
        from app.core.hybrid_search import _score_entity_articles

        c1 = 0.9 / math.log2(3)
        c2 = 0.6 / math.log2(3)
        best, rest = max(c1, c2), min(c1, c2)

        scores_default, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "e1", 1), self._row("art1", "e2", 1)],
            sim_map={"e1": 0.9, "e2": 0.6},
        )
        scores_zero, _ = _score_entity_articles(
            mention_rows=[self._row("art1", "e1", 1), self._row("art1", "e2", 1)],
            sim_map={"e1": 0.9, "e2": 0.6},
            secondary_weight=0.0,
        )
        assert abs(scores_default["art1"] - (best + 0.3 * rest)) < 1e-9
        assert abs(scores_zero["art1"] - best) < 1e-9
