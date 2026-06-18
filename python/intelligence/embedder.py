"""
intelligence/embedder.py
─────────────────────────
Semantic embedding service using sentence-transformers.

Wraps the all-MiniLM-L6-v2 model (384-dim, ~80 MB) for generating
embeddings used by the signal correlation engine and semantic search.

The model is loaded eagerly at startup via run_in_executor so the first
request doesn't block.  All embedding calls are synchronous (CPU-bound)
and should be dispatched to a thread pool from async code.
"""

import logging
from typing import List, Optional

from config.settings import settings

logger = logging.getLogger("vision_i.intelligence.embedder")


class EmbeddingService:
    """Generates 384-dimensional semantic embeddings for text."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self._model_name = model_name or settings.embedding_model
        self._model = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """
        Eagerly load the sentence-transformer model.
        Called at startup via ``run_in_executor`` so the event loop isn't blocked.
        """
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            logger.info("Embedding model loaded: %s", self._model_name)
        except Exception as exc:
            logger.warning("Failed to load embedding model: %s", exc)
            self._model = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Batch-embed a list of texts.

        Returns a list of 384-dim float vectors.  If the model isn't
        loaded, returns zero vectors so callers degrade gracefully.
        """
        if not texts:
            return []
        if not self._model:
            logger.warning("Embedding model not loaded — returning zero vectors")
            return [[0.0] * settings.embedding_dim for _ in texts]
        return self._model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).tolist()

    def embed_single(self, text: str) -> List[float]:
        """Embed a single text string."""
        return self.embed_texts([text])[0]
