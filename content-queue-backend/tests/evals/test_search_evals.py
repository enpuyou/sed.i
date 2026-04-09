"""
Search quality evals.

These are NOT unit tests — they measure search quality metrics.
Run with: pytest tests/evals/test_search_evals.py -v -s
(the -s flag prints the eval report to stdout)

Requires:
- Test database with pgvector
- OpenAI API key in env (for semantic/hybrid evals)
- Embeddings generated for eval articles (done in fixture)
"""

import pytest
import time
from collections import defaultdict
from app.core.search_router import classify_query
from app.core.hybrid_search import (
    hybrid_search,
    keyword_search,
    get_user_search_context,
)
from app.models.content import ContentItem
from .search_eval_dataset import EVAL_ARTICLES, EVAL_QUERIES


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def eval_articles(db_module, user_module):
    """Seed all eval articles into the DB. Returns key->article mapping."""
    articles = {}
    for spec in EVAL_ARTICLES:
        item = ContentItem(
            original_url=spec["original_url"],
            title=spec["title"],
            author=spec["author"],
            description=spec["description"],
            tags=spec["tags"],
            full_text=spec["full_text"],
            user_id=user_module.id,
            processing_status="completed",
        )
        db_module.add(item)
        db_module.commit()
        db_module.refresh(item)
        articles[spec["key"]] = item
    return articles


@pytest.fixture(scope="module")
def eval_articles_with_embeddings(eval_articles, db_module):
    """
    Generate embeddings for all eval articles.
    Requires OPENAI_API_KEY in env. If not set, skip semantic evals.
    """
    import os

    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — skipping embedding-dependent evals")

    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    for key, article in eval_articles.items():
        text = f"{article.title}\n\n{article.description}\n\n{article.full_text}"
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
            encoding_format="float",
        )
        article.embedding = response.data[0].embedding
    db_module.commit()
    return eval_articles


@pytest.fixture(scope="module")
def user_context(db_module, user_module, eval_articles):
    """Load user's authors and tags for the classifier."""
    return get_user_search_context(user=user_module, db=db_module)


# ─────────────────────────────────────────────────────────────
# Eval 1: Classification Accuracy
# ─────────────────────────────────────────────────────────────


class TestClassificationAccuracy:
    """
    Measures whether the query classifier routes queries correctly.
    This eval does NOT need OpenAI — it's pure heuristic logic.
    """

    def test_classification_report(self, user_context, eval_articles, db_module):
        print("DEBUG COUNT:", db_module.query(ContentItem).count())
        user_authors, user_tags = user_context
        correct = 0
        total = 0
        failures = []

        for eq in EVAL_QUERIES:
            result, _ = classify_query(
                eq["query"],
                user_authors=user_authors,
                user_tags=user_tags,
            )
            total += 1
            if result == eq["expected_intent"]:
                correct += 1
            else:
                failures.append(
                    f"  MISS: '{eq['query']}' → got '{result}', expected '{eq['expected_intent']}'"
                )

        accuracy = correct / total if total else 0
        report = [
            "",
            "=" * 60,
            "EVAL: Query Classification Accuracy",
            "=" * 60,
            f"Total queries:  {total}",
            f"Correct:        {correct}",
            f"Accuracy:       {accuracy:.1%}",
        ]
        if failures:
            report.append(f"Failures ({len(failures)}):")
            report.extend(failures)
        report.append("=" * 60)
        print("\n".join(report))

        # Threshold: classifier should get at least 80% right
        assert accuracy >= 0.80, (
            f"Classification accuracy {accuracy:.1%} below 80% threshold.\n"
            + "\n".join(failures)
        )


# ─────────────────────────────────────────────────────────────
# Eval 2: Keyword Search Quality (no OpenAI needed)
# ─────────────────────────────────────────────────────────────


