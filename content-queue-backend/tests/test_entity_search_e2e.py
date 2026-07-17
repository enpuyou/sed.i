"""
End-to-end tests for the entity search lane (_entity_search).

These cover the path: entity + mention + embedding written to DB → _entity_search
returns the article that mentions the entity.

Most unit tests for analyze_article already verify entity writes. This file
verifies the retrieval side — that _entity_search can actually find articles
given entity data that mirrors what analyze_article produces.

No LLM calls. Embeddings are fixed 1536-d vectors stored directly in the DB.
The query_embedding is passed directly to _entity_search so no embed API calls
are made.
"""

from __future__ import annotations

import uuid

from app.models.content import ContentItem
from app.models.entity import Entity, EntityMention
from app.core.hybrid_search import _entity_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_EMBEDDING = [0.1] * 1536


def _article(db, user, title: str = "Test Article") -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        full_text="<p>Content about various topics.</p>",
        user_id=user.id,
        processing_status="completed",
        embedding=_FAKE_EMBEDDING,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _entity(db, user, name: str, entity_type: str = "CONCEPT") -> Entity:
    e = Entity(
        user_id=user.id,
        name=name,
        entity_type=entity_type,
        embedding=_FAKE_EMBEDDING,
        article_count=1,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _mention(db, user, entity: Entity, article: ContentItem) -> EntityMention:
    m = EntityMention(
        entity_id=entity.id,
        content_item_id=article.id,
        user_id=user.id,
        context_text="mentioned in the text",
    )
    db.add(m)
    db.commit()
    return m


def _search(db, user, query: str, limit: int = 10) -> list[dict]:
    """Call _entity_search with pre-built embedding to avoid any API calls."""
    return _entity_search(query, user, db, limit=limit, query_embedding=_FAKE_EMBEDDING)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntitySearchExactMatch:
    def test_exact_name_match_returns_article(self, db_session, test_user):
        """
        Article with entity 'transformer architecture' is found by exact query.
        Exact-match path (LOWER(e.name) = LOWER(:q)) must work — query_embedding
        is passed in directly so no OpenAI calls happen.
        """
        article = _article(db_session, test_user, title="Attention Is All You Need")
        entity = _entity(db_session, test_user, "transformer architecture")
        _mention(db_session, test_user, entity, article)

        results = _search(db_session, test_user, "transformer architecture")

        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert str(article.id) in ids

    def test_entity_from_other_user_not_returned(self, db_session, test_user):
        """
        Another user's entity + mention must not appear in search results.
        """
        from app.models.user import User
        from app.core.security import get_password_hash

        other = User(
            email="other_entity_test@example.com",
            username="otheruser_entity",
            hashed_password=get_password_hash("x"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        other_article = _article(db_session, other, title="Other User Article")
        other_entity = _entity(db_session, other, "private concept")
        _mention(db_session, other, other_entity, other_article)

        results = _search(db_session, test_user, "private concept")

        # test_user has no entities — must get empty results
        assert results == []

    def test_returns_empty_when_no_entities_have_embeddings(
        self, db_session, test_user
    ):
        """
        _entity_search gates on at least one entity with a non-null embedding.
        If none exist, it returns [] rather than raising.
        """
        _article(db_session, test_user, title="Orphan Article")
        # No entity row → empty

        results = _search(db_session, test_user, "anything")

        assert results == []

    def test_match_type_is_entity(self, db_session, test_user):
        """Results from _entity_search must carry match_type='entity'."""
        article = _article(db_session, test_user, title="Graph Theory")
        entity = _entity(db_session, test_user, "graph theory")
        _mention(db_session, test_user, entity, article)

        results = _search(db_session, test_user, "graph theory")

        assert len(results) >= 1
        for r in results:
            assert r["match_type"] == "entity"

    def test_matched_via_contains_entity_name(self, db_session, test_user):
        """Each result's matched_via list must include the entity name."""
        article = _article(db_session, test_user, title="Neural Nets")
        entity = _entity(db_session, test_user, "neural network")
        _mention(db_session, test_user, entity, article)

        results = _search(db_session, test_user, "neural network")

        assert len(results) >= 1
        matched_via = results[0].get("matched_via", [])
        # matched_via is a list of strings or dicts depending on implementation
        names = [m if isinstance(m, str) else m.get("name", "") for m in matched_via]
        assert any("neural network" in n.lower() for n in names)

    def test_article_mentioned_by_matched_entity_appears_in_results(
        self, db_session, test_user
    ):
        """
        An article with multiple entity mentions appears in results when any
        of its entities exactly matches the query.
        """
        article_rich = _article(db_session, test_user, title="Rich Article")
        article_lean = _article(db_session, test_user, title="Lean Article")

        entity_a = _entity(db_session, test_user, "topic alpha")
        entity_b = _entity(db_session, test_user, "topic beta")
        entity_c = _entity(db_session, test_user, "topic gamma")

        _mention(db_session, test_user, entity_a, article_rich)
        _mention(db_session, test_user, entity_b, article_rich)
        _mention(db_session, test_user, entity_c, article_lean)

        results = _search(db_session, test_user, "topic alpha")

        returned_ids = [r["id"] for r in results]
        # article_rich is linked to entity_a (exact match) — must appear
        assert str(article_rich.id) in returned_ids
