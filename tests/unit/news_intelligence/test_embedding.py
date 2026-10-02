"""
Unit tests for SentenceTransformerEmbeddingProvider and similarity calculations.
"""

import pytest
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)


def test_compute_similarity_identical_vectors():
    provider = SentenceTransformerEmbeddingProvider()
    vec = [0.5, 0.5, 0.5, 0.5]
    similarity = provider.compute_similarity(vec, vec)
    assert pytest.approx(similarity, rel=1e-5) == 1.0


def test_compute_similarity_orthogonal_vectors():
    provider = SentenceTransformerEmbeddingProvider()
    vec1 = [1.0, 0.0, 0.0]
    vec2 = [0.0, 1.0, 0.0]
    similarity = provider.compute_similarity(vec1, vec2)
    assert pytest.approx(similarity, abs=1e-6) == 0.0


def test_compute_similarity_empty_vectors():
    provider = SentenceTransformerEmbeddingProvider()
    assert provider.compute_similarity([], []) == 0.0
    assert provider.compute_similarity([1.0], [1.0, 2.0]) == 0.0
