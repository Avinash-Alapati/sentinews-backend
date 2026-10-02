"""
Domain service for Full Coverage clustering of related news articles.

Groups near-duplicate articles (representing the same underlying event reported
by different publishers) using semantic embedding cosine similarity or headline token overlap.
"""

from typing import List, Optional, Set
import numpy as np

from app.infrastructure.observability.decorators import track_compute
from app.modules.news_intelligence.domain.entities import (
    FullCoverageCluster,
    NewsArticle,
    RelatedSourceLink,
)

DEFAULT_CLUSTER_SIMILARITY_THRESHOLD = 0.85
DEFAULT_LEXICAL_JACCARD_THRESHOLD = 0.50


def compute_cosine_similarity(vec1: Optional[List[float]], vec2: Optional[List[float]]) -> float:
    """Computes cosine similarity between two float vectors."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    v1 = np.array(vec1, dtype=np.float32)
    v2 = np.array(vec2, dtype=np.float32)

    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    sim = float(np.dot(v1, v2) / (norm1 * norm2))
    return max(0.0, min(1.0, sim))


@track_compute("news_clustering")
def cluster_articles(
    articles: List[NewsArticle],
    similarity_threshold: float = DEFAULT_CLUSTER_SIMILARITY_THRESHOLD,
    lexical_threshold: float = DEFAULT_LEXICAL_JACCARD_THRESHOLD,
) -> List[FullCoverageCluster]:
    """
    Clusters a list of articles into Full Coverage groups.

    Args:
        articles: List of articles (ordered by recency or importance).
        similarity_threshold: Cosine similarity threshold for embedding clustering (default 0.85).
        lexical_threshold: Jaccard similarity threshold for fallback title token matching (default 0.50).

    Returns:
        List[FullCoverageCluster]: List of clustered items.
    """
    if not articles:
        return []

    clustered_indices: Set[int] = set()
    clusters: List[FullCoverageCluster] = []

    for i, primary in enumerate(articles):
        if i in clustered_indices:
            continue

        clustered_indices.add(i)
        related_links: List[RelatedSourceLink] = []

        for j in range(i + 1, len(articles)):
            if j in clustered_indices:
                continue

            candidate = articles[j]
            is_match = False

            # Check embedding similarity if embeddings are present
            if primary.embedding and candidate.embedding:
                sim = compute_cosine_similarity(primary.embedding, candidate.embedding)
                is_match = (sim >= similarity_threshold)
            else:
                # Fallback: title token overlap / lexical Jaccard match
                primary_tokens = set(primary.title.lower().split())
                candidate_tokens = set(candidate.title.lower().split())
                if primary_tokens and candidate_tokens:
                    jaccard = len(primary_tokens & candidate_tokens) / len(primary_tokens | candidate_tokens)
                    is_match = (jaccard >= lexical_threshold)

            if is_match:
                clustered_indices.add(j)
                related_links.append(
                    RelatedSourceLink(
                        article_id=candidate.id,
                        title=candidate.title,
                        source=candidate.source,
                        url=candidate.url,
                        published_at=candidate.published_at,
                    )
                )

        cluster = FullCoverageCluster(
            primary_article=primary,
            related_sources=related_links,
            cluster_size=1 + len(related_links),
        )
        clusters.append(cluster)

    return clusters
