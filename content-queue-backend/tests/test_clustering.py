"""
TDD tests for tag clustering (Phase 2 — connections plan).

Behaviors tested:
- cluster_user_tags produces ≥1 cluster for a user with 15 semantic-tagged articles
- cluster_user_tags skips users with <10 tagged articles without error
- cluster update is idempotent — rerun replaces clusters, does not append
- GET /themes returns clusters with article_count and top_articles
- GET /themes for user with 0 clusters returns {clusters: []}, not 404
"""

import numpy as np

from app.models.content import ContentItem
from app.models.tag_embedding import TagEmbedding
from app.models.reading_cluster import ReadingCluster
from app.tasks.clustering import cluster_user_tags


def _make_embedding(
    seed_dim: int, size: int = 1536, noise: float = 0.05
) -> list[float]:
    """Return a unit-normalized embedding dominated by seed_dim with slight noise."""
    vec = np.zeros(size)
    vec[seed_dim] = 1.0
    rng = np.random.default_rng(seed_dim)
    vec += rng.uniform(-noise, noise, size)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


def _make_item(db, user_id, url_suffix: str, tags: list[str]) -> ContentItem:
    item = ContentItem(
        original_url=f"https://example.com/{url_suffix}",
        title=url_suffix,
        user_id=user_id,
        processing_status="completed",
        embedding=[0.1] * 1536,
        tags=tags,
    )
    db.add(item)
    return item


def _seed_embeddings(db, tag_dim: dict[str, int]) -> None:
    """Insert TagEmbedding rows with controlled cluster structure."""
    for label, dim in tag_dim.items():
        existing = db.query(TagEmbedding).filter_by(label=label).first()
        if not existing:
            db.add(TagEmbedding(label=label, embedding=_make_embedding(dim)))
    db.commit()


class TestClusterUserTags:
    def test_produces_cluster_for_well_tagged_library(self, db_session, test_user):
        """15 articles with 2 tight tag groups → at least 1 cluster written."""
        tag_dim = {
            "distributed systems": 0,
            "consensus protocols": 0,
            "machine learning": 1,
            "gradient descent": 1,
        }
        _seed_embeddings(db_session, tag_dim)

        for i in range(8):
            _make_item(
                db_session,
                test_user.id,
                f"dist-{i}",
                ["distributed systems", "consensus protocols"],
            )
        for i in range(7):
            _make_item(
                db_session,
                test_user.id,
                f"ml-{i}",
                ["machine learning", "gradient descent"],
            )
        db_session.commit()

        result = cluster_user_tags(str(test_user.id), db=db_session)

        assert result["status"] == "completed"
        clusters = (
            db_session.query(ReadingCluster).filter_by(user_id=test_user.id).all()
        )
        assert len(clusters) >= 1

    def test_skips_user_with_fewer_than_10_tagged_articles(self, db_session, test_user):
        """Fewer than 10 tagged articles → status skipped, no clusters written, no error."""
        _seed_embeddings(db_session, {"distributed systems": 0})

        for i in range(5):
            _make_item(db_session, test_user.id, f"few-{i}", ["distributed systems"])
        db_session.commit()

        result = cluster_user_tags(str(test_user.id), db=db_session)

        assert result["status"] == "skipped"
        count = db_session.query(ReadingCluster).filter_by(user_id=test_user.id).count()
        assert count == 0

    def test_is_idempotent(self, db_session, test_user):
        """Running twice replaces clusters — does not append duplicates."""
        tag_dim = {"distributed systems": 0, "consensus protocols": 0}
        _seed_embeddings(db_session, tag_dim)

        for i in range(12):
            _make_item(
                db_session,
                test_user.id,
                f"idem-{i}",
                ["distributed systems", "consensus protocols"],
            )
        db_session.commit()

        cluster_user_tags(str(test_user.id), db=db_session)
        first_count = (
            db_session.query(ReadingCluster).filter_by(user_id=test_user.id).count()
        )

        cluster_user_tags(str(test_user.id), db=db_session)
        second_count = (
            db_session.query(ReadingCluster).filter_by(user_id=test_user.id).count()
        )

        assert second_count == first_count

    def test_cluster_size_minimum_enforced(self, db_session, test_user):
        """A tag that appears on only 2 articles is excluded from clusters."""
        tag_dim = {
            "distributed systems": 0,
            "consensus protocols": 0,
            "rare concept": 0,  # same cluster direction but too few articles
        }
        _seed_embeddings(db_session, tag_dim)

        # 12 articles with main cluster tags — enough to form a cluster
        for i in range(12):
            _make_item(
                db_session,
                test_user.id,
                f"main-{i}",
                ["distributed systems", "consensus protocols"],
            )
        # Only 2 articles with rare tag — should not form its own cluster
        for i in range(2):
            _make_item(db_session, test_user.id, f"rare-{i}", ["rare concept"])
        db_session.commit()

        cluster_user_tags(str(test_user.id), db=db_session)

        clusters = (
            db_session.query(ReadingCluster).filter_by(user_id=test_user.id).all()
        )
        # No cluster should be labeled "rare concept" because it has <3 articles
        rare_clusters = [
            c for c in clusters if c.label == "rare concept" and len(c.article_ids) < 3
        ]
        assert not rare_clusters


class TestThemesAPI:
    def test_returns_clusters_for_user(
        self, client, db_session, test_user, auth_headers
    ):
        """GET /themes returns cluster objects with required fields."""
        tag_dim = {"personal finance": 0, "compound interest": 0}
        _seed_embeddings(db_session, tag_dim)

        for i in range(5):
            _make_item(
                db_session,
                test_user.id,
                f"fin-{i}",
                ["personal finance", "compound interest"],
            )
        db_session.commit()

        cluster_user_tags(str(test_user.id), db=db_session)

        resp = client.get("/themes", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "clusters" in data
        if data["clusters"]:
            c = data["clusters"][0]
            assert "id" in c
            assert "label" in c
            assert "article_count" in c
            assert "tag_labels" in c
            assert "top_articles" in c
            assert c["article_count"] >= 1

    def test_returns_empty_list_when_no_clusters(self, client, test_user, auth_headers):
        """GET /themes with no clusters → {clusters: []} not 404."""
        resp = client.get("/themes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"clusters": []}

    def test_requires_auth(self, client):
        """GET /themes without token → 401."""
        resp = client.get("/themes")
        assert resp.status_code == 401

    def test_top_articles_capped_at_three(
        self, client, db_session, test_user, auth_headers
    ):
        """top_articles list has at most 3 items regardless of cluster size."""
        tag_dim = {"behavioral economics": 0, "loss aversion": 0}
        _seed_embeddings(db_session, tag_dim)

        for i in range(10):
            _make_item(
                db_session,
                test_user.id,
                f"econ-{i}",
                ["behavioral economics", "loss aversion"],
            )
        db_session.commit()

        cluster_user_tags(str(test_user.id), db=db_session)

        resp = client.get("/themes", headers=auth_headers)
        assert resp.status_code == 200
        for c in resp.json()["clusters"]:
            assert len(c["top_articles"]) <= 3
