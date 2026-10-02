"""
Sentence Transformers Embedding Provider.

Satisfies the EmbeddingProvider Protocol port using a local model (all-MiniLM-L6-v2).
Operates entirely offline without API keys or vendor subscriptions.

Note: The model weights (~80MB) are downloaded on first initialization as a
one-time network fetch and cached locally in HuggingFace cache directory. Subsequent
instantiations run 100% locally from the local disk cache.
"""

import logging
from typing import List, Optional
import numpy as np

import hashlib
import logging
import re
from typing import List, Optional
import numpy as np

logger = logging.getLogger("sentinews.news_intelligence.embedding")


def _compute_fallback_embedding(text: str, dim: int = 384) -> List[float]:
    """
    Computes a deterministic, unit-normalized dense embedding vector from text
    using word n-gram hashing. Provides instant zero-network fallback.
    """
    if not text or not text.strip():
        return [0.0] * dim

    vec = np.zeros(dim, dtype=np.float32)
    words = re.findall(r"\w+", text.lower())
    if not words:
        words = [text.strip().lower()]

    for word in words:
        h = int(hashlib.sha256(word.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16)
        idx = h % dim
        sign = 1.0 if (h % 2 == 0) else -1.0
        vec[idx] += sign

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


_shared_models: dict = {}
_shared_failed_models: set = set()


class SentenceTransformerEmbeddingProvider:
    """
    Local SentenceTransformer embedding provider for fast semantic similarity computation.
    Falls back gracefully to deterministic n-gram hash embeddings if weights are unavailable.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name

    def _get_model(self):
        """
        Lazy-loads and caches the SentenceTransformer model across all provider instances.
        """
        if self.model_name in _shared_failed_models:
            return None

        if self.model_name not in _shared_models:
            import os
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading SentenceTransformer model: %s", self.model_name)
                is_offline = (
                    os.environ.get("HF_HUB_OFFLINE") == "1"
                    or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
                    or os.environ.get("ENVIRONMENT") == "test"
                )
                if is_offline:
                    _shared_models[self.model_name] = SentenceTransformer(self.model_name, local_files_only=True)
                else:
                    try:
                        # Try loading from local HuggingFace cache first without network roundtrips
                        _shared_models[self.model_name] = SentenceTransformer(self.model_name, local_files_only=True)
                    except Exception:
                        _shared_models[self.model_name] = SentenceTransformer(self.model_name)
            except Exception as exc:
                logger.warning(
                    "Could not load SentenceTransformer model '%s' (%s). "
                    "Using deterministic fallback embedding engine.",
                    self.model_name,
                    str(exc),
                )
                _shared_failed_models.add(self.model_name)
                return None

        return _shared_models.get(self.model_name)


    def embed_text(self, text: str) -> List[float]:
        """
        Generates a 384-dimensional dense vector embedding for input text.
        """
        if not text or not text.strip():
            return [0.0] * 384

        try:
            model = self._get_model()
            if model is not None:
                embedding = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
                return embedding.tolist()
        except Exception as exc:
            logger.warning("SentenceTransformer encode error: %s. Using fallback vector.", exc)

        return _compute_fallback_embedding(text, dim=384)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generates normalized vector embeddings for a batch of texts.
        """
        if not texts:
            return []

        clean_texts = [t if (t and t.strip()) else " " for t in texts]
        try:
            model = self._get_model()
            if model is not None:
                embeddings = model.encode(
                    clean_texts,
                    batch_size=32,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                return embeddings.tolist()
        except Exception as exc:
            logger.warning("SentenceTransformer encode batch error: %s. Using fallback vector.", exc)

        return [_compute_fallback_embedding(t, dim=384) for t in clean_texts]

    def compute_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Computes cosine similarity between two unit-normalized embedding vectors.
        For unit vectors, dot product equals cosine similarity.
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0

        v1 = np.array(vec1, dtype=np.float32)
        v2 = np.array(vec2, dtype=np.float32)

        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)

        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0

        similarity = float(np.dot(v1, v2) / (norm1 * norm2))
        return max(0.0, min(1.0, similarity))
