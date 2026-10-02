"""
Unit tests for Full Coverage Clustering domain service.
"""

from datetime import datetime, timezone
import pytest
import numpy as np

from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.clustering import (
    cluster_articles,
    compute_cosine_similarity,
)


def test_compute_cosine_similarity():
    """Identical vectors have similarity 1.0, orthogonal vectors have 0.0."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]

    assert pytest.approx(compute_cosine_similarity(v1, v2), 0.001) == 1.0
    assert pytest.approx(compute_cosine_similarity(v1, v3), 0.001) == 0.0


def test_cluster_articles_near_duplicates():
    """Articles with high embedding similarity (>= 0.85) cluster into one FullCoverage item."""
    now = datetime.now(timezone.utc)

    # Base vector
    vec_base = [0.5, 0.5, 0.5, 0.5]
    # Very similar vector (sim > 0.95)
    vec_similar = [0.51, 0.49, 0.50, 0.50]
    # Orthogonal/different event vector
    vec_different = [0.0, 0.0, 1.0, 0.0]

    art1 = NewsArticle(
        id=1,
        title="Reliance acquires renewable energy startup for 500 crores",
        summary="Reliance Industries expands green footprint.",
        url="https://moneycontrol.com/rel1",
        source="Moneycontrol",
        published_at=now,
        embedding=vec_base,
    )
    art2 = NewsArticle(
        id=2,
        title="RIL buys clean tech firm in 500 Cr deal",
        summary="Mukesh Ambani led Reliance boosts solar portfolio.",
        url="https://economictimes.com/rel2",
        source="Economic Times",
        published_at=now,
        embedding=vec_similar,
    )
    art3 = NewsArticle(
        id=3,
        title="TCS announces $2 Billion deal in North America",
        summary="TCS signs multi-year digital transformation contract.",
        url="https://livemint.com/tcs3",
        source="LiveMint",
        published_at=now,
        embedding=vec_different,
    )

    clusters = cluster_articles([art1, art2, art3], similarity_threshold=0.85)

    # Expected: 2 clusters (Reliance articles clustered together, TCS article separate)
    assert len(clusters) == 2

    rel_cluster = next((c for c in clusters if c.primary_article.id == 1), None)
    assert rel_cluster is not None
    assert rel_cluster.cluster_size == 2
    assert len(rel_cluster.related_sources) == 1
    assert rel_cluster.related_sources[0].article_id == 2
    assert rel_cluster.related_sources[0].source == "Economic Times"

    tcs_cluster = next((c for c in clusters if c.primary_article.id == 3), None)
    assert tcs_cluster is not None
    assert tcs_cluster.cluster_size == 1
    assert len(tcs_cluster.related_sources) == 0