class TestKeywordSearchQuality:
    """
    Measures hit rate and MRR for keyword-classified queries.
    Runs entirely on PostgreSQL tsvector — no API calls.
    """

    def test_keyword_eval_report(self, db_module, user_module, eval_articles):
        keyword_queries = [
            eq for eq in EVAL_QUERIES if eq["category"].startswith("keyword")
        ]
        if not keyword_queries:
            pytest.skip("No keyword eval queries")

        hits = 0
        reciprocal_ranks = []
        latencies = []

        article_map = {
            spec["key"]: eval_articles[spec["key"]] for spec in EVAL_ARTICLES
        }

        for eq in keyword_queries:
            expected_ids = {
                str(article_map[k].id)
                for k in eq.get("expected_article_keys", [])
                if k in article_map
            }

            start = time.perf_counter()
            results = keyword_search(
                query=eq["query"], user=user_module, db=db_module, limit=10
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

            result_ids = [r["id"] for r in results]

            # Hit rate: any expected article in results?
            if expected_ids and expected_ids & set(result_ids):
                hits += 1

            # MRR: rank of first expected article
            top1_key = eq.get("expected_top1_key")
            if top1_key and top1_key in article_map:
                target_id = str(article_map[top1_key].id)
                if target_id in result_ids:
                    rank = result_ids.index(target_id) + 1
                    reciprocal_ranks.append(1.0 / rank)
                else:
                    reciprocal_ranks.append(0.0)

        hit_rate = hits / len(keyword_queries) if keyword_queries else 0
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

        report = [
            "",
            "=" * 60,
            "EVAL: Keyword Search Quality",
            "=" * 60,
            f"Queries:        {len(keyword_queries)}",
            f"Hit Rate @10:   {hit_rate:.1%}",
            f"MRR:            {mrr:.3f}",
            f"Latency p50:    {p50:.1f}ms",
            f"Latency p95:    {p95:.1f}ms",
            "=" * 60,
        ]
        print("\n".join(report))

        assert hit_rate >= 0.70, f"Keyword hit rate {hit_rate:.1%} below 70%"


# ─────────────────────────────────────────────────────────────
# Eval 3: Hybrid Search Quality (needs OpenAI)
# ─────────────────────────────────────────────────────────────


class TestHybridSearchQuality:
    """
    Full end-to-end eval across ALL query types through hybrid_search().
    Compares hybrid results against expected results.
    Requires OpenAI API key for semantic/hybrid queries.
    """

    def test_hybrid_eval_report(
        self, db_module, user_module, eval_articles_with_embeddings, user_context
    ):
        user_authors, user_tags = user_context
        article_map = eval_articles_with_embeddings

        hits_by_category = defaultdict(lambda: {"hit": 0, "total": 0})
        reciprocal_ranks = []
        latencies = []
        api_calls_avoided = 0

        for eq in EVAL_QUERIES:
            expected_ids = {
                str(article_map[k].id)
                for k in eq.get("expected_article_keys", [])
                if k in article_map
            }
            category = eq["category"]

            start = time.perf_counter()
            results = hybrid_search(
                query=eq["query"],
                user=user_module,
                db=db_module,
                limit=10,
                user_authors=user_authors,
                user_tags=user_tags,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(
                {"query": eq["query"], "ms": elapsed_ms, "category": category}
            )

            result_ids = [r["id"] for r in results]

            # Track API calls avoided (filter/keyword don't call OpenAI)
            intent, _ = classify_query(
                eq["query"], user_authors=user_authors, user_tags=user_tags
            )
            if intent in ("filter", "keyword"):
                api_calls_avoided += 1

            # Hit rate per category
            hits_by_category[category]["total"] += 1
            if expected_ids and expected_ids & set(result_ids):
                hits_by_category[category]["hit"] += 1

            # MRR
            top1_key = eq.get("expected_top1_key")
            if top1_key and top1_key in article_map:
                target_id = str(article_map[top1_key].id)
                if target_id in result_ids:
                    rank = result_ids.index(target_id) + 1
                    reciprocal_ranks.append(1.0 / rank)
                else:
                    reciprocal_ranks.append(0.0)

        # ── Compute aggregates ──
        total_queries = len(EVAL_QUERIES)
        total_hits = sum(c["hit"] for c in hits_by_category.values())
        total_expected = sum(c["total"] for c in hits_by_category.values())
        overall_hit_rate = total_hits / total_expected if total_expected else 0
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0

        lat_values = sorted(latency["ms"] for latency in latencies)
        p50 = lat_values[len(lat_values) // 2] if lat_values else 0
        p95 = lat_values[int(len(lat_values) * 0.95)] if lat_values else 0

        # ── Print report ──
        report = [
            "",
            "=" * 70,
            "EVAL: Hybrid Search Quality (Full Pipeline)",
            "=" * 70,
            f"Total queries:       {total_queries}",
            f"Overall Hit Rate:    {overall_hit_rate:.1%}",
            f"MRR:                 {mrr:.3f}",
            f"API Calls Avoided:   {api_calls_avoided}/{total_queries} ({api_calls_avoided/total_queries:.0%})",
            f"Latency p50:         {p50:.1f}ms",
            f"Latency p95:         {p95:.1f}ms",
            "",
            "Per-category breakdown:",
        ]
        for cat in sorted(hits_by_category.keys()):
            c = hits_by_category[cat]
            cat_rate = c["hit"] / c["total"] if c["total"] else 0
            cat_latencies = [
                latency["ms"] for latency in latencies if latency["category"] == cat
            ]
            cat_avg = sum(cat_latencies) / len(cat_latencies) if cat_latencies else 0
            report.append(
                f"  - {cat}: {cat_rate:.1%} hit rate "
                f"({c['hit']}/{c['total']}), avg latency {cat_avg:.1f}ms"
            )
        report.append("=" * 70)
        print("\n".join(report))

        # ── Assertions ──
        assert (
            overall_hit_rate >= 0.75
        ), f"Overall hit rate {overall_hit_rate:.1%} below 75%"
        assert mrr >= 0.60, f"MRR {mrr:.3f} below 0.60"
        assert (
            api_calls_avoided >= 5
        ), f"Expected at least 5 queries to avoid API calls, got {api_calls_avoided}"


# ─────────────────────────────────────────────────────────────
# Eval 4: Before/After Comparison (the real eval)
# ─────────────────────────────────────────────────────────────


class TestBeforeAfterComparison:
    """
    Runs the SAME queries through the OLD search path (pure semantic)
    and the NEW search path (hybrid), and compares metrics.

    This is the eval that proves the feature is an improvement.
    """

    def test_comparison_report(
        self, db_module, user_module, eval_articles_with_embeddings, user_context
    ):
        user_authors, user_tags = user_context
        article_map = eval_articles_with_embeddings

        def run_old_search(query: str) -> list[dict]:
            """Simulate old behavior: always semantic, call OpenAI for every query."""
            from app.mcp.tools.content import search_content

            try:
                return search_content(
                    query=query, user=user_module, db=db_module, limit=10
                )
            except Exception:
                return []

        def run_new_search(query: str) -> list[dict]:
            """New behavior: hybrid routing."""
            return hybrid_search(
                query=query,
                user=user_module,
                db=db_module,
                limit=10,
                user_authors=user_authors,
                user_tags=user_tags,
            )

        old_metrics = {"hits": 0, "rr_sum": 0, "rr_count": 0, "latencies": []}
        new_metrics = {"hits": 0, "rr_sum": 0, "rr_count": 0, "latencies": []}

        queries_with_expected = [
            eq for eq in EVAL_QUERIES if eq.get("expected_article_keys")
        ]

        for eq in queries_with_expected:
            expected_ids = {
                str(article_map[k].id)
                for k in eq["expected_article_keys"]
                if k in article_map
            }
            top1_key = eq.get("expected_top1_key")
            target_id = (
                str(article_map[top1_key].id)
                if top1_key and top1_key in article_map
                else None
            )

            for label, search_fn, metrics in [
                ("old", run_old_search, old_metrics),
                ("new", run_new_search, new_metrics),
            ]:
                start = time.perf_counter()
                results = search_fn(eq["query"])
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics["latencies"].append(elapsed_ms)

                result_ids = [
                    r.get("id") or r.get("item", {}).get("id", "") for r in results
                ]

                if expected_ids & set(result_ids):
                    metrics["hits"] += 1

                if target_id:
                    metrics["rr_count"] += 1
                    if target_id in result_ids:
                        rank = result_ids.index(target_id) + 1
                        metrics["rr_sum"] += 1.0 / rank

        total = len(queries_with_expected)
        old_hr = old_metrics["hits"] / total if total else 0
        new_hr = new_metrics["hits"] / total if total else 0
        old_mrr = (
            old_metrics["rr_sum"] / old_metrics["rr_count"]
            if old_metrics["rr_count"]
            else 0
        )
        new_mrr = (
            new_metrics["rr_sum"] / new_metrics["rr_count"]
            if new_metrics["rr_count"]
            else 0
        )

        old_lat = sorted(old_metrics["latencies"])
        new_lat = sorted(new_metrics["latencies"])
        old_p50 = old_lat[len(old_lat) // 2] if old_lat else 0
        new_p50 = new_lat[len(new_lat) // 2] if new_lat else 0
        old_p95 = old_lat[int(len(old_lat) * 0.95)] if old_lat else 0
        new_p95 = new_lat[int(len(new_lat) * 0.95)] if new_lat else 0

        report = [
            "",
            "=" * 70,
            "EVAL: Before/After Comparison",
            "=" * 70,
            f"Queries evaluated:  {total}",
            "",
            f"{'Metric':<25s} {'OLD (semantic-only)':>20s} {'NEW (hybrid)':>20s} {'Delta':>10s}",
            f"{'-'*25:<25s} {'-'*20:>20s} {'-'*20:>20s} {'-'*10:>10s}",
            f"{'Hit Rate @10':<25s} {old_hr:>19.1%} {new_hr:>19.1%} {'+' if new_hr>=old_hr else ''}{(new_hr-old_hr)*100:>+8.1f}pp",
            f"{'MRR':<25s} {old_mrr:>20.3f} {new_mrr:>20.3f} {new_mrr-old_mrr:>+10.3f}",
            f"{'Latency p50':<25s} {old_p50:>18.0f}ms {new_p50:>18.0f}ms {new_p50-old_p50:>+8.0f}ms",
            f"{'Latency p95':<25s} {old_p95:>18.0f}ms {new_p95:>18.0f}ms {new_p95-old_p95:>+8.0f}ms",
            "",
        ]

        # Verdict
        improved = new_hr >= old_hr and new_mrr >= old_mrr
        if improved:
            report.append(
                "VERDICT: Hybrid search is EQUAL or BETTER on all quality metrics."
            )
        else:
            report.append(
                "VERDICT: Hybrid search REGRESSED on some metrics. Investigate."
            )

        if old_p50 > 0 and new_p50 < old_p50 * 0.5:
            report.append(
                f"BONUS: p50 latency improved by {(1 - new_p50/old_p50)*100:.0f}%."
            )

        report.append("=" * 70)
        print("\n".join(report))

        # The new system should not be worse
        assert (
            new_hr >= old_hr * 0.95
        ), f"Hybrid hit rate ({new_hr:.1%}) regressed vs semantic-only ({old_hr:.1%})"
